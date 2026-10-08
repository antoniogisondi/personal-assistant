"""Google OAuth 2.0 (authorization code + PKCE) with encrypted token storage and auto-refresh."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from pydantic import SecretStr

from gsoi_assistant.core.errors import BadRequestError, ConnectorNotConnectedError, ToolError
from gsoi_assistant.db.stores import OAuthStore, utcnow
from gsoi_assistant.security.crypto import TokenCipher

log = structlog.get_logger(__name__)

PROVIDER = "google"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  # nosec B105
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"  # drafts + sending
CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
DEFAULT_SCOPES = (GMAIL_READONLY, GMAIL_COMPOSE, CALENDAR_READONLY, CALENDAR_EVENTS)

NOT_CONNECTED = (
    "Google is not connected. Ask the user to connect it (POST /v1/connections/google/start)."
)
_SKEW_S = 60
_STATE_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class ConnectionStatus:
    connected: bool
    status: str  # "active" | "needs_reauth" | "not_connected"
    scopes: tuple[str, ...] = ()


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)  # 86 chars, within the 43-128 PKCE limit
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return verifier, challenge.decode()


class GoogleAuth:
    def __init__(
        self,
        *,
        store: OAuthStore,
        cipher: TokenCipher,
        client_id: str,
        client_secret: SecretStr,
        redirect_uri: str,
        scopes: tuple[str, ...] = DEFAULT_SCOPES,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scopes = scopes
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._locks: dict[str, asyncio.Lock] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- connect ----------------------------------------------------------

    async def start(self, user_id: str) -> str:
        """Return the Google consent URL. The pending state is stored server-side."""
        state = secrets.token_urlsafe(32)
        verifier, challenge = _pkce_pair()
        await self._store.save_state(
            state=state,
            user_id=user_id,
            provider=PROVIDER,
            code_verifier=verifier,
            expires_at=utcnow() + _STATE_TTL,
        )
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": " ".join(self._scopes),
                "access_type": "offline",  # we need a refresh token
                "prompt": "consent",
                "include_granted_scopes": "true",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{AUTH_URL}?{query}"

    async def complete(self, state: str, code: str) -> str:
        """Finish the flow from the redirect. Returns the user_id the state was issued to."""
        pending = await self._store.pop_state(state, PROVIDER)
        if pending is None:
            raise BadRequestError("unknown or expired authorization; start the connection again")
        data = await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
                "code_verifier": pending.code_verifier,
            }
        )
        existing = await self._store.get_credential(pending.user_id, PROVIDER)
        refresh = data.get("refresh_token")
        if not refresh and existing is not None:
            refresh = self._cipher.decrypt_json(existing.token_enc).get("refresh_token")
        if not refresh:
            raise BadRequestError(
                "Google did not return a refresh token: remove this app's access at "
                "myaccount.google.com/permissions and connect again"
            )
        granted = tuple(str(data.get("scope", "")).split()) or self._scopes
        blob = {
            "refresh_token": refresh,
            "access_token": data["access_token"],
            "expires_at": time.time() + float(data.get("expires_in", 3600)),
        }
        await self._store.upsert_credential(
            user_id=pending.user_id,
            provider=PROVIDER,
            scopes=list(granted),
            token_enc=self._cipher.encrypt_json(blob),
        )
        log.info("google_connected", scopes=len(granted))
        return pending.user_id

    # ---- use --------------------------------------------------------------

    async def access_token(self, user_id: str, *, force_refresh: bool = False) -> str:
        cred = await self._store.get_credential(user_id, PROVIDER)
        if cred is None:
            raise ConnectorNotConnectedError(NOT_CONNECTED)
        if cred.status != "active":
            raise ConnectorNotConnectedError(
                "The Google connection expired or was revoked. The user must connect it again."
            )
        blob = self._cipher.decrypt_json(cred.token_enc)
        if not force_refresh and float(blob["expires_at"]) - _SKEW_S > time.time():
            return str(blob["access_token"])

        async with self._locks.setdefault(user_id, asyncio.Lock()):  # one refresh at a time
            cred = await self._store.get_credential(user_id, PROVIDER)
            if cred is None:
                raise ConnectorNotConnectedError("Google is not connected.")
            blob = self._cipher.decrypt_json(cred.token_enc)
            if not force_refresh and float(blob["expires_at"]) - _SKEW_S > time.time():
                return str(blob["access_token"])  # someone else refreshed meanwhile
            try:
                data = await self._token_request(
                    {"grant_type": "refresh_token", "refresh_token": blob["refresh_token"]}
                )
            except _InvalidGrantError:
                await self._store.set_status(user_id, PROVIDER, "needs_reauth")
                raise ConnectorNotConnectedError(
                    "The Google connection expired or was revoked. The user must connect it again."
                ) from None
            blob.update(
                access_token=data["access_token"],
                expires_at=time.time() + float(data.get("expires_in", 3600)),
            )
            if data.get("refresh_token"):  # Google may rotate it
                blob["refresh_token"] = data["refresh_token"]
            await self._store.update_tokens(user_id, PROVIDER, self._cipher.encrypt_json(blob))
            return str(blob["access_token"])

    async def granted_scopes(self, user_id: str) -> set[str]:
        cred = await self._store.get_credential(user_id, PROVIDER)
        return set(cred.scopes) if cred else set()

    async def status(self, user_id: str) -> ConnectionStatus:
        cred = await self._store.get_credential(user_id, PROVIDER)
        if cred is None:
            return ConnectionStatus(False, "not_connected")
        return ConnectionStatus(cred.status == "active", cred.status, tuple(cred.scopes))

    async def disconnect(self, user_id: str) -> bool:
        cred = await self._store.get_credential(user_id, PROVIDER)
        if cred is None:
            return False
        try:  # best effort: revoke at Google so the grant really disappears
            token = self._cipher.decrypt_json(cred.token_enc).get("refresh_token", "")
            await self._http.post(REVOKE_URL, data={"token": token})
        except Exception:
            log.warning("google_revoke_failed")
        return await self._store.delete_credential(user_id, PROVIDER)

    # ---- internals --------------------------------------------------------

    async def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        body = {
            **form,
            "client_id": self._client_id,
            "client_secret": self._client_secret.get_secret_value(),
        }
        try:
            resp = await self._http.post(TOKEN_URL, data=body)
        except httpx.HTTPError as exc:
            raise ToolError("Google sign-in service is unreachable right now") from exc
        if resp.status_code == 400 and _error_code(resp) == "invalid_grant":
            raise _InvalidGrantError
        if resp.status_code >= 400:
            log.warning("google_token_error", status=resp.status_code, code=_error_code(resp))
            raise ToolError(f"Google rejected the sign-in request ({resp.status_code})")
        data: dict[str, Any] = resp.json()
        return data


class _InvalidGrantError(Exception):
    pass


def _error_code(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("error", ""))
    except ValueError:
        return ""

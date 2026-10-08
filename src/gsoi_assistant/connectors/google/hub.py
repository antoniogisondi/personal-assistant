"""GoogleHub: owns the (reconfigurable) Google OAuth application.

The OAuth client (id + secret) comes from, in order: the environment (operator-managed
deployments) or the encrypted database record saved from the /setup page. Changing it takes
effect immediately, without restarting the server.
"""

from __future__ import annotations

from typing import Literal

import httpx
from pydantic import SecretStr

from gsoi_assistant.connectors.google.auth import GoogleAuth
from gsoi_assistant.core.errors import BadRequestError, ConflictError, ConnectorNotConnectedError
from gsoi_assistant.db.stores import ConnectorConfigStore, OAuthStore
from gsoi_assistant.security.crypto import TokenCipher

PROVIDER = "google"
NOT_CONFIGURED = (
    "Google is not set up yet. Ask the user to open /setup and enter the Google application "
    "credentials, then connect their account."
)

Source = Literal["env", "app"]


class GoogleHub:
    def __init__(
        self,
        *,
        oauth_store: OAuthStore,
        config_store: ConnectorConfigStore,
        cipher: TokenCipher,
        redirect_uri: str,
        env_client_id: str | None = None,
        env_client_secret: SecretStr | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._oauth = oauth_store
        self._configs = config_store
        self._cipher = cipher
        self.redirect_uri = redirect_uri
        self._http = http
        self.auth: GoogleAuth | None = None
        self.source: Source | None = None
        self.client_id: str | None = None
        if env_client_id and env_client_secret is not None:
            self._build(env_client_id, env_client_secret)
            self.source = "env"

    def _build(self, client_id: str, client_secret: SecretStr) -> None:
        self.auth = GoogleAuth(
            store=self._oauth,
            cipher=self._cipher,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=self.redirect_uri,
            http=self._http,
        )
        self.client_id = client_id

    async def load(self) -> None:
        """Pick up the saved configuration (when the environment does not provide one)."""
        if self.source == "env":
            return
        row = await self._configs.get(PROVIDER)
        if row is None:
            return
        secret = self._cipher.decrypt_json(row.client_secret_enc)["client_secret"]
        self._build(row.client_id, SecretStr(str(secret)))
        self.source = "app"

    async def configure(self, client_id: str, client_secret: str) -> None:
        if self.source == "env":
            raise ConflictError(
                "Google is configured in the server environment; remove it there to manage it here"
            )
        client_id, client_secret = client_id.strip(), client_secret.strip()
        if not client_id.endswith(".apps.googleusercontent.com"):
            raise BadRequestError("the Client ID should end with '.apps.googleusercontent.com'")
        if len(client_secret) < 8:
            raise BadRequestError("the Client Secret looks too short")
        changed = self.client_id != client_id
        if self.auth is not None:
            await self.auth.aclose()
        await self._configs.save(
            PROVIDER, client_id, self._cipher.encrypt_json({"client_secret": client_secret})
        )
        if changed:  # tokens issued to a different OAuth client are useless
            await self._oauth.delete_all_credentials(PROVIDER)
        self._build(client_id, SecretStr(client_secret))
        self.source = "app"

    async def clear(self) -> None:
        if self.source == "env":
            raise ConflictError("Google is configured through the server environment")
        if self.auth is not None:
            await self.auth.aclose()
        await self._configs.delete(PROVIDER)
        await self._oauth.delete_all_credentials(PROVIDER)
        self.auth = None
        self.source = None
        self.client_id = None

    def require(self) -> GoogleAuth:
        if self.auth is None:
            raise ConnectorNotConnectedError(NOT_CONFIGURED)
        return self.auth

    async def aclose(self) -> None:
        if self.auth is not None:
            await self.auth.aclose()

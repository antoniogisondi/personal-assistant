from __future__ import annotations

from typing import Any

import httpx

from gsoi_assistant.connectors.google.auth import NOT_CONNECTED, GoogleAuth
from gsoi_assistant.core.errors import ConnectorNotConnectedError, ToolError


class GoogleApi:
    """Authenticated JSON calls to Google APIs. Errors become user-presentable ToolErrors with
    messages we author; raw upstream bodies are never forwarded to the model."""

    def __init__(self, auth: GoogleAuth, client: httpx.AsyncClient | None = None) -> None:
        self._auth = auth
        self._client = client or httpx.AsyncClient(timeout=20.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def require_scopes(self, user_id: str, scopes: tuple[str, ...]) -> None:
        granted = await self._auth.granted_scopes(user_id)
        if not granted:
            raise ConnectorNotConnectedError(NOT_CONNECTED)
        missing = [s.rsplit("/", 1)[-1] for s in scopes if s not in granted]
        if missing:
            raise ToolError(
                f"Google access is missing the permission(s) {', '.join(missing)}; "
                "the user must reconnect and grant them."
            )

    async def request(
        self,
        user_id: str,
        method: str,
        url: str,
        *,
        scopes: tuple[str, ...] = (),
        params: Any = None,  # dict or list of (key, value) pairs for repeated keys
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if scopes:
            await self.require_scopes(user_id, scopes)
        for attempt in (1, 2):
            token = await self._auth.access_token(user_id, force_refresh=attempt == 2)
            try:
                resp = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                raise ToolError("Google is unreachable right now") from exc
            if resp.status_code == 401 and attempt == 1:
                continue  # token revoked/expired early: refresh once and retry
            return self._parse(resp)
        raise ToolError("Google rejected the credentials")  # pragma: no cover

    @staticmethod
    def _parse(resp: httpx.Response) -> dict[str, Any]:
        code = resp.status_code
        if code == 204:
            return {}
        if code < 300:
            data: dict[str, Any] = resp.json()
            return data
        if code == 401:
            raise ConnectorNotConnectedError(
                "Google rejected the credentials; the user must reconnect."
            )
        if code == 403:
            raise ToolError(
                "Google denied access (permission or quota); the user may need to reconnect."
            )
        if code == 404:
            raise ToolError("Google could not find that item.")
        if code == 429 or code >= 500:
            raise ToolError("Google is temporarily unavailable; try again shortly.")
        raise ToolError(f"Google rejected the request ({code}).")

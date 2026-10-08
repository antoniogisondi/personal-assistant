"""Thin synchronous client for the embedded backend's HTTP API."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class ApiClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 180.0) -> None:
        self._http = httpx.Client(
            base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout
        )

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        try:
            resp = self._http.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise ApiError(
                0, "Non riesco a contattare il servizio interno dell'assistente."
            ) from exc
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, _message(resp))
        return resp.json() if resp.content else None

    def health(self) -> bool:
        try:
            return bool(self._http.get("/health", timeout=3).status_code == 200)
        except httpx.HTTPError:
            return False

    def chat(
        self, message: str, conversation_id: str | None, channel: str = "text"
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"message": message, "channel": channel}
        if conversation_id:
            body["conversation_id"] = conversation_id
        return self._call("POST", "/v1/chat", body)  # type: ignore[no-any-return]

    def chat_stream(
        self,
        message: str,
        conversation_id: str | None,
        channel: str,
        on_event: Callable[[str, dict[str, Any]], None],
    ) -> None:
        """Run a chat turn and report each server-sent event as it arrives."""
        body: dict[str, Any] = {"message": message, "channel": channel}
        if conversation_id:
            body["conversation_id"] = conversation_id
        try:
            with self._http.stream("POST", "/v1/chat/stream", json=body) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise ApiError(resp.status_code, _message(resp))
                name = "message"
                for line in resp.iter_lines():
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:"):
                        on_event(name, json.loads(line[5:].strip()))
        except httpx.HTTPError as exc:
            raise ApiError(
                0, "Non riesco a contattare il servizio interno dell'assistente."
            ) from exc

    def briefing(self, channel: str = "voice") -> dict[str, Any]:
        return self._call("POST", "/v1/briefing", {"channel": channel})  # type: ignore[no-any-return]

    def decide(self, approval_id: str, approve: bool, confirm_tool: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"approve": approve}
        if confirm_tool:
            body["confirm_tool"] = confirm_tool
        return self._call("POST", f"/v1/approvals/{approval_id}/decision", body)  # type: ignore[no-any-return]

    def pending_approvals(self) -> list[dict[str, Any]]:
        return self._call("GET", "/v1/approvals")  # type: ignore[no-any-return]

    def connections(self) -> list[dict[str, Any]]:
        return self._call("GET", "/v1/connections")  # type: ignore[no-any-return]

    def google_start(self) -> str:
        return str(self._call("POST", "/v1/connections/google/start")["auth_url"])

    def google_disconnect(self) -> None:
        self._call("DELETE", "/v1/connections/google")


def _message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return f"Errore {resp.status_code}"
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, str):
        return detail
    return f"Errore {resp.status_code}"

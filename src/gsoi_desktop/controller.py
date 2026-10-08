"""Application logic of the desktop app, independent of Qt (so it is easy to test)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class Api(Protocol):
    def chat(
        self, message: str, conversation_id: str | None, channel: str = ...
    ) -> dict[str, Any]: ...
    def briefing(self, channel: str = ...) -> dict[str, Any]: ...
    def decide(
        self, approval_id: str, approve: bool, confirm_tool: str | None
    ) -> dict[str, Any]: ...
    def connections(self) -> list[dict[str, Any]]: ...
    def google_start(self) -> str: ...
    def google_disconnect(self) -> None: ...


@dataclass(frozen=True)
class Approval:
    approval_id: str
    tool: str
    risk: str
    strong: bool
    summary: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Turn:
    """What the user should see after one step: an answer, or a request for their approval."""

    text: str = ""
    approval: Approval | None = None
    cost_usd: float = 0.0


@dataclass(frozen=True)
class GoogleState:
    available: bool  # the Google application is configured in this installation
    connected: bool
    needs_reconnect: bool = False
    scopes: tuple[str, ...] = field(default_factory=tuple)


def _turn(data: dict[str, Any]) -> Turn:
    raw = data.get("approval")
    approval = None
    if raw:
        display = raw.get("display", {})
        approval = Approval(
            approval_id=raw["approval_id"],
            tool=raw["tool"],
            risk=raw["risk"],
            strong=raw.get("strength") == "strong",
            summary=str(display.get("summary", raw["tool"])),
            arguments=dict(display.get("arguments", {})),
        )
    return Turn(
        text=data.get("content", ""), approval=approval, cost_usd=float(data.get("cost_usd", 0))
    )


class AssistantController:
    def __init__(self, api: Api) -> None:
        self._api = api
        self.conversation_id: str | None = None

    def use_api(self, api: Api) -> None:
        """Point at a new backend (it listens on a new port after the settings are applied)."""
        self._api = api
        self.conversation_id = None

    def new_conversation(self) -> None:
        self.conversation_id = None

    def send(self, text: str) -> Turn:
        data = self._api.chat(text, self.conversation_id)
        self.conversation_id = data["conversation_id"]
        return _turn(data)

    def briefing(self) -> Turn:
        data = self._api.briefing()
        self.conversation_id = data["conversation_id"]
        return _turn(data)

    def decide(self, approval: Approval, approve: bool) -> Turn:
        """Answer an approval request. Destructive actions are confirmed by naming the tool."""
        confirm = approval.tool if (approve and approval.strong) else None
        return _turn(self._api.decide(approval.approval_id, approve, confirm))

    def google_state(self) -> GoogleState:
        for c in self._api.connections():
            if c["provider"] == "google":
                return GoogleState(
                    available=bool(c["configured"]),
                    connected=bool(c["connected"]),
                    needs_reconnect=c.get("status") == "needs_reauth",
                    scopes=tuple(c.get("scopes", [])),
                )
        return GoogleState(available=False, connected=False)

    def google_connect_url(self) -> str:
        return self._api.google_start()

    def google_disconnect(self) -> None:
        self._api.google_disconnect()

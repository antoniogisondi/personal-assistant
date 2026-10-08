"""Application logic of the desktop app, independent of Qt (so it is easy to test)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from gsoi_desktop.client import ApiError
from gsoi_desktop.sentences import SentenceSplitter


class Api(Protocol):
    def chat(
        self, message: str, conversation_id: str | None, channel: str = ...
    ) -> dict[str, Any]: ...
    def chat_stream(
        self,
        message: str,
        conversation_id: str | None,
        channel: str,
        on_event: Callable[[str, dict[str, Any]], None],
    ) -> None: ...
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

    def send(self, text: str, channel: str = "text") -> Turn:
        data = self._api.chat(text, self.conversation_id, channel)
        self.conversation_id = data["conversation_id"]
        return _turn(data)

    def send_streaming(self, text: str, channel: str, on_sentence: Callable[[str], None]) -> Turn:
        """Like `send`, but hands every sentence of the answer over as soon as it is complete."""
        splitter = SentenceSplitter()
        result: dict[str, Any] = {}
        got_tokens = False

        def emit(parts: list[str]) -> None:
            for part in parts:
                on_sentence(part)

        def on_event(name: str, data: dict[str, Any]) -> None:
            nonlocal got_tokens
            if name == "run_started":
                self.conversation_id = str(data["conversation_id"])
            elif name == "token":
                got_tokens = True
                emit(splitter.feed(data.get("delta", "")))
            elif name == "tool_call_started":
                emit(splitter.flush())  # "un attimo, controllo": say it before the tool runs
                got_tokens = False
            elif name == "final":
                if not got_tokens:  # e.g. a fast command: the answer arrives whole
                    emit(splitter.feed(data.get("content", "")))
                emit(splitter.flush())
                result.update(data)
            elif name == "approval_required":
                emit(splitter.flush())
                result["approval"] = data
            elif name == "error":
                raise ApiError(0, str(data.get("message", "Errore")))

        self._api.chat_stream(text, self.conversation_id, channel, on_event)
        if not result:
            raise ApiError(0, "Nessuna risposta dal servizio interno.")
        return _turn(result)

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

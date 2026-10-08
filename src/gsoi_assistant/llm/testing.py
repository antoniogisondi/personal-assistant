"""Deterministic provider for tests and offline development. Never used in production wiring."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from gsoi_assistant.llm.base import (
    Capabilities,
    ChatRequest,
    ChatResponse,
    Message,
    StreamChunk,
    ToolCall,
    Usage,
)


@dataclass
class ToolUse:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str | None = None


@dataclass
class Turn:
    """One scripted model turn: optional text plus tool calls."""

    text: str = ""
    calls: list[ToolUse] = field(default_factory=list)


def use(name: str, **arguments: Any) -> Turn:
    """Shorthand: a turn in which the model calls one tool (by dotted or wire name)."""
    return Turn(calls=[ToolUse(name, arguments)])


Item = str | Exception | Turn
Script = Callable[[ChatRequest], Item]


class ScriptedProvider:
    def __init__(
        self,
        script: Script | list[Item] | None = None,
        *,
        name: str = "scripted",
        model: str = "scripted-1",
        capabilities: Capabilities | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.capabilities = capabilities or Capabilities()
        self.requests: list[ChatRequest] = []
        self._queue: list[Item] = list(script) if isinstance(script, list) else []
        self._fn: Script | None = script if callable(script) else None
        self._n = 0

    def _next(self, req: ChatRequest) -> Item:
        self.requests.append(req)
        if self._fn is not None:
            return self._fn(req)
        if not self._queue:
            return "ok"
        return self._queue.pop(0)

    def _calls(self, turn: Turn) -> list[ToolCall]:
        out = []
        for c in turn.calls:
            self._n += 1
            out.append(
                ToolCall(
                    id=c.id or f"call_{self._n}",
                    name=c.name.replace(".", "__"),
                    arguments=c.arguments,
                )
            )
        return out

    @staticmethod
    def _usage(req: ChatRequest, text: str) -> Usage:
        return Usage(input_tokens=len(req.messages) * 10, output_tokens=len(text.split()))

    async def chat(self, req: ChatRequest) -> ChatResponse:
        out = self._next(req)
        if isinstance(out, Exception):
            raise out
        turn = out if isinstance(out, Turn) else Turn(text=out)
        return ChatResponse(
            message=Message(
                role="assistant", content=turn.text or None, tool_calls=self._calls(turn)
            ),
            usage=self._usage(req, turn.text),
            model=self.model,
            finish_reason="tool_calls" if turn.calls else "stop",
        )

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamChunk]:
        out = self._next(req)
        if isinstance(out, Exception):
            raise out
        turn = out if isinstance(out, Turn) else Turn(text=out)
        if turn.text:
            words = turn.text.split(" ")
            for i, word in enumerate(words):
                yield StreamChunk(delta=word + (" " if i < len(words) - 1 else ""))
        for i, call in enumerate(self._calls(turn)):
            raw = json.dumps(call.arguments)
            half = len(raw) // 2  # split like a real provider does
            yield StreamChunk(
                tool_call_delta={
                    "index": i,
                    "id": call.id,
                    "name": call.name,
                    "arguments_delta": raw[:half],
                }
            )
            yield StreamChunk(
                tool_call_delta={
                    "index": i,
                    "id": None,
                    "name": None,
                    "arguments_delta": raw[half:],
                }
            )
        yield StreamChunk(usage=self._usage(req, turn.text), model=self.model, finish_reason="stop")

    async def aclose(self) -> None:
        return None

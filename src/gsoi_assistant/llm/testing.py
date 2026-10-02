"""Deterministic provider for tests and offline development. Never used in production wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from gsoi_assistant.llm.base import (
    Capabilities,
    ChatRequest,
    ChatResponse,
    Message,
    StreamChunk,
    Usage,
)

Script = Callable[[ChatRequest], str | Exception]


class ScriptedProvider:
    def __init__(
        self,
        script: Script | list[str | Exception] | None = None,
        *,
        name: str = "scripted",
        model: str = "scripted-1",
        capabilities: Capabilities | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.capabilities = capabilities or Capabilities()
        self.requests: list[ChatRequest] = []
        self._queue: list[str | Exception] = list(script) if isinstance(script, list) else []
        self._fn: Script | None = script if callable(script) else None

    def _next(self, req: ChatRequest) -> str | Exception:
        self.requests.append(req)
        if self._fn is not None:
            return self._fn(req)
        if not self._queue:
            return "ok"
        return self._queue.pop(0)

    async def chat(self, req: ChatRequest) -> ChatResponse:
        out = self._next(req)
        if isinstance(out, Exception):
            raise out
        return ChatResponse(
            message=Message(role="assistant", content=out),
            usage=Usage(input_tokens=len(req.messages) * 10, output_tokens=len(out.split())),
            model=self.model,
            finish_reason="stop",
        )

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamChunk]:
        out = self._next(req)
        if isinstance(out, Exception):
            raise out
        for word in out.split(" "):
            yield StreamChunk(delta=word + " ")
        yield StreamChunk(
            usage=Usage(input_tokens=len(req.messages) * 10, output_tokens=len(out.split())),
            model=self.model,
            finish_reason="stop",
        )

    async def aclose(self) -> None:
        return None

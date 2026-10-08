"""Provider-neutral LLM types and the `LLMProvider` interface.

Nothing here knows about DeepSeek, OpenAI, Ollama, ...: adapters in `llm/providers`
translate to and from these types.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from pydantic import BaseModel, Field

from gsoi_assistant.core.types import Role


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Set when the model emitted arguments that are not valid JSON (common with small models).
    arguments_error: str | None = None


class Message(BaseModel):
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class ToolDef(BaseModel):
    """Tool schema exposed to the model."""

    name: str
    description: str
    parameters: dict[str, Any]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


class ChatRequest(BaseModel):
    messages: list[Message]
    tools: list[ToolDef] = Field(default_factory=list)
    temperature: float | None = None
    max_output_tokens: int | None = None
    response_schema: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    message: Message
    usage: Usage = Field(default_factory=Usage)
    model: str
    finish_reason: str | None = None
    latency_ms: int = 0
    # Filled by the gateway:
    profile: str | None = None
    provider: str | None = None
    cost_usd: float = 0.0


class StreamChunk(BaseModel):
    delta: str | None = None
    # {"index": int, "id": str | None, "name": str | None, "arguments_delta": str | None}
    tool_call_delta: dict[str, Any] | None = None
    # Terminal chunk only:
    usage: Usage | None = None
    model: str | None = None
    finish_reason: str | None = None
    cost_usd: float | None = None  # filled by the gateway


class Capabilities(BaseModel):
    tool_calling: bool = True
    structured_output: bool = False
    streaming: bool = True
    context_window: int = 32_000
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    is_local: bool = False


class LLMProvider(Protocol):
    name: str
    model: str
    capabilities: Capabilities

    async def chat(self, req: ChatRequest) -> ChatResponse: ...

    def stream(self, req: ChatRequest) -> AsyncIterator[StreamChunk]: ...

    async def aclose(self) -> None: ...

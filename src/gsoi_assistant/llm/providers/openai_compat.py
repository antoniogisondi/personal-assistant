"""Adapter for any server speaking the OpenAI chat-completions protocol.

Covers DeepSeek, OpenAI, Ollama (/v1), vLLM, llama.cpp server, and others.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import SecretStr

from gsoi_assistant.core.errors import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderUnavailableError,
    RateLimitedError,
)
from gsoi_assistant.llm.base import (
    Capabilities,
    ChatRequest,
    ChatResponse,
    Message,
    StreamChunk,
    ToolCall,
    Usage,
    tool_call_from_parts,
)


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        api_key: SecretStr | None,
        capabilities: Capabilities,
        timeout_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.capabilities = capabilities
        headers = {"Content-Type": "application/json"}
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key.get_secret_value()}"
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- request building -------------------------------------------------

    def _payload(self, req: ChatRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_message_to_wire(m) for m in req.messages],
            "stream": stream,
        }
        if req.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in req.tools
            ]
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.max_output_tokens is not None:
            payload["max_tokens"] = req.max_output_tokens
        if req.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": req.response_schema, "strict": True},
            }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    # ---- non-streaming ----------------------------------------------------

    async def chat(self, req: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        try:
            resp = await self._client.post(
                "chat/completions", json=self._payload(req, stream=False)
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"{self.name}: {type(exc).__name__}") from exc
        _raise_for_status(self.name, resp.status_code, resp.text)
        try:
            data = resp.json()
            choice = data["choices"][0]
            msg = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailableError(f"{self.name}: malformed response") from exc
        return ChatResponse(
            message=Message(
                role="assistant",
                content=msg.get("content"),
                tool_calls=[_parse_tool_call(tc) for tc in msg.get("tool_calls") or []],
            ),
            usage=_parse_usage(data.get("usage")),
            model=data.get("model") or self.model,
            finish_reason=choice.get("finish_reason"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # ---- streaming --------------------------------------------------------

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamChunk]:
        usage: Usage | None = None
        model: str | None = None
        finish: str | None = None
        try:
            async with self._client.stream(
                "POST", "chat/completions", json=self._payload(req, stream=True)
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    _raise_for_status(self.name, resp.status_code, body)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue  # blank lines and SSE comments/keep-alives
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    model = event.get("model") or model
                    if event.get("usage"):
                        usage = _parse_usage(event["usage"])
                    for choice in event.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield StreamChunk(delta=delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            fn = tc.get("function") or {}
                            yield StreamChunk(
                                tool_call_delta={
                                    "index": tc.get("index", 0),
                                    "id": tc.get("id"),
                                    "name": fn.get("name"),
                                    "arguments_delta": fn.get("arguments"),
                                }
                            )
                        finish = choice.get("finish_reason") or finish
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"{self.name}: {type(exc).__name__}") from exc
        yield StreamChunk(usage=usage or Usage(), model=model or self.model, finish_reason=finish)


# ---- wire helpers -----------------------------------------------------------


def _message_to_wire(m: Message) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        wire["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in m.tool_calls
        ]
    if m.tool_call_id is not None:
        wire["tool_call_id"] = m.tool_call_id
    return wire


def _parse_tool_call(raw: dict[str, Any]) -> ToolCall:
    fn = raw.get("function") or {}
    return tool_call_from_parts(raw.get("id"), fn.get("name"), fn.get("arguments"))


def _parse_usage(raw: dict[str, Any] | None) -> Usage:
    if not raw:
        return Usage()
    cached = raw.get("prompt_cache_hit_tokens")  # DeepSeek
    if cached is None:
        cached = (raw.get("prompt_tokens_details") or {}).get("cached_tokens")  # OpenAI
    return Usage(
        input_tokens=int(raw.get("prompt_tokens") or 0),
        output_tokens=int(raw.get("completion_tokens") or 0),
        cached_tokens=int(cached or 0),
    )


def _raise_for_status(name: str, status: int, body: str) -> None:
    if status < 400:
        return
    snippet = body[:200].replace("\n", " ")
    if status in (401, 403):
        raise ProviderAuthError(f"{name}: authentication failed ({status})")
    if status == 429:
        raise RateLimitedError(f"{name}: rate limited")
    if status in (408, 409) or status >= 500:
        raise ProviderUnavailableError(f"{name}: upstream error {status}")
    raise ProviderBadRequestError(f"{name}: request rejected ({status}): {snippet}")

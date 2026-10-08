"""Contract tests: every LLMProvider must satisfy the same behaviour.

The OpenAI-compatible adapter is exercised twice (DeepSeek-like with API key, Ollama-like
without) against a mocked HTTP layer; the ScriptedProvider must satisfy the same basics.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from pydantic import SecretStr

from gsoi_assistant.core.errors import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderUnavailableError,
    RateLimitedError,
)
from gsoi_assistant.llm.base import Capabilities, ChatRequest, LLMProvider, Message, ToolDef
from gsoi_assistant.llm.providers.openai_compat import OpenAICompatProvider
from gsoi_assistant.llm.testing import ScriptedProvider

REQ = ChatRequest(messages=[Message(role="user", content="ciao")])
TOOLS = [
    ToolDef(name="email.search", description="d", parameters={"type": "object", "properties": {}})
]

CONFIGS = {
    "deepseek-like": (
        "https://api.example-cloud.invalid/v1",
        SecretStr("sk-test-secret-value-123"),
    ),
    "ollama-like": ("http://localhost:11434/v1", None),
}


@pytest.fixture(params=list(CONFIGS))
def compat(request: pytest.FixtureRequest) -> tuple[OpenAICompatProvider, str]:
    base, key = CONFIGS[request.param]
    p = OpenAICompatProvider(
        name=request.param, model="m-1", base_url=base, api_key=key, capabilities=Capabilities()
    )
    return p, base.rstrip("/") + "/chat/completions"


def ok_body(content: str = "ciao a te", **extra: object) -> dict[str, object]:
    return {
        "model": "m-1",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4, **extra},
        **{},
    }


def sse(*events: object) -> str:
    lines = [f"data: {json.dumps(e)}\n\n" if not isinstance(e, str) else f"{e}\n\n" for e in events]
    return "".join(lines)


# ---- non-streaming ---------------------------------------------------------


@respx.mock
async def test_chat_parses_content_and_usage(compat: tuple[OpenAICompatProvider, str]) -> None:
    p, url = compat
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json=ok_body(prompt_cache_hit_tokens=5))
    )
    resp = await p.chat(REQ)
    assert resp.message.content == "ciao a te" and resp.finish_reason == "stop"
    assert (resp.usage.input_tokens, resp.usage.output_tokens, resp.usage.cached_tokens) == (
        11,
        4,
        5,
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "m-1" and sent["stream"] is False and "tools" not in sent


@respx.mock
async def test_auth_header_only_when_key_configured(
    compat: tuple[OpenAICompatProvider, str],
) -> None:
    p, url = compat
    route = respx.post(url).mock(return_value=httpx.Response(200, json=ok_body()))
    await p.chat(REQ)
    auth = route.calls[0].request.headers.get("authorization")
    if p.name == "deepseek-like":
        assert auth == "Bearer sk-test-secret-value-123"
    else:
        assert auth is None


@respx.mock
async def test_tool_calls_sent_and_parsed(compat: tuple[OpenAICompatProvider, str]) -> None:
    p, url = compat
    body = ok_body()
    body["choices"][0]["message"] = {  # type: ignore[index]
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "email.search", "arguments": '{"q": "today"}'},
            }
        ],
    }
    route = respx.post(url).mock(return_value=httpx.Response(200, json=body))
    resp = await p.chat(ChatRequest(messages=REQ.messages, tools=TOOLS))
    sent = json.loads(route.calls[0].request.content)
    assert sent["tools"][0]["function"]["name"] == "email.search"
    (tc,) = resp.message.tool_calls
    assert (tc.id, tc.name, tc.arguments, tc.arguments_error) == (
        "c1",
        "email.search",
        {"q": "today"},
        None,
    )


@respx.mock
async def test_invalid_tool_arguments_are_flagged_not_raised(
    compat: tuple[OpenAICompatProvider, str],
) -> None:
    p, url = compat
    body = ok_body()
    body["choices"][0]["message"] = {  # type: ignore[index]
        "content": None,
        "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": "{not json"}}],
    }
    respx.post(url).mock(return_value=httpx.Response(200, json=body))
    resp = await p.chat(ChatRequest(messages=REQ.messages, tools=TOOLS))
    assert resp.message.tool_calls[0].arguments == {}
    assert resp.message.tool_calls[0].arguments_error


@respx.mock
async def test_tool_result_messages_round_trip(compat: tuple[OpenAICompatProvider, str]) -> None:
    from gsoi_assistant.llm.base import ToolCall

    p, url = compat
    route = respx.post(url).mock(return_value=httpx.Response(200, json=ok_body()))
    msgs = [
        Message(role="user", content="x"),
        Message(role="assistant", tool_calls=[ToolCall(id="c1", name="t", arguments={"a": 1})]),
        Message(role="tool", content="result", tool_call_id="c1"),
    ]
    await p.chat(ChatRequest(messages=msgs))
    wire = json.loads(route.calls[0].request.content)["messages"]
    assert wire[1]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'
    assert wire[2]["tool_call_id"] == "c1"


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (429, RateLimitedError),
        (500, ProviderUnavailableError),
        (503, ProviderUnavailableError),
        (400, ProviderBadRequestError),
        (422, ProviderBadRequestError),
    ],
)
@respx.mock
async def test_http_errors_are_mapped(
    compat: tuple[OpenAICompatProvider, str], status: int, exc: type[Exception]
) -> None:
    p, url = compat
    respx.post(url).mock(return_value=httpx.Response(status, text="nope"))
    with pytest.raises(exc):
        await p.chat(REQ)


@respx.mock
async def test_network_errors_are_transient(compat: tuple[OpenAICompatProvider, str]) -> None:
    p, url = compat
    respx.post(url).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderUnavailableError):
        await p.chat(REQ)
    respx.post(url).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderUnavailableError):
        await p.chat(REQ)


@respx.mock
async def test_malformed_response_is_transient(compat: tuple[OpenAICompatProvider, str]) -> None:
    p, url = compat
    respx.post(url).mock(return_value=httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(ProviderUnavailableError):
        await p.chat(REQ)


@respx.mock
async def test_error_message_does_not_leak_api_key(
    compat: tuple[OpenAICompatProvider, str],
) -> None:
    p, url = compat
    respx.post(url).mock(return_value=httpx.Response(401, text="bad key sk-test-secret-value-123"))
    with pytest.raises(ProviderAuthError) as ei:
        await p.chat(REQ)
    assert "sk-test-secret-value-123" not in str(ei.value)


# ---- streaming ---------------------------------------------------------------------------


@respx.mock
async def test_stream_text_and_usage(compat: tuple[OpenAICompatProvider, str]) -> None:
    p, url = compat
    body = sse(
        {"model": "m-1", "choices": [{"delta": {"role": "assistant", "content": "Ci"}}]},
        {"choices": [{"delta": {"content": "ao"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
        "data: [DONE]",
    )
    route = respx.post(url).mock(return_value=httpx.Response(200, text=": keepalive\n\n" + body))
    chunks = [c async for c in p.stream(REQ)]
    assert "".join(c.delta or "" for c in chunks) == "Ciao"
    last = chunks[-1]
    assert last.usage is not None and last.usage.input_tokens == 7 and last.finish_reason == "stop"
    sent = json.loads(route.calls[0].request.content)
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True}


@respx.mock
async def test_stream_tool_call_deltas(compat: tuple[OpenAICompatProvider, str]) -> None:
    p, url = compat
    body = sse(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "c1", "function": {"name": "t", "arguments": '{"a"'}}
                        ]
                    }
                }
            ]
        },
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ": 1}"}}]}}]},
        "data: [DONE]",
    )
    respx.post(url).mock(return_value=httpx.Response(200, text=body))
    deltas = [c.tool_call_delta async for c in p.stream(REQ) if c.tool_call_delta]
    assert (
        deltas[0]["name"] == "t"
        and "".join(d["arguments_delta"] or "" for d in deltas) == '{"a": 1}'
    )


@respx.mock
async def test_stream_http_error_before_body(compat: tuple[OpenAICompatProvider, str]) -> None:
    p, url = compat
    respx.post(url).mock(return_value=httpx.Response(429, text="slow down"))
    with pytest.raises(RateLimitedError):
        _ = [c async for c in p.stream(REQ)]


@respx.mock
async def test_stream_ignores_garbage_lines(compat: tuple[OpenAICompatProvider, str]) -> None:
    p, url = compat
    body = "data: {not json\n\n" + sse({"choices": [{"delta": {"content": "ok"}}]}, "data: [DONE]")
    respx.post(url).mock(return_value=httpx.Response(200, text=body))
    assert "ok" in "".join([c.delta or "" async for c in p.stream(REQ)])


# ---- behaviour every provider shares -----------------------------------------------------


@pytest.fixture
def scripted() -> LLMProvider:
    return ScriptedProvider(["hello"])


async def test_scripted_provider_satisfies_contract(scripted: LLMProvider) -> None:
    resp = await scripted.chat(REQ)
    assert resp.message.role == "assistant" and resp.message.content == "hello"
    assert scripted.capabilities and scripted.name and scripted.model

    async def collect(p: LLMProvider) -> AsyncIterator[str]:
        async for c in p.stream(REQ):
            yield c.delta or ""

    assert "hello" in "".join([x async for x in collect(ScriptedProvider(["hello"]))])

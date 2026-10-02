from __future__ import annotations

import pytest

from gsoi_assistant.config.settings import ModelProfile
from gsoi_assistant.core.errors import (
    CapabilityError,
    EgressDeniedError,
    ProviderAuthError,
    ProviderUnavailableError,
    UnknownProfileError,
)
from gsoi_assistant.core.records import ModelCallRecord
from gsoi_assistant.core.types import DataClass
from gsoi_assistant.llm.base import Capabilities, ChatRequest, Message, ToolDef
from gsoi_assistant.llm.gateway import CallContext, LLMGateway
from gsoi_assistant.llm.testing import ScriptedProvider

REQ = ChatRequest(messages=[Message(role="user", content="hi")])


class ListRecorder:
    def __init__(self) -> None:
        self.records: list[ModelCallRecord] = []

    async def record(self, record: ModelCallRecord) -> None:
        self.records.append(record)


def profiles(fallback: str | None = "private") -> dict[str, ModelProfile]:
    return {
        "cloud": ModelProfile(provider="c", model="m", base_url="http://c", fallback=fallback),
        "private": ModelProfile(provider="l", model="m", base_url="http://l", is_local=True),
    }


def gw(
    cloud: ScriptedProvider, local: ScriptedProvider, rec: ListRecorder, **kw: object
) -> LLMGateway:
    return LLMGateway(
        profiles=profiles(**kw),  # type: ignore[arg-type]
        providers={"cloud": cloud, "private": local},
        recorder=rec,
        max_attempts=3,
        backoff_initial_s=0.0,
    )


def cloud_p(script: list[str | Exception] | None = None, **caps: object) -> ScriptedProvider:
    return ScriptedProvider(
        script,
        name="c",
        capabilities=Capabilities(input_cost_per_mtok=1000, output_cost_per_mtok=2000, **caps),  # type: ignore[arg-type]
    )


def local_p(script: list[str | Exception] | None = None) -> ScriptedProvider:
    return ScriptedProvider(script, name="l", capabilities=Capabilities(is_local=True))


async def test_chat_ok_records_and_costs() -> None:
    rec = ListRecorder()
    resp = await gw(cloud_p(["hello there"]), local_p(), rec).chat("cloud", REQ)
    assert resp.message.content == "hello there"
    assert resp.profile == "cloud" and resp.cost_usd > 0
    assert [r.status for r in rec.records] == ["ok"]


async def test_retries_transient_errors_then_succeeds() -> None:
    err = ProviderUnavailableError("boom")
    c = cloud_p([err, err, "finally"])
    resp = await gw(c, local_p(), ListRecorder()).chat("cloud", REQ)
    assert resp.message.content == "finally"
    assert len(c.requests) == 3


async def test_falls_back_to_local_after_retries_exhausted() -> None:
    err = ProviderUnavailableError("down")
    rec = ListRecorder()
    c = cloud_p([err, err, err])
    resp = await gw(c, local_p(["from local"]), rec).chat("cloud", REQ)
    assert resp.message.content == "from local" and resp.profile == "private"
    assert [r.status for r in rec.records] == ["error", "ok"]


async def test_non_retryable_error_is_not_retried_or_fallen_back() -> None:
    c = cloud_p([ProviderAuthError("bad key")])
    lo = local_p(["should not be used"])
    with pytest.raises(ProviderAuthError):
        await gw(c, lo, ListRecorder()).chat("cloud", REQ)
    assert len(c.requests) == 1 and lo.requests == []


async def test_secret_data_never_goes_to_cloud() -> None:
    c = cloud_p(["x"])
    with pytest.raises(EgressDeniedError):
        await gw(c, local_p(), ListRecorder()).chat(
            "cloud", REQ, CallContext(data_class=DataClass.SECRET)
        )
    assert c.requests == []


async def test_secret_data_allowed_on_local() -> None:
    resp = await gw(cloud_p(), local_p(["ok"]), ListRecorder()).chat(
        "private", REQ, CallContext(data_class=DataClass.SECRET)
    )
    assert resp.message.content == "ok"


async def test_fallback_to_cloud_is_blocked_by_egress() -> None:
    # local primary fails; its fallback is a cloud profile, which SECRET data may not reach.
    p = {
        "private": ModelProfile(
            provider="l", model="m", base_url="http://l", is_local=True, fallback="cloud"
        ),
        "cloud": ModelProfile(provider="c", model="m", base_url="http://c"),
    }
    c = cloud_p(["leak"])
    lo = local_p([ProviderUnavailableError("down")] * 3)
    g = LLMGateway(
        profiles=p, providers={"cloud": c, "private": lo}, max_attempts=3, backoff_initial_s=0.0
    )
    with pytest.raises(ProviderUnavailableError):
        await g.chat("private", REQ, CallContext(data_class=DataClass.SECRET))
    assert c.requests == []


async def test_tools_rejected_for_model_without_tool_calling() -> None:
    c = cloud_p(tool_calling=False)
    req = ChatRequest(
        messages=REQ.messages,
        tools=[ToolDef(name="t", description="d", parameters={"type": "object"})],
    )
    with pytest.raises(CapabilityError):
        await gw(c, local_p(), ListRecorder(), fallback=None).chat("cloud", req)


async def test_unknown_profile() -> None:
    with pytest.raises(UnknownProfileError):
        await gw(cloud_p(), local_p(), ListRecorder()).chat("nope", REQ)


async def test_recorder_failure_does_not_break_request() -> None:
    class Broken:
        async def record(self, record: ModelCallRecord) -> None:
            raise RuntimeError("db down")

    g = LLMGateway(
        profiles=profiles(),
        providers={"cloud": cloud_p(["fine"]), "private": local_p()},
        recorder=Broken(),
    )
    assert (await g.chat("cloud", REQ)).message.content == "fine"


async def test_error_text_in_record_is_redacted() -> None:
    rec = ListRecorder()
    c = cloud_p([ProviderAuthError("rejected key sk-abcdefghijklmnop123")])
    with pytest.raises(ProviderAuthError):
        await gw(c, local_p(), rec).chat("cloud", REQ)
    assert rec.records[0].error is not None and "sk-abcdefghijklmnop123" not in rec.records[0].error


async def test_stream_yields_chunks_cost_and_records() -> None:
    rec = ListRecorder()
    chunks = [c async for c in gw(cloud_p(["a b c"]), local_p(), rec).stream("cloud", REQ)]
    text = "".join(c.delta or "" for c in chunks)
    assert text.split() == ["a", "b", "c"]
    assert chunks[-1].usage is not None and chunks[-1].cost_usd is not None
    assert [r.status for r in rec.records] == ["ok"]


async def test_stream_retries_before_first_chunk() -> None:
    c = cloud_p([ProviderUnavailableError("x"), "ok now"])
    chunks = [x async for x in gw(c, local_p(), ListRecorder()).stream("cloud", REQ)]
    assert "ok" in "".join(x.delta or "" for x in chunks)
    assert len(c.requests) == 2


async def test_stream_falls_back_before_first_chunk() -> None:
    c = cloud_p([ProviderUnavailableError("x")] * 3)
    chunks = [x async for x in gw(c, local_p(["local reply"]), ListRecorder()).stream("cloud", REQ)]
    assert "local" in "".join(x.delta or "" for x in chunks)


async def test_stream_does_not_retry_after_first_chunk() -> None:
    from collections.abc import AsyncIterator

    from gsoi_assistant.llm.base import StreamChunk

    class Flaky(ScriptedProvider):
        async def stream(self, req: ChatRequest) -> AsyncIterator[StreamChunk]:
            self.requests.append(req)
            yield StreamChunk(delta="partial")
            raise ProviderUnavailableError("cut")

    c = Flaky(name="c", capabilities=Capabilities())
    with pytest.raises(ProviderUnavailableError):
        _ = [x async for x in gw(c, local_p(["no"]), ListRecorder()).stream("cloud", REQ)]
    assert len(c.requests) == 1

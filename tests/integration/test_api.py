from __future__ import annotations

import json
import uuid

import httpx
from sqlalchemy import select

from gsoi_assistant.api.container import Container
from gsoi_assistant.core.errors import ProviderAuthError, ProviderUnavailableError
from gsoi_assistant.db import models
from gsoi_assistant.llm.testing import ScriptedProvider


async def rows(c: Container, model: type) -> list:  # type: ignore[type-arg]
    async with c.repo._sf() as s:
        return list((await s.execute(select(model))).scalars())


async def test_health_and_ready(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/ready")).json() == {"status": "ready"}


async def test_auth_required(client: httpx.AsyncClient) -> None:
    for headers in ({"Authorization": "Bearer wrong"}, {"Authorization": ""}):
        r = await client.post("/v1/chat", json={"message": "hi"}, headers=headers)
        assert r.status_code == 401
    anon = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    assert (await anon.post("/v1/chat", json={"message": "hi"})).status_code == 401
    assert (await anon.post("/v1/chat/stream", json={"message": "hi"})).status_code == 401


async def test_request_id_echoed_or_generated(client: httpx.AsyncClient) -> None:
    r = await client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    r = await client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert uuid.UUID(r.headers["x-request-id"])


async def test_chat_persists_run_messages_and_model_call(
    client: httpx.AsyncClient, container: Container, cloud: ScriptedProvider
) -> None:
    cloud._queue = ["Ciao! Come posso aiutarti?"]
    r = await client.post("/v1/chat", json={"message": "Ciao"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"] == "Ciao! Come posso aiutarti?" and body["model"] == "cloud-model"
    assert body["cost_usd"] >= 0

    (run,) = await rows(container, models.Run)
    assert run.status == "done" and str(run.id) == body["run_id"] and run.finished_at is not None
    msgs = sorted(await rows(container, models.Message), key=lambda m: m.created_at)
    assert [m.role for m in msgs] == ["user", "assistant"]
    (call,) = await rows(container, models.ModelCall)
    assert call.run_id == run.id and call.status == "ok" and call.profile == "reasoning"


async def test_conversation_history_is_sent_to_the_model(
    client: httpx.AsyncClient, cloud: ScriptedProvider
) -> None:
    cloud._queue = ["first answer", "second answer"]
    first = (await client.post("/v1/chat", json={"message": "one"})).json()
    await client.post(
        "/v1/chat", json={"message": "two", "conversation_id": first["conversation_id"]}
    )
    sent = [m.content for m in cloud.requests[1].messages]
    assert sent[0] and "GSOI" in sent[0]  # system prompt
    assert sent[1:] == ["one", "first answer", "two"]

    msgs = (await client.get(f"/v1/conversations/{first['conversation_id']}/messages")).json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]


async def test_unknown_conversation_is_404(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/chat", json={"message": "x", "conversation_id": str(uuid.uuid4())})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert (await client.get(f"/v1/conversations/{uuid.uuid4()}/messages")).status_code == 404


async def test_unknown_profile_is_400(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/chat", json={"message": "x", "profile": "ghost"})
    assert r.status_code == 400


async def test_validation(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/chat", json={"message": ""})).status_code == 422
    assert (
        await client.post("/v1/chat", json={"message": "x", "data_class": 9})
    ).status_code == 422


async def test_secret_data_to_cloud_is_blocked_before_any_state(
    client: httpx.AsyncClient, container: Container, cloud: ScriptedProvider
) -> None:
    r = await client.post("/v1/chat", json={"message": "my password is ...", "data_class": 2})
    assert r.status_code == 403 and r.json()["error"]["code"] == "egress_denied"
    assert cloud.requests == []
    assert await rows(container, models.Run) == []


async def test_secret_data_is_served_by_local_profile(
    client: httpx.AsyncClient, local: ScriptedProvider
) -> None:
    local._queue = ["handled locally"]
    r = await client.post(
        "/v1/chat", json={"message": "secret", "data_class": 2, "profile": "private"}
    )
    assert r.status_code == 200 and r.json()["content"] == "handled locally"


async def test_transient_failure_falls_back_to_local(
    client: httpx.AsyncClient,
    cloud: ScriptedProvider,
    local: ScriptedProvider,
    container: Container,
) -> None:
    cloud._queue = [ProviderUnavailableError("down")] * 3
    local._queue = ["local saved the day"]
    r = await client.post("/v1/chat", json={"message": "hello"})
    assert r.status_code == 200 and r.json()["content"] == "local saved the day"
    calls = await rows(container, models.ModelCall)
    assert sorted(c.status for c in calls) == ["error", "ok"]


async def test_provider_auth_failure_marks_run_failed(
    client: httpx.AsyncClient, cloud: ScriptedProvider, container: Container
) -> None:
    cloud._queue = [ProviderAuthError("bad key sk-abcdefghijklmnop123")]
    r = await client.post("/v1/chat", json={"message": "hello"})
    assert r.status_code == 502 and "sk-abcdefghijklmnop123" not in r.text
    (run,) = await rows(container, models.Run)
    assert run.status == "failed" and "sk-abcdefghijklmnop123" not in (run.result or "")


def parse_sse(text: str) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    out = []
    for block in (
        text.strip().split("\r\n\r\n") if "\r\n\r\n" in text else text.strip().split("\n\n")
    ):
        ev = data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if ev:
            out.append((ev, json.loads(data)))
    return out


async def test_stream_emits_ordered_events_and_persists(
    client: httpx.AsyncClient, cloud: ScriptedProvider, container: Container
) -> None:
    cloud._queue = ["streamed reply here"]
    r = await client.post("/v1/chat/stream", json={"message": "hi"})
    assert r.status_code == 200
    events = parse_sse(r.text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "run_started" and kinds[-1] == "final" and "token" in kinds
    final = events[-1][1]
    assert final["content"].split() == ["streamed", "reply", "here"]
    (run,) = await rows(container, models.Run)
    assert run.status == "done" and run.result == final["content"]


async def test_stream_error_is_an_event_and_run_failed(
    client: httpx.AsyncClient, cloud: ScriptedProvider, container: Container
) -> None:
    cloud._queue = [ProviderAuthError("nope")]
    events = parse_sse((await client.post("/v1/chat/stream", json={"message": "hi"})).text)
    assert events[-1][0] == "error" and events[-1][1]["code"] == "llm_error"
    (run,) = await rows(container, models.Run)
    assert run.status == "failed"


async def test_stream_egress_denied_is_an_error_event(client: httpx.AsyncClient) -> None:
    events = parse_sse(
        (await client.post("/v1/chat/stream", json={"message": "x", "data_class": 2})).text
    )
    assert events == [
        ("error", {"type": "error", "code": "egress_denied", "message": events[0][1]["message"]})
    ]

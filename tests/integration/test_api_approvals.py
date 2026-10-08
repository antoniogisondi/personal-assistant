from __future__ import annotations

import uuid

import httpx

from gsoi_assistant.api.container import Container
from gsoi_assistant.llm.testing import ScriptedProvider, use
from support import Outbox
from tests_sse import parse_sse

SEND = {"to": "marco@example.com", "subject": "Hi", "body": "Hello"}


async def start(client: httpx.AsyncClient, cloud: ScriptedProvider) -> dict:  # type: ignore[type-arg]
    cloud._queue = [use("mail.send", **SEND), "Sent!"]
    r = await client.post("/v1/chat", json={"message": "email Marco"})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_chat_returns_pending_approval(
    client: httpx.AsyncClient, cloud: ScriptedProvider, outbox: Outbox
) -> None:
    body = await start(client, cloud)
    assert body["status"] == "awaiting_approval" and body["content"] == ""
    a = body["approval"]
    assert a["tool"] == "mail.send" and a["risk"] == "EXTERNAL" and a["strength"] == "normal"
    assert a["display"]["arguments"] == SEND and outbox.sent == []

    listed = (await client.get("/v1/approvals")).json()
    assert [x["approval_id"] for x in listed] == [a["approval_id"]]


async def test_approve_via_api_runs_the_action(
    client: httpx.AsyncClient, cloud: ScriptedProvider, outbox: Outbox
) -> None:
    a = (await start(client, cloud))["approval"]
    r = await client.post(f"/v1/approvals/{a['approval_id']}/decision", json={"approve": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done" and r.json()["content"] == "Sent!"
    assert len(outbox.sent) == 1
    assert (await client.get("/v1/approvals")).json() == []

    run = (await client.get(f"/v1/runs/{a['run_id']}")).json()
    assert run["status"] == "done" and run["tainted"] is False
    assert [(c["tool"], c["status"]) for c in run["tool_calls"]] == [
        ("mail.send", "approval_required"),
        ("mail.send", "ok"),
    ]


async def test_reject_via_api(
    client: httpx.AsyncClient, cloud: ScriptedProvider, outbox: Outbox
) -> None:
    a = (await start(client, cloud))["approval"]
    r = await client.post(f"/v1/approvals/{a['approval_id']}/decision", json={"approve": False})
    assert r.status_code == 200 and outbox.sent == []


async def test_double_decision_is_409(client: httpx.AsyncClient, cloud: ScriptedProvider) -> None:
    a = (await start(client, cloud))["approval"]
    url = f"/v1/approvals/{a['approval_id']}/decision"
    assert (await client.post(url, json={"approve": True})).status_code == 200
    r = await client.post(url, json={"approve": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"


async def test_unknown_approval_is_404_and_requires_auth(client: httpx.AsyncClient) -> None:
    url = f"/v1/approvals/{uuid.uuid4()}/decision"
    assert (await client.post(url, json={"approve": True})).status_code == 404
    anon = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    assert (await anon.post(url, json={"approve": True})).status_code == 401
    assert (await anon.get("/v1/approvals")).status_code == 401
    assert (await anon.get(f"/v1/runs/{uuid.uuid4()}")).status_code == 401
    assert (await anon.get("/v1/audit/verify")).status_code == 401


async def test_destructive_action_needs_confirm_tool(
    client: httpx.AsyncClient, cloud: ScriptedProvider, container: Container
) -> None:
    nid = await container.executor._services.notes.create("owner", "t", "b")
    cloud._queue = [use("notes.delete", note_id=str(nid)), "Deleted."]
    a = (await client.post("/v1/chat", json={"message": "delete it"})).json()["approval"]
    assert a["strength"] == "strong" and a["risk"] == "DESTRUCTIVE"
    url = f"/v1/approvals/{a['approval_id']}/decision"
    bad = await client.post(url, json={"approve": True})
    assert bad.status_code == 400 and "notes.delete" in bad.text
    ok = await client.post(url, json={"approve": True, "confirm_tool": "notes.delete"})
    assert ok.status_code == 200 and ok.json()["content"] == "Deleted."
    assert await container.executor._services.notes.get("owner", nid) is None


async def test_decision_stream(client: httpx.AsyncClient, cloud: ScriptedProvider) -> None:
    a = (await start(client, cloud))["approval"]
    r = await client.post(
        f"/v1/approvals/{a['approval_id']}/decision/stream", json={"approve": True}
    )
    kinds = [k for k, _ in parse_sse(r.text)]
    assert kinds[0] == "run_started" and "tool_result" in kinds and kinds[-1] == "final"


async def test_chat_stream_reports_approval_required(
    client: httpx.AsyncClient, cloud: ScriptedProvider
) -> None:
    cloud._queue = [use("mail.send", **SEND)]
    events = parse_sse((await client.post("/v1/chat/stream", json={"message": "email"})).text)
    kinds = [k for k, _ in events]
    assert kinds == ["run_started", "tool_call_started", "approval_required"]
    assert events[-1][1]["display"]["arguments"] == SEND


async def test_audit_verify_endpoint_and_root_redirect(
    client: httpx.AsyncClient, cloud: ScriptedProvider
) -> None:
    await start(client, cloud)
    v = (await client.get("/v1/audit/verify")).json()
    assert v["ok"] is True and v["entries"] >= 2
    r = await client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/docs"


async def test_runs_are_private_to_their_owner(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"/v1/runs/{uuid.uuid4()}")).status_code == 404

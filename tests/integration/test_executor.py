from __future__ import annotations

import json
import uuid

import pytest

from gsoi_assistant.api.container import Container, build_container
from gsoi_assistant.core.errors import BadRequestError, ConflictError, NotFoundError
from gsoi_assistant.llm.base import ToolCall
from gsoi_assistant.tools.executor import ExecContext
from support import Outbox, new_run

SEND = {"to": "marco@example.com", "subject": "Hi", "body": "Hello"}


def call(name: str, args: dict | None = None, cid: str = "c1") -> ToolCall:  # type: ignore[type-arg]
    return ToolCall(id=cid, name=name, arguments=args or {})


async def ctx(c: Container, tainted: bool = False) -> ExecContext:
    return ExecContext(user_id="owner", run_id=await new_run(c), tainted=tainted)


async def test_unknown_tool_is_an_error_not_a_crash(container: Container) -> None:
    r = await container.executor.execute(call("nope.nothing"), await ctx(container))
    assert r.status == "error" and "unknown tool" in r.content


async def test_invalid_arguments_name_the_field_and_do_not_run(
    container: Container, outbox: Outbox
) -> None:
    r = await container.executor.execute(call("mail.send", {"to": "x"}), await ctx(container))
    assert r.status == "error" and "subject" in r.content and outbox.sent == []


async def test_malformed_json_arguments(container: Container) -> None:
    bad = ToolCall(
        id="c", name="time__now", arguments={}, arguments_error="invalid JSON arguments: x"
    )
    r = await container.executor.execute(bad, await ctx(container))
    assert r.status == "error" and "invalid arguments" in r.content


async def test_read_tool_runs_by_wire_name(container: Container) -> None:
    r = await container.executor.execute(call("time__now"), await ctx(container))
    assert r.status == "ok" and "iso" in json.loads(r.content)


async def test_write_local_allowed_when_clean_but_needs_approval_when_tainted(
    container: Container,
) -> None:
    args = {"title": "buy milk"}
    ok = await container.executor.execute(call("tasks.create", args), await ctx(container))
    assert ok.status == "ok"
    gated = await container.executor.execute(
        call("tasks.create", args), await ctx(container, tainted=True)
    )
    assert gated.status == "approval_required" and gated.approval is not None


async def test_external_action_requires_approval_and_shows_exact_arguments(
    container: Container, outbox: Outbox
) -> None:
    r = await container.executor.execute(call("mail.send", SEND), await ctx(container))
    assert r.status == "approval_required" and outbox.sent == []
    a = r.approval
    assert a is not None and a.strength == "normal"
    assert a.display["arguments"] == SEND and "marco@example.com" in a.display["summary"]


async def test_same_pending_request_is_deduplicated(container: Container) -> None:
    c = await ctx(container)
    a1 = (await container.executor.execute(call("mail.send", SEND), c)).approval
    a2 = (await container.executor.execute(call("mail.send", SEND, "c2"), c)).approval
    assert a1 and a2 and a1.id == a2.id


async def test_approval_is_single_use(container: Container, outbox: Outbox) -> None:
    c = await ctx(container)
    first = await container.executor.execute(call("mail.send", SEND), c)
    assert first.approval
    await container.approvals.decide(
        user_id="owner", approval_id=first.approval.id, approve=True, via="t"
    )
    done = await container.executor.execute(call("mail.send", SEND), c)
    assert done.status == "ok" and len(outbox.sent) == 1
    again = await container.executor.execute(call("mail.send", SEND), c)
    assert again.status == "approval_required" and len(outbox.sent) == 1  # needs a fresh approval


async def test_approval_does_not_cover_different_arguments(
    container: Container, outbox: Outbox
) -> None:
    c = await ctx(container)
    first = await container.executor.execute(call("mail.send", SEND), c)
    assert first.approval
    await container.approvals.decide(
        user_id="owner", approval_id=first.approval.id, approve=True, via="t"
    )
    swapped = {**SEND, "to": "attacker@evil.example"}
    r = await container.executor.execute(call("mail.send", swapped), c)
    assert r.status == "approval_required" and outbox.sent == []
    assert r.approval and r.approval.display["arguments"]["to"] == "attacker@evil.example"


async def test_approval_is_bound_to_the_run(container: Container, outbox: Outbox) -> None:
    c1, c2 = await ctx(container), await ctx(container)
    r1 = await container.executor.execute(call("mail.send", SEND), c1)
    assert r1.approval
    await container.approvals.decide(
        user_id="owner", approval_id=r1.approval.id, approve=True, via="t"
    )
    r2 = await container.executor.execute(call("mail.send", SEND), c2)
    assert r2.status == "approval_required" and outbox.sent == []


async def test_destructive_needs_strong_confirmation(container: Container) -> None:
    c = await ctx(container)
    nid = await container.executor._services.notes.create("owner", "t", "b")
    r = await container.executor.execute(call("notes.delete", {"note_id": str(nid)}), c)
    assert r.approval and r.approval.strength == "strong"
    with pytest.raises(BadRequestError):
        await container.approvals.decide(
            user_id="owner", approval_id=r.approval.id, approve=True, via="t"
        )
    await container.approvals.decide(
        user_id="owner",
        approval_id=r.approval.id,
        approve=True,
        via="t",
        confirm_tool="notes.delete",
    )
    done = await container.executor.execute(call("notes.delete", {"note_id": str(nid)}), c)
    assert done.status == "ok" and json.loads(done.content)["deleted"] is True


async def test_rejecting_never_allows_execution(container: Container, outbox: Outbox) -> None:
    c = await ctx(container)
    r = await container.executor.execute(call("mail.send", SEND), c)
    assert r.approval
    await container.approvals.decide(
        user_id="owner", approval_id=r.approval.id, approve=False, via="t"
    )
    again = await container.executor.execute(call("mail.send", SEND), c)
    assert again.status == "approval_required" and outbox.sent == []


async def test_approvals_cannot_be_decided_twice_or_by_someone_else(container: Container) -> None:
    r = await container.executor.execute(call("mail.send", SEND), await ctx(container))
    assert r.approval
    with pytest.raises(NotFoundError):
        await container.approvals.decide(
            user_id="mallory", approval_id=r.approval.id, approve=True, via="t"
        )
    await container.approvals.decide(
        user_id="owner", approval_id=r.approval.id, approve=True, via="t"
    )
    with pytest.raises(ConflictError):
        await container.approvals.decide(
            user_id="owner", approval_id=r.approval.id, approve=False, via="t"
        )
    with pytest.raises(NotFoundError):
        await container.approvals.decide(
            user_id="owner", approval_id=uuid.uuid4(), approve=True, via="t"
        )


async def _expire(container: Container, approval_id: uuid.UUID) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from gsoi_assistant.db import models

    async with container.repo._sf() as s, s.begin():
        await s.execute(
            update(models.Approval)
            .where(models.Approval.id == approval_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )


async def test_expired_approved_approval_cannot_be_used(
    container: Container, outbox: Outbox
) -> None:
    c = await ctx(container)
    r = await container.executor.execute(call("mail.send", SEND), c)
    assert r.approval
    await container.approvals.decide(
        user_id="owner", approval_id=r.approval.id, approve=True, via="t"
    )
    await _expire(container, r.approval.id)
    again = await container.executor.execute(call("mail.send", SEND), c)
    assert again.status == "approval_required" and outbox.sent == []


async def test_expired_pending_approval_cannot_be_approved(container: Container) -> None:
    r = await container.executor.execute(call("mail.send", SEND), await ctx(container))
    assert r.approval
    await _expire(container, r.approval.id)
    with pytest.raises(ConflictError, match="expired"):
        await container.approvals.decide(
            user_id="owner", approval_id=r.approval.id, approve=True, via="t"
        )
    assert await container.approvals.list_pending("owner") == []


async def test_tool_failure_is_contained_and_secrets_never_leak(container: Container) -> None:
    c = await ctx(container)
    r = await container.executor.execute(call("test.boom"), c)
    assert r.status == "error" and "sk-leak" not in r.content and "postgres" not in r.content
    rows = await container.tool_calls.for_run(c.run_id)
    assert rows[0].error and "sk-leakleakleakleak123" not in rows[0].error


async def test_tool_timeout(container: Container) -> None:
    r = await container.executor.execute(call("test.slow"), await ctx(container))
    assert r.status == "error" and "timed out" in r.content


async def test_large_output_is_truncated(container: Container) -> None:
    r = await container.executor.execute(call("test.big"), await ctx(container))
    assert len(r.content) < 9000 and "truncated" in r.content


async def test_untrusted_output_is_wrapped_and_taints(container: Container) -> None:
    r = await container.executor.execute(
        call("web.read", {"url": "http://x"}), await ctx(container)
    )
    assert r.status == "ok" and r.tainted
    assert r.content.startswith('<untrusted source="tool:web.read">') and r.content.endswith(
        "</untrusted>"
    )
    assert r.content.count("</untrusted>") == 1  # hostile closing tag neutralised


async def test_readonly_mode_denies_writes(settings, cloud, local, outbox) -> None:  # type: ignore[no-untyped-def]
    from support import make_tools

    ro = build_container(
        settings.model_copy(update={"readonly": True}),
        providers={"reasoning": cloud, "private": local},
        extra_tools=make_tools(outbox),
    )
    from gsoi_assistant.db.base import Base

    async with ro.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        c = await ctx(ro)
        assert (
            await ro.executor.execute(call("tasks.create", {"title": "x"}), c)
        ).status == "denied"
        assert (await ro.executor.execute(call("time__now"), c)).status == "ok"
    finally:
        await ro.aclose()


async def test_every_call_is_recorded_and_audit_chain_is_valid(container: Container) -> None:
    c = await ctx(container)
    await container.executor.execute(call("time__now"), c)
    await container.executor.execute(call("mail.send", SEND), c)
    await container.executor.execute(call("nope.x"), c)
    rows = await container.tool_calls.for_run(c.run_id)
    assert [r.status for r in rows] == ["ok", "approval_required", "error"]
    result = await container.audit.verify()
    assert result.ok and result.entries >= 4  # 3 calls + approval.requested


async def test_builtin_notes_and_tasks_round_trip(container: Container) -> None:
    c = await ctx(container)
    ex = container.executor
    assert (
        await ex.execute(call("notes.create", {"title": "Idea", "body": "GSOI voice"}), c)
    ).status == "ok"
    found = json.loads((await ex.execute(call("notes.search", {"query": "voice"}), c)).content)
    assert found["notes"][0]["title"] == "Idea"
    assert (
        json.loads((await ex.execute(call("notes.search", {"query": "zzz"}), c)).content)["notes"]
        == []
    )
    t = json.loads((await ex.execute(call("tasks.create", {"title": "Call Marco"}), c)).content)
    listed = json.loads((await ex.execute(call("tasks.list"), c)).content)["tasks"]
    assert [x["title"] for x in listed] == ["Call Marco"]
    await ex.execute(call("tasks.complete", {"task_id": t["task_id"]}), c)
    assert json.loads((await ex.execute(call("tasks.list"), c)).content)["tasks"] == []


async def test_data_is_scoped_per_user(container: Container) -> None:
    ex = container.executor
    mine = await ctx(container)
    await ex.execute(call("notes.create", {"title": "private", "body": "x"}), mine)
    other = ExecContext(user_id="someone-else", run_id=await new_run(container, "someone-else"))
    res = json.loads((await ex.execute(call("notes.search"), other)).content)
    assert res["notes"] == []

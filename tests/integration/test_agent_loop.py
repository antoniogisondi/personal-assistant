from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from gsoi_assistant.agent.service import ChatCommand
from gsoi_assistant.api.container import Container, build_container
from gsoi_assistant.config.settings import Settings
from gsoi_assistant.core.errors import BudgetExceededError, ConflictError, NotFoundError
from gsoi_assistant.core.events import (
    ApprovalRequiredEvent,
    FinalEvent,
    TokenEvent,
    ToolCallStarted,
    ToolResultEvent,
)
from gsoi_assistant.db import models
from gsoi_assistant.llm.base import Capabilities
from gsoi_assistant.llm.testing import ScriptedProvider, ToolUse, Turn, use
from support import Outbox, make_tools

SEND = {"to": "marco@example.com", "subject": "Hi", "body": "Hello"}


def cmd(msg: str = "hi", **kw: object) -> ChatCommand:
    return ChatCommand(user_id="owner", message=msg, profile="reasoning", **kw)  # type: ignore[arg-type]


async def run_row(c: Container, run_id: uuid.UUID) -> models.Run:
    row = await c.repo.get_run(run_id)
    assert row is not None
    return row


async def test_tool_call_then_answer(container: Container, cloud: ScriptedProvider) -> None:
    cloud._queue = [use("time.now"), "It is Thursday."]
    r = await container.agent.reply(cmd("what day is it?"))
    assert r.status == "done" and r.content == "It is Thursday."

    # The second model request carries the assistant tool call AND the tool result.
    second = cloud.requests[1].messages
    assert second[-2].role == "assistant" and second[-2].tool_calls[0].name == "time__now"
    assert second[-1].role == "tool" and second[-1].tool_call_id == second[-2].tool_calls[0].id
    assert '"weekday"' in (second[-1].content or "")

    calls = await container.tool_calls.for_run(r.run_id)
    assert [(c.tool, c.status) for c in calls] == [("time.now", "ok")]
    assert (await run_row(container, r.run_id)).status == "done"


async def test_tools_are_exposed_with_dot_free_names(
    container: Container, cloud: ScriptedProvider
) -> None:
    await container.agent.reply(cmd())
    names = [t.name for t in cloud.requests[0].tools]
    assert "time__now" in names and "notes__delete" in names
    assert all("." not in n for n in names)


async def test_models_without_tool_calling_get_no_tools(
    settings: Settings, local: ScriptedProvider
) -> None:
    plain = ScriptedProvider(["hello"], name="p", capabilities=Capabilities(tool_calling=False))
    c = build_container(settings, providers={"reasoning": plain, "private": local})
    from gsoi_assistant.db.base import Base

    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        assert (await c.agent.reply(cmd())).content == "hello"
        assert plain.requests[0].tools == []
    finally:
        await c.aclose()


async def test_unknown_tool_from_model_is_reported_back_and_loop_continues(
    container: Container, cloud: ScriptedProvider
) -> None:
    cloud._queue = [use("hack.everything"), "Sorry, I can't do that."]
    r = await container.agent.reply(cmd())
    assert r.content == "Sorry, I can't do that."
    assert "unknown tool" in (cloud.requests[1].messages[-1].content or "")


async def test_multiple_tool_calls_in_one_turn(
    container: Container, cloud: ScriptedProvider
) -> None:
    turn = Turn(calls=[ToolUse("time.now"), ToolUse("tasks.list")])
    cloud._queue = [turn, "done"]
    r = await container.agent.reply(cmd())
    assert r.content == "done"
    tool_msgs = [m for m in cloud.requests[1].messages if m.role == "tool"]
    assert len(tool_msgs) == 2


async def test_stream_event_order(container: Container, cloud: ScriptedProvider) -> None:
    cloud._queue = [Turn(text="Checking.", calls=[ToolUse("time.now")]), "All good now."]
    events = [e async for e in container.agent.stream(cmd())]
    kinds = [e.type for e in events]
    assert kinds[0] == "run_started" and kinds[-1] == "final"
    assert kinds.index("tool_call_started") < kinds.index("tool_result") < kinds.index("final")
    assert any(isinstance(e, TokenEvent) for e in events)
    assert isinstance(events[-1], FinalEvent) and events[-1].content == "All good now."
    started = next(e for e in events if isinstance(e, ToolCallStarted))
    assert started.tool == "time.now"


async def test_budget_stops_runaway_loops(
    settings: Settings, local: ScriptedProvider, outbox: Outbox
) -> None:
    looping = ScriptedProvider(lambda req: use("time.now"), name="p")
    s = settings.model_copy(update={"agent_max_steps": 3})
    c = build_container(
        s, providers={"reasoning": looping, "private": local}, extra_tools=make_tools(outbox)
    )
    from gsoi_assistant.db.base import Base

    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        with pytest.raises(BudgetExceededError, match="step limit"):
            await c.agent.reply(cmd())
        assert len(looping.requests) == 3
        async with c.repo._sf() as sess:
            run = (await sess.execute(select(models.Run))).scalar_one()
        assert run.status == "failed"
        events = [e async for e in c.agent.stream(cmd())]
        assert events[-1].type == "error" and events[-1].code == "budget_exceeded"  # type: ignore[union-attr]
    finally:
        await c.aclose()


async def test_too_many_tool_calls_in_one_step_are_cut(
    container: Container, cloud: ScriptedProvider
) -> None:
    cloud._queue = [Turn(calls=[ToolUse("time.now") for _ in range(12)]), "ok"]
    await container.agent.reply(cmd())
    tool_msgs = [m for m in cloud.requests[1].messages if m.role == "tool"]
    assert len(tool_msgs) == 12
    assert sum("too many tool calls" in (m.content or "") for m in tool_msgs) == 4


# ---- approval: suspend / resume ------------------------------------------------


async def test_run_suspends_for_approval_then_resumes(
    container: Container, cloud: ScriptedProvider, outbox: Outbox
) -> None:
    cloud._queue = [use("mail.send", **SEND), "Email sent to Marco."]
    first = await container.agent.reply(cmd("email marco"))
    assert first.status == "awaiting_approval" and first.approval is not None
    assert outbox.sent == []
    assert (await run_row(container, first.run_id)).status == "awaiting_approval"
    assert first.approval.display["arguments"] == SEND

    final = await container.agent.resume("owner", first.approval.approval_id, True)
    assert final.status == "done" and final.content == "Email sent to Marco."
    assert len(outbox.sent) == 1 and outbox.sent[0].to == "marco@example.com"
    row = await run_row(container, first.run_id)
    assert row.status == "done" and row.finished_at is not None

    msgs = await container.repo.history(first.conversation_id, 10)
    assert [m.role for m in msgs] == ["user", "assistant"]


async def test_rejection_is_reported_to_the_model_and_nothing_is_sent(
    container: Container, cloud: ScriptedProvider, outbox: Outbox
) -> None:
    cloud._queue = [use("mail.send", **SEND), "Understood, I won't send it."]
    first = await container.agent.reply(cmd())
    assert first.approval
    final = await container.agent.resume("owner", first.approval.approval_id, False)
    assert final.content == "Understood, I won't send it."
    assert outbox.sent == []
    assert "rejected" in (cloud.requests[-1].messages[-1].content or "")


async def test_resume_works_after_a_restart(
    settings: Settings,
    container: Container,
    cloud: ScriptedProvider,
    local: ScriptedProvider,
    outbox: Outbox,
) -> None:
    cloud._queue = [use("mail.send", **SEND)]
    first = await container.agent.reply(cmd())
    assert first.approval

    # A brand-new process: new container, same database, new model provider instance.
    fresh_cloud = ScriptedProvider(["Sent after restart."], name="deepseek", model="cloud-model")
    restarted = build_container(
        settings,
        providers={"reasoning": fresh_cloud, "private": local},
        extra_tools=make_tools(outbox),
    )
    try:
        final = await restarted.agent.resume("owner", first.approval.approval_id, True)
        assert final.content == "Sent after restart." and len(outbox.sent) == 1
    finally:
        await restarted.aclose()


async def test_resume_guards(container: Container, cloud: ScriptedProvider) -> None:
    cloud._queue = [use("mail.send", **SEND), "ok"]
    first = await container.agent.reply(cmd())
    assert first.approval
    with pytest.raises(NotFoundError):
        await container.agent.resume("mallory", first.approval.approval_id, True)
    await container.agent.resume("owner", first.approval.approval_id, True)
    with pytest.raises(ConflictError):  # already decided
        await container.agent.resume("owner", first.approval.approval_id, True)


async def test_approval_resume_streams_events(
    container: Container, cloud: ScriptedProvider
) -> None:
    cloud._queue = [use("mail.send", **SEND), "Done."]
    events = [e async for e in container.agent.stream(cmd())]
    assert isinstance(events[-1], ApprovalRequiredEvent)
    resumed = [
        e async for e in container.agent.resume_stream("owner", events[-1].approval_id, True)
    ]
    kinds = [e.type for e in resumed]
    assert kinds[0] == "run_started" and "tool_result" in kinds and kinds[-1] == "final"
    result = next(e for e in resumed if isinstance(e, ToolResultEvent))
    assert result.status == "ok"


async def test_pending_approvals_listing(container: Container, cloud: ScriptedProvider) -> None:
    cloud._queue = [use("mail.send", **SEND)]
    first = await container.agent.reply(cmd())
    pending = await container.approvals.list_pending("owner")
    assert [a.id for a in pending] == [first.approval.approval_id]  # type: ignore[union-attr]
    assert await container.approvals.list_pending("someone-else") == []


async def test_failed_model_call_marks_run_failed_and_keeps_pending_state_consistent(
    container: Container, cloud: ScriptedProvider
) -> None:
    from gsoi_assistant.core.errors import ProviderAuthError

    cloud._queue = [use("time.now"), ProviderAuthError("nope")]
    with pytest.raises(ProviderAuthError):
        await container.agent.reply(cmd())
    async with container.repo._sf() as s:
        run = (await s.execute(select(models.Run))).scalar_one()
    assert run.status == "failed"


def test_capabilities_import() -> None:
    assert Capabilities().tool_calling is True

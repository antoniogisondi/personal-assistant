from __future__ import annotations

import json

import pytest
from test_pc_tools import FakePc

from gsoi_assistant.agent.service import ChatCommand
from gsoi_assistant.api.container import build_container
from gsoi_assistant.connectors.pc.tools import make_pc_tools
from gsoi_assistant.db.base import Base
from gsoi_assistant.llm.testing import ScriptedProvider
from support import Outbox


@pytest.fixture
async def fast(settings, cloud: ScriptedProvider, local, outbox: Outbox):  # type: ignore[no-untyped-def]
    pc = FakePc()
    c = build_container(
        settings, providers={"reasoning": cloud, "private": local}, extra_tools=make_pc_tools(pc)
    )
    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield c, pc, cloud
    await c.aclose()


def cmd(text: str, channel: str = "voice") -> ChatCommand:
    return ChatCommand(user_id="owner", message=text, profile="reasoning", channel=channel)


async def test_a_clear_command_runs_without_the_language_model(fast) -> None:  # type: ignore[no-untyped-def]
    c, pc, cloud = fast
    r = await c.agent.reply(cmd("apri Chrome"))
    assert r.status == "done" and r.content == "Apro Google Chrome." and r.cost_usd == 0.0
    assert pc.launched == ["Google Chrome"]
    assert cloud.requests == []  # no model call at all: instant and free


async def test_the_fast_path_is_recorded_like_any_other_run(fast) -> None:  # type: ignore[no-untyped-def]
    c, _, _ = fast
    r = await c.agent.reply(cmd("apri Spotify"))
    run = await c.repo.get_run(r.run_id)
    assert run is not None and run.status == "done" and run.finished_at is not None
    calls = await c.tool_calls.for_run(r.run_id)
    assert [(x.tool, x.status, x.decision) for x in calls] == [("pc.open_app", "ok", "allow")]
    assert (await c.audit.verify()).ok
    msgs = await c.repo.history(r.conversation_id, 10)
    assert [m.role for m in msgs] == ["user", "assistant"] and msgs[1].content == "Apro Spotify."


async def test_the_stream_has_the_usual_events(fast) -> None:  # type: ignore[no-untyped-def]
    c, _, _ = fast
    events = [e async for e in c.agent.stream(cmd("apri Chrome"))]
    assert [e.type for e in events] == ["run_started", "tool_call_started", "tool_result", "final"]
    assert events[-1].content == "Apro Google Chrome."  # type: ignore[union-attr]


async def test_an_ambiguous_name_is_left_to_the_model(fast) -> None:  # type: ignore[no-untyped-def]
    c, pc, cloud = fast
    cloud._queue = ["Quale intendi: Edge o Word?"]
    r = await c.agent.reply(cmd("apri microsoft"))
    assert (
        pc.launched == []
        and r.content == "Quale intendi: Edge o Word?"
        and len(cloud.requests) == 1
    )


async def test_an_unknown_program_is_left_to_the_model(fast) -> None:  # type: ignore[no-untyped-def]
    c, pc, cloud = fast
    cloud._queue = ["Non trovo quel programma."]
    r = await c.agent.reply(cmd("apri photoshop"))
    assert pc.launched == [] and r.content == "Non trovo quel programma."


async def test_volume_presses_the_key_several_times(fast) -> None:  # type: ignore[no-untyped-def]
    c, pc, _ = fast
    await c.agent.reply(cmd("alza il volume"))
    assert pc.keys == ["volume_up"] * 5


async def test_time_is_answered_locally(fast) -> None:  # type: ignore[no-untyped-def]
    c, _, cloud = fast
    r = await c.agent.reply(cmd("che ore sono"))
    assert r.content.startswith(("Sono le", "È l'una")) and cloud.requests == []


async def test_readonly_mode_still_blocks_fast_commands(settings, cloud, local) -> None:  # type: ignore[no-untyped-def]
    pc = FakePc()
    c = build_container(
        settings.model_copy(update={"readonly": True}),
        providers={"reasoning": cloud, "private": local},
        extra_tools=make_pc_tools(pc),
    )
    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        cloud._queue = ["Sono in modalità sola lettura."]
        r = await c.agent.reply(cmd("apri Chrome"))
        assert pc.launched == [] and r.content == "Sono in modalità sola lettura."
        calls = await c.tool_calls.for_run(r.run_id)
        assert calls[0].status == "denied"  # the policy was consulted and said no
    finally:
        await c.aclose()


async def test_the_fast_path_can_be_turned_off(settings, cloud, local) -> None:  # type: ignore[no-untyped-def]
    pc = FakePc()
    c = build_container(
        settings.model_copy(update={"fast_commands": False}),
        providers={"reasoning": cloud, "private": local},
        extra_tools=make_pc_tools(pc),
    )
    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        cloud._queue = ["ok"]
        await c.agent.reply(cmd("apri Chrome"))
        assert len(cloud.requests) == 1 and pc.launched == []
    finally:
        await c.aclose()


async def test_normal_conversation_is_unaffected(fast) -> None:  # type: ignore[no-untyped-def]
    c, _, cloud = fast
    cloud._queue = ["Sto bene, grazie!"]
    r = await c.agent.reply(cmd("come stai", channel="text"))
    assert r.content == "Sto bene, grazie!" and json.dumps(r.usage.model_dump())

from __future__ import annotations

import json
from typing import Any

import pytest

from gsoi_assistant.agent.service import ChatCommand
from gsoi_assistant.api.container import Container, build_container
from gsoi_assistant.connectors.pc.backend import AppEntry
from gsoi_assistant.connectors.pc.tools import make_pc_tools
from gsoi_assistant.db.base import Base
from gsoi_assistant.llm.base import ToolCall
from gsoi_assistant.llm.testing import ScriptedProvider, use
from gsoi_assistant.tools.executor import ExecContext
from support import new_run


class FakePc:
    def __init__(self) -> None:
        self.launched: list[str] = []
        self.urls: list[str] = []
        self.folders: list[str] = []
        self.keys: list[str] = []
        names = ("Google Chrome", "Microsoft Edge", "Microsoft Word", "Spotify", "Calcolatrice")
        self._apps = [AppEntry(n, f"shell:AppsFolder\\{n.replace(' ', '')}") for n in names]

    def apps(self) -> list[AppEntry]:
        return self._apps

    def launch(self, app: AppEntry) -> None:
        self.launched.append(app.name)

    def open_url(self, url: str) -> None:
        self.urls.append(url)

    def open_folder(self, which: str) -> None:
        self.folders.append(which)

    def media(self, action: str) -> None:
        self.keys.append(action)


@pytest.fixture
async def pc_container(settings, cloud, local, outbox):  # type: ignore[no-untyped-def]
    pc = FakePc()
    c = build_container(settings, providers={"reasoning": cloud, "private": local},
                        extra_tools=make_pc_tools(pc))  # fmt: skip
    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield c, pc
    await c.aclose()


async def run(
    c: Container, name: str, args: dict[str, Any] | None = None, tainted: bool = False
) -> Any:
    ctx = ExecContext(user_id="owner", run_id=await new_run(c), tainted=tainted)
    return await c.executor.execute(ToolCall(id="1", name=name, arguments=args or {}), ctx)


async def test_open_app_by_spoken_name(pc_container) -> None:  # type: ignore[no-untyped-def]
    c, pc = pc_container
    r = await run(c, "pc.open_app", {"name": "chrome"})
    assert r.status == "ok" and pc.launched == ["Google Chrome"]
    assert json.loads(r.content) == {"launched": "Google Chrome"}


async def test_unknown_or_ambiguous_names_never_launch_anything(pc_container) -> None:  # type: ignore[no-untyped-def]
    c, pc = pc_container
    missing = await run(c, "pc.open_app", {"name": "photoshop"})
    assert missing.status == "error" and "No installed program" in missing.content
    ambiguous = await run(c, "pc.open_app", {"name": "microsoft"})
    assert (
        ambiguous.status == "error"
        and "Microsoft Edge" in ambiguous.content
        and "which one" in ambiguous.content
    )
    assert pc.launched == []


async def test_arbitrary_commands_cannot_be_launched(pc_container) -> None:  # type: ignore[no-untyped-def]
    c, pc = pc_container
    for payload in (
        "cmd.exe /c calc",
        "powershell -enc AAAA",
        "C:\\Windows\\System32\\cmd.exe",
        "..\\..\\evil.exe",
    ):
        r = await run(c, "pc.open_app", {"name": payload})
        assert r.status == "error"
    assert pc.launched == []


async def test_list_apps(pc_container) -> None:  # type: ignore[no-untyped-def]
    c, _ = pc_container
    r = await run(c, "pc.list_apps", {"query": "micro"})
    assert set(json.loads(r.content)["apps"]) == {"Microsoft Edge", "Microsoft Word"}
    everything = json.loads((await run(c, "pc.list_apps")).content)["apps"]
    assert len(everything) == 5


async def test_open_url_only_allows_web_addresses(pc_container) -> None:  # type: ignore[no-untyped-def]
    c, pc = pc_container
    ok = await run(c, "pc.open_url", {"url": "https://www.example.com/a"})
    assert ok.status == "ok" and pc.urls == ["https://www.example.com/a"]
    for bad in ("file:///C:/Windows/win.ini", "javascript:alert(1)", "https://u:p@example.com"):
        r = await run(c, "pc.open_url", {"url": bad})
        assert r.status == "error" and "Cannot open" in r.content
    assert len(pc.urls) == 1


async def test_folders_and_media_are_restricted_to_known_values(pc_container) -> None:  # type: ignore[no-untyped-def]
    c, pc = pc_container
    assert (await run(c, "pc.open_folder", {"folder": "downloads"})).status == "ok"
    assert (await run(c, "pc.open_folder", {"folder": "C:\\Windows"})).status == "error"
    assert (await run(c, "pc.media", {"action": "play_pause"})).status == "ok"
    assert (await run(c, "pc.media", {"action": "format_disk"})).status == "error"
    assert pc.folders == ["downloads"] and pc.keys == ["play_pause"]


async def test_after_untrusted_content_pc_actions_need_the_users_approval(pc_container) -> None:  # type: ignore[no-untyped-def]
    c, pc = pc_container
    r = await run(c, "pc.open_url", {"url": "https://phishing.example/login"}, tainted=True)
    assert r.status == "approval_required" and pc.urls == []
    assert r.approval is not None and "phishing.example" in r.approval.display["summary"]
    app = await run(c, "pc.open_app", {"name": "chrome"}, tainted=True)
    assert app.status == "approval_required" and pc.launched == []


async def test_readonly_mode_blocks_pc_control(settings, cloud, local) -> None:  # type: ignore[no-untyped-def]
    pc = FakePc()
    c = build_container(settings.model_copy(update={"readonly": True}),
                        providers={"reasoning": cloud, "private": local},
                        extra_tools=make_pc_tools(pc))  # fmt: skip
    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        assert (await run(c, "pc.open_app", {"name": "chrome"})).status == "denied"
        assert (await run(c, "pc.list_apps")).status == "ok"
        assert pc.launched == []
    finally:
        await c.aclose()


async def test_voice_command_end_to_end(pc_container, cloud: ScriptedProvider) -> None:  # type: ignore[no-untyped-def]
    c, pc = pc_container
    cloud._queue = [use("pc.open_app", name="Spotify"), "Ho aperto Spotify."]
    res = await c.agent.reply(
        ChatCommand(
            user_id="owner",
            message="mi puoi aprire Spotify, quello della musica, per favore?",
            profile="reasoning",
            channel="voice",
        )
    )
    assert (
        res.status == "done" and res.content == "Ho aperto Spotify." and pc.launched == ["Spotify"]
    )
    assert any(t.name == "pc__open_app" for t in cloud.requests[0].tools)

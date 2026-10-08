"""One Google application ships with the product: end users only press "Collega Google"."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from conftest import TOKEN, make_settings
from gsoi_assistant.api import container as container_module
from gsoi_assistant.api.container import build_container
from gsoi_assistant.api.main import create_app
from gsoi_assistant.connectors.google.hub import load_bundled_app
from gsoi_assistant.db.base import Base
from gsoi_assistant.security.secrets import EnvSecretStore
from support import make_tools

BUNDLED_ID = "bundled-1.apps.googleusercontent.com"
OWN_ID = "own-2.apps.googleusercontent.com"


def test_loader_reads_a_complete_file_only(tmp_path: Path) -> None:
    f = tmp_path / "app.json"
    f.write_text(json.dumps({"client_id": BUNDLED_ID, "client_secret": "GOCSPX-bundled"}))
    loaded = load_bundled_app(f)
    assert (
        loaded is not None
        and loaded[0] == BUNDLED_ID
        and loaded[1].get_secret_value() == "GOCSPX-bundled"
    )
    assert "GOCSPX" not in repr(loaded)  # SecretStr never prints the value

    for content in (
        '{"client_id": "", "client_secret": ""}',
        '{"client_id": "x"}',
        "not json",
        "[]",
    ):
        f.write_text(content)
        assert load_bundled_app(f) is None
    assert load_bundled_app(tmp_path / "missing.json") is None


def test_no_credentials_are_committed_with_the_package() -> None:
    assert load_bundled_app() is None  # a fresh checkout has no bundled app


@pytest.fixture
async def app(tmp_path, outbox, cloud, local, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        container_module, "load_bundled_app", lambda: (BUNDLED_ID, SecretStr("GOCSPX-bundled"))
    )
    s = make_settings(tmp_path / "b.db")
    c = build_container(
        s,
        secrets=EnvSecretStore({}, dotenv_path=None),
        providers={"reasoning": cloud, "private": local},
        extra_tools=make_tools(outbox),
    )
    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await c.hub.load()
    a = create_app(s, c)
    a.state.container = c
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=a),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    yield c, client
    await client.aclose()
    await c.aclose()


async def test_user_only_has_to_press_connect(app) -> None:  # type: ignore[no-untyped-def]
    _, client = app
    st = (await client.get("/v1/connections")).json()[0]
    assert st["configured"] is True and st["config_source"] == "bundled" and not st["connected"]
    url = (await client.post("/v1/connections/google/start")).json()["auth_url"]
    assert BUNDLED_ID in url  # straight to Google's consent screen, nothing to set up


async def test_a_saved_override_wins_and_removing_it_returns_to_the_bundled_app(app) -> None:  # type: ignore[no-untyped-def]
    c, client = app
    await client.put(
        "/v1/connections/google/config",
        json={"client_id": OWN_ID, "client_secret": "GOCSPX-own-secret"},
    )
    assert (await client.get("/v1/connections")).json()[0]["config_source"] == "app"
    assert OWN_ID in (await client.post("/v1/connections/google/start")).json()["auth_url"]

    await c.hub.load()  # restart: the saved override is picked up again
    assert c.hub.client_id == OWN_ID

    await client.delete("/v1/connections/google/config")
    st = (await client.get("/v1/connections")).json()[0]
    assert st["configured"] is True and st["config_source"] == "bundled"
    assert BUNDLED_ID in (await client.post("/v1/connections/google/start")).json()["auth_url"]


async def test_page_leads_with_a_single_button_and_hides_developer_settings(app) -> None:  # type: ignore[no-untyped-def]
    _, client = app
    html = (await client.get("/setup")).text
    assert "Collega Google" in html and "Impostazioni per lo sviluppatore" in html
    assert "<details" in html  # developer form is collapsed by default, not shown to users

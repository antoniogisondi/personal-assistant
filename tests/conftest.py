from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport

from gsoi_assistant.api.container import Container, build_container
from gsoi_assistant.api.main import create_app
from gsoi_assistant.config.settings import ModelProfile, Settings
from gsoi_assistant.db.base import Base
from gsoi_assistant.llm.base import Capabilities
from gsoi_assistant.llm.testing import ScriptedProvider
from gsoi_assistant.security.secrets import EnvSecretStore
from support import Outbox, make_tools

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # Qt without a display (desktop tests)

TOKEN = "test-token-0123456789abcdef"


def make_settings(db_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": "test",
        "database_url": f"sqlite+aiosqlite:///{db_path}",
        "api_token": TOKEN,
        "profiles": {
            "reasoning": ModelProfile(
                provider="deepseek",
                model="cloud-model",
                base_url="https://cloud.invalid/v1",
                api_key_ref="CLOUD_KEY",
                input_cost_per_mtok=1.0,
                output_cost_per_mtok=2.0,
                fallback="private",
            ),
            "private": ModelProfile(
                provider="ollama",
                model="local-model",
                base_url="http://localhost:11434/v1",
                is_local=True,
            ),
        },
        "default_profile": "reasoning",
        "llm_backoff_initial_s": 0.0,
        "data_dir": db_path.parent / "data",
        "log_level": "WARNING",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path / "test.db")


@pytest.fixture
def cloud() -> ScriptedProvider:
    return ScriptedProvider(
        name="deepseek",
        model="cloud-model",
        capabilities=Capabilities(input_cost_per_mtok=1.0, output_cost_per_mtok=2.0),
    )


@pytest.fixture
def local() -> ScriptedProvider:
    return ScriptedProvider(
        name="ollama", model="local-model", capabilities=Capabilities(is_local=True)
    )


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
async def container(
    settings: Settings, cloud: ScriptedProvider, local: ScriptedProvider, outbox: Outbox
) -> AsyncIterator[Container]:
    c = build_container(
        settings, providers={"reasoning": cloud, "private": local}, extra_tools=make_tools(outbox)
    )
    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield c
    await c.aclose()


@pytest.fixture
async def client(settings: Settings, container: Container) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, container)
    app.state.container = container  # lifespan is not run by ASGITransport
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as c:
        yield c


# ---- Google-enabled container -------------------------------------------------------------

MASTER_KEY = Fernet.generate_key().decode()


@pytest.fixture
async def gcontainer(
    tmp_path: Path, cloud: ScriptedProvider, local: ScriptedProvider, outbox: Outbox
) -> AsyncIterator[Container]:
    settings = make_settings(
        tmp_path / "g.db",
        google_client_id="client-id.apps.googleusercontent.com",
        timezone="Europe/Rome",
    )
    secrets = EnvSecretStore(
        {"GOOGLE_CLIENT_SECRET": "client-secret-value", "GSOI_MASTER_KEY": MASTER_KEY},
        dotenv_path=None,
    )
    c = build_container(
        settings,
        secrets=secrets,
        providers={"reasoning": cloud, "private": local},
        extra_tools=make_tools(outbox),
    )
    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield c
    await c.aclose()


@pytest.fixture
async def gclient(gcontainer: Container) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(gcontainer.settings, gcontainer)
    app.state.container = gcontainer
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as c:
        yield c


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated per-user data folder for the desktop app."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("GSOI_DESKTOP_HOME", str(h))
    return h

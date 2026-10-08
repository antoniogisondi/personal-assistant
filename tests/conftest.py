from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from gsoi_assistant.api.container import Container, build_container
from gsoi_assistant.api.main import create_app
from gsoi_assistant.config.settings import ModelProfile, Settings
from gsoi_assistant.db.base import Base
from gsoi_assistant.llm.base import Capabilities
from gsoi_assistant.llm.testing import ScriptedProvider
from support import Outbox, make_tools

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

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from gsoi_assistant.db import models  # noqa: F401
from gsoi_assistant.db.base import Base

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Config, str]:
    db = tmp_path / "mig.db"
    monkeypatch.setenv("GSOI_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg, f"sqlite:///{db}"


def test_upgrade_matches_models_and_downgrades(alembic_cfg: tuple[Config, str]) -> None:
    cfg, sync_url = alembic_cfg
    command.upgrade(cfg, "head")
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == [], f"models and migrations have drifted: {diff}"
        assert {"conversations", "runs", "messages", "model_calls"} <= set(
            inspect(conn).get_table_names()
        )
    command.downgrade(cfg, "base")
    with engine.connect() as conn:
        assert "runs" not in inspect(conn).get_table_names()

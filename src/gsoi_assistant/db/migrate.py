"""Run database migrations from code (used by the desktop app at startup)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config


def migrations_dir() -> Path:
    override = os.environ.get("GSOI_MIGRATIONS_DIR")
    if override:
        return Path(override)
    frozen_root = getattr(sys, "_MEIPASS", None)  # PyInstaller bundle
    if frozen_root:
        return Path(frozen_root) / "migrations"
    return Path(__file__).resolve().parents[3] / "migrations"  # source checkout


def upgrade_to_head(database_url: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir()))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(cfg, "head")

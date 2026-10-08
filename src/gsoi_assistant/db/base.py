from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from gsoi_assistant.core.errors import DatabaseConfigError

# JSONB on PostgreSQL, plain JSON elsewhere (SQLite in unit tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


def check_database_url(url: str) -> None:
    """Fail early, with the absolute path, when a SQLite file cannot be created or opened."""
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):
        return
    name = parsed.database
    if not name or name == ":memory:" or name.startswith("file:"):
        return
    path = Path(name).expanduser().resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            raise DatabaseConfigError(f"'{path}' is a folder; GSOI_DATABASE_URL must name a file")
        sqlite3.connect(path).close()
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseConfigError(
            f"cannot open the SQLite database file '{path}': {exc}. "
            "Check GSOI_DATABASE_URL in .env (example: sqlite+aiosqlite:///./gsoi.db) and that "
            "the folder is writable."
        ) from exc


def make_engine(url: str) -> AsyncEngine:
    check_database_url(url)
    return create_async_engine(url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)

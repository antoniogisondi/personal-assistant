from __future__ import annotations

import asyncio
import os

from alembic import context
from dotenv import dotenv_values
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from gsoi_assistant.db import models  # noqa: F401  (register tables on Base.metadata)
from gsoi_assistant.db.base import Base, check_database_url

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    # Same sources as the app: real environment first, then `.env` in the working directory.
    url = (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("GSOI_DATABASE_URL")
        or dotenv_values(".env").get("GSOI_DATABASE_URL")
    )
    if not url:
        raise RuntimeError("GSOI_DATABASE_URL is not set (environment or .env)")
    check_database_url(url)
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

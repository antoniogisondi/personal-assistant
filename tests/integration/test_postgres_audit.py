"""Runs only against a real PostgreSQL (set GSOI_TEST_POSTGRES_URL, e.g. in CI)."""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from gsoi_assistant.db.base import make_session_factory
from gsoi_assistant.db.stores import AuditStore
from gsoi_assistant.security.audit import AuditLog
from tests_root import ROOT

URL = os.environ.get("GSOI_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="GSOI_TEST_POSTGRES_URL not set")


async def test_audit_log_is_immutable_and_chain_survives_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    assert URL
    monkeypatch.setenv("GSOI_DATABASE_URL", URL)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(URL)
    audit = AuditLog(AuditStore(make_session_factory(engine)))
    await asyncio.gather(
        *(audit.record(user_id="u", actor="system", action="t", subject=str(i)) for i in range(20))
    )
    res = await audit.verify()
    assert res.ok and res.entries == 20  # concurrent writers did not fork the chain

    for stmt in ("UPDATE audit_log SET subject='x'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
        with pytest.raises(Exception, match="append-only"):
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
    await engine.dispose()

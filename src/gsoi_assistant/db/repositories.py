from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gsoi_assistant.core.ids import new_id
from gsoi_assistant.core.records import ModelCallRecord
from gsoi_assistant.core.types import DataClass
from gsoi_assistant.db import models


class SqlRepository:
    """Persistence for conversations, runs, messages and model calls.

    Each operation uses its own short transaction: a run's audit trail must survive
    even if a later step of the same request fails.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def ping(self) -> None:
        async with self._sf() as s:
            await s.execute(text("SELECT 1"))

    # conversations ---------------------------------------------------------

    async def create_conversation(
        self, user_id: str, title: str | None, channel: str = "api"
    ) -> uuid.UUID:
        cid = new_id()
        async with self._sf() as s, s.begin():
            s.add(models.Conversation(id=cid, user_id=user_id, title=title, channel=channel))
        return cid

    async def conversation_exists(self, user_id: str, conversation_id: uuid.UUID) -> bool:
        async with self._sf() as s:
            row = await s.execute(
                select(models.Conversation.id).where(
                    models.Conversation.id == conversation_id,
                    models.Conversation.user_id == user_id,
                )
            )
            return row.scalar_one_or_none() is not None

    async def history(self, conversation_id: uuid.UUID, limit: int) -> list[models.Message]:
        async with self._sf() as s:
            rows = await s.execute(
                select(models.Message)
                .where(models.Message.conversation_id == conversation_id)
                .order_by(models.Message.created_at.desc(), models.Message.id)
                .limit(limit)
            )
            return list(reversed(rows.scalars().all()))

    async def add_message(
        self,
        *,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID | None,
        role: str,
        content: str | None,
        data_class: DataClass = DataClass.PRIVATE,
    ) -> uuid.UUID:
        mid = new_id()
        async with self._sf() as s, s.begin():
            s.add(
                models.Message(
                    id=mid,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    role=role,
                    content=content,
                    data_class=int(data_class),
                )
            )
        return mid

    # runs -------------------------------------------------------------------

    async def create_run(
        self,
        *,
        user_id: str,
        conversation_id: uuid.UUID,
        user_request: str,
        route: dict[str, object],
    ) -> uuid.UUID:
        rid = new_id()
        async with self._sf() as s, s.begin():
            s.add(
                models.Run(
                    id=rid,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    user_request=user_request,
                    route=route,
                )
            )
        return rid

    async def finish_run(
        self, run_id: uuid.UUID, *, status: str, cost_usd: float = 0.0, result: str | None = None
    ) -> None:
        async with self._sf() as s, s.begin():
            run = await s.get(models.Run, run_id)
            if run is None:
                return
            run.status = status
            run.cost_usd = cost_usd
            run.result = result
            run.finished_at = datetime.now(UTC)

    # model calls ------------------------------------------------------------

    async def record(self, record: ModelCallRecord) -> None:
        async with self._sf() as s, s.begin():
            s.add(
                models.ModelCall(
                    id=record.id,
                    run_id=record.run_id,
                    profile=record.profile,
                    provider=record.provider,
                    model=record.model,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    cached_tokens=record.cached_tokens,
                    latency_ms=record.latency_ms,
                    cost_usd=record.cost_usd,
                    data_class_max=int(record.data_class_max),
                    status=record.status,
                    error=record.error,
                )
            )

    # run state (suspend / resume) -------------------------------------------

    async def get_run(self, run_id: uuid.UUID) -> models.Run | None:
        async with self._sf() as s:
            return await s.get(models.Run, run_id)

    async def save_run_state(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        state: dict[str, object] | None,
        tainted: bool,
        cost_usd: float,
    ) -> None:
        async with self._sf() as s, s.begin():
            run = await s.get(models.Run, run_id)
            if run is None:
                return
            run.status = status
            run.state = state
            run.tainted = tainted
            run.cost_usd = cost_usd

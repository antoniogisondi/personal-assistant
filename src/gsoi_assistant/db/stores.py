"""Persistence for the Phase-2 domain: tool calls, approvals, audit chain, notes, tasks."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gsoi_assistant.core.ids import new_id
from gsoi_assistant.db import models

SessionFactory = async_sessionmaker[AsyncSession]
GENESIS_HASH = "0" * 64


def utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class ToolCallStore:
    def __init__(self, sf: SessionFactory) -> None:
        self._sf = sf

    async def record(
        self,
        *,
        run_id: uuid.UUID,
        call_id: str,
        tool: str,
        args_redacted: dict[str, Any] | None,
        args_hash: str | None,
        risk: int | None,
        decision: str,
        approval_id: uuid.UUID | None,
        status: str,
        latency_ms: int,
        error: str | None,
    ) -> None:
        async with self._sf() as s, s.begin():
            s.add(
                models.ToolCall(
                    id=new_id(),
                    run_id=run_id,
                    call_id=call_id,
                    tool=tool,
                    args_redacted=args_redacted,
                    args_hash=args_hash,
                    risk=risk,
                    decision=decision,
                    approval_id=approval_id,
                    status=status,
                    latency_ms=latency_ms,
                    error=error,
                )
            )

    async def for_run(self, run_id: uuid.UUID) -> list[models.ToolCall]:
        async with self._sf() as s:
            rows = await s.execute(
                select(models.ToolCall)
                .where(models.ToolCall.run_id == run_id)
                .order_by(models.ToolCall.created_at, models.ToolCall.id)
            )
            return list(rows.scalars())


class ApprovalStore:
    def __init__(self, sf: SessionFactory) -> None:
        self._sf = sf

    async def create(
        self,
        *,
        user_id: str,
        run_id: uuid.UUID,
        tool: str,
        risk: int,
        strength: str,
        args_hash: str,
        display: dict[str, Any],
        expires_at: datetime,
    ) -> models.Approval:
        row = models.Approval(
            id=new_id(),
            user_id=user_id,
            run_id=run_id,
            tool=tool,
            risk=risk,
            strength=strength,
            args_hash=args_hash,
            display=display,
            status="pending",
            expires_at=expires_at,
        )
        async with self._sf() as s, s.begin():
            s.add(row)
        return row

    async def get(self, approval_id: uuid.UUID) -> models.Approval | None:
        async with self._sf() as s:
            return await s.get(models.Approval, approval_id)

    async def find(
        self, run_id: uuid.UUID, tool: str, args_hash: str, status: str
    ) -> models.Approval | None:
        async with self._sf() as s:
            rows = await s.execute(
                select(models.Approval)
                .where(
                    models.Approval.run_id == run_id,
                    models.Approval.tool == tool,
                    models.Approval.args_hash == args_hash,
                    models.Approval.status == status,
                )
                .order_by(models.Approval.created_at.desc())
                .limit(1)
            )
            return rows.scalar_one_or_none()

    async def transition(
        self, approval_id: uuid.UUID, *, from_status: str, to_status: str, via: str | None = None
    ) -> bool:
        """Atomic compare-and-set. Returns False if the approval was not in `from_status`."""
        values: dict[str, Any] = {"status": to_status, "updated_at": utcnow()}
        if to_status in ("approved", "rejected", "expired"):
            values.update(decided_at=utcnow(), decided_via=via)
        async with self._sf() as s, s.begin():
            result = await s.execute(
                update(models.Approval)
                .where(models.Approval.id == approval_id, models.Approval.status == from_status)
                .values(**values)
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def list_pending(self, user_id: str) -> list[models.Approval]:
        async with self._sf() as s:
            rows = await s.execute(
                select(models.Approval)
                .where(models.Approval.user_id == user_id, models.Approval.status == "pending")
                .order_by(models.Approval.created_at)
            )
            return list(rows.scalars())


HashFn = Callable[[str, dict[str, Any]], str]


class AuditStore:
    def __init__(self, sf: SessionFactory) -> None:
        self._sf = sf

    async def append(
        self,
        *,
        user_id: str,
        actor: str,
        action: str,
        subject: str,
        details: dict[str, Any],
        hash_fn: HashFn,
    ) -> models.AuditEntry:
        """Append one entry, chained to the previous one, inside a single transaction."""
        async with self._sf() as s, s.begin():
            if (
                s.sync_session.get_bind().dialect.name == "postgresql"
            ):  # serialize writers so the chain cannot fork
                await s.execute(text("SELECT pg_advisory_xact_lock(7203001)"))
            last = await s.execute(
                select(models.AuditEntry.hash).order_by(models.AuditEntry.seq.desc()).limit(1)
            )
            prev = last.scalar_one_or_none() or GENESIS_HASH
            entry_id, ts = new_id(), utcnow()
            payload = {
                "id": str(entry_id),
                "ts": ts.isoformat(),
                "user_id": user_id,
                "actor": actor,
                "action": action,
                "subject": subject,
                "details": details,
            }
            entry = models.AuditEntry(
                id=entry_id,
                ts=ts,
                user_id=user_id,
                actor=actor,
                action=action,
                subject=subject,
                details=details,
                prev_hash=prev,
                hash=hash_fn(prev, payload),
            )
            s.add(entry)
        return entry

    async def all(self) -> list[models.AuditEntry]:
        async with self._sf() as s:
            rows = await s.execute(select(models.AuditEntry).order_by(models.AuditEntry.seq))
            return list(rows.scalars())


class NoteStore:
    def __init__(self, sf: SessionFactory) -> None:
        self._sf = sf

    async def create(self, user_id: str, title: str, body: str) -> uuid.UUID:
        nid = new_id()
        async with self._sf() as s, s.begin():
            s.add(models.Note(id=nid, user_id=user_id, title=title, body=body))
        return nid

    async def search(self, user_id: str, query: str, limit: int = 10) -> list[models.Note]:
        like = f"%{query.lower()}%"
        async with self._sf() as s:
            stmt = select(models.Note).where(models.Note.user_id == user_id)
            if query:
                stmt = stmt.where(models.Note.title.ilike(like) | models.Note.body.ilike(like))
            rows = await s.execute(stmt.order_by(models.Note.created_at.desc()).limit(limit))
            return list(rows.scalars())

    async def get(self, user_id: str, note_id: uuid.UUID) -> models.Note | None:
        async with self._sf() as s:
            note = await s.get(models.Note, note_id)
            return note if note is not None and note.user_id == user_id else None

    async def delete(self, user_id: str, note_id: uuid.UUID) -> bool:
        async with self._sf() as s, s.begin():
            note = await s.get(models.Note, note_id)
            if note is None or note.user_id != user_id:
                return False
            await s.delete(note)
            return True


class TaskStore:
    def __init__(self, sf: SessionFactory) -> None:
        self._sf = sf

    async def create(
        self,
        user_id: str,
        title: str,
        notes: str | None,
        due_at: datetime | None,
        source_run_id: uuid.UUID | None,
    ) -> uuid.UUID:
        tid = new_id()
        async with self._sf() as s, s.begin():
            s.add(
                models.Task(
                    id=tid,
                    user_id=user_id,
                    title=title,
                    notes=notes,
                    due_at=due_at,
                    source_run_id=source_run_id,
                )
            )
        return tid

    async def list(self, user_id: str, status: str | None, limit: int = 50) -> list[models.Task]:
        async with self._sf() as s:
            stmt = select(models.Task).where(models.Task.user_id == user_id)
            if status:
                stmt = stmt.where(models.Task.status == status)
            rows = await s.execute(stmt.order_by(models.Task.created_at).limit(limit))
            return list(rows.scalars())

    async def complete(self, user_id: str, task_id: uuid.UUID) -> bool:
        async with self._sf() as s, s.begin():
            task = await s.get(models.Task, task_id)
            if task is None or task.user_id != user_id:
                return False
            task.status = "done"
            return True


class OAuthStore:
    def __init__(self, sf: SessionFactory) -> None:
        self._sf = sf

    async def save_state(
        self, *, state: str, user_id: str, provider: str, code_verifier: str, expires_at: datetime
    ) -> None:
        async with self._sf() as s, s.begin():
            await s.execute(
                delete(models.OAuthState).where(models.OAuthState.expires_at < utcnow())
            )
            s.add(
                models.OAuthState(
                    state=state,
                    user_id=user_id,
                    provider=provider,
                    code_verifier=code_verifier,
                    expires_at=expires_at,
                )
            )

    async def pop_state(self, state: str, provider: str) -> models.OAuthState | None:
        """Atomically consume a pending authorization (single use)."""
        async with self._sf() as s, s.begin():
            row = await s.get(models.OAuthState, state)
            if row is None or row.provider != provider:
                return None
            await s.delete(row)
            return row if _aware(row.expires_at) > utcnow() else None

    async def upsert_credential(
        self, *, user_id: str, provider: str, scopes: list[str], token_enc: str
    ) -> None:
        async with self._sf() as s, s.begin():
            row = (
                await s.execute(
                    select(models.OAuthCredential).where(
                        models.OAuthCredential.user_id == user_id,
                        models.OAuthCredential.provider == provider,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                s.add(
                    models.OAuthCredential(
                        id=new_id(),
                        user_id=user_id,
                        provider=provider,
                        scopes=scopes,
                        token_enc=token_enc,
                        status="active",
                    )
                )
            else:
                row.scopes, row.token_enc, row.status = scopes, token_enc, "active"

    async def get_credential(self, user_id: str, provider: str) -> models.OAuthCredential | None:
        async with self._sf() as s:
            return (
                await s.execute(
                    select(models.OAuthCredential).where(
                        models.OAuthCredential.user_id == user_id,
                        models.OAuthCredential.provider == provider,
                    )
                )
            ).scalar_one_or_none()

    async def update_tokens(self, user_id: str, provider: str, token_enc: str) -> None:
        async with self._sf() as s, s.begin():
            await s.execute(
                update(models.OAuthCredential)
                .where(
                    models.OAuthCredential.user_id == user_id,
                    models.OAuthCredential.provider == provider,
                )
                .values(token_enc=token_enc, updated_at=utcnow())
            )

    async def set_status(self, user_id: str, provider: str, status: str) -> None:
        async with self._sf() as s, s.begin():
            await s.execute(
                update(models.OAuthCredential)
                .where(
                    models.OAuthCredential.user_id == user_id,
                    models.OAuthCredential.provider == provider,
                )
                .values(status=status, updated_at=utcnow())
            )

    async def delete_credential(self, user_id: str, provider: str) -> bool:
        async with self._sf() as s, s.begin():
            result = await s.execute(
                delete(models.OAuthCredential).where(
                    models.OAuthCredential.user_id == user_id,
                    models.OAuthCredential.provider == provider,
                )
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

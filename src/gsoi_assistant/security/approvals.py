"""Human approval for sensitive tool calls.

An approval is bound to the exact arguments (via `args_hash`) of one tool call in one run,
expires, and can be consumed exactly once. If the model changes anything after the user said
yes, the hash differs and a fresh approval is required.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from gsoi_assistant.core.errors import BadRequestError, ConflictError, NotFoundError
from gsoi_assistant.core.redaction import redact
from gsoi_assistant.core.types import Risk
from gsoi_assistant.db import models
from gsoi_assistant.db.stores import ApprovalStore, _aware, utcnow
from gsoi_assistant.security.audit import AuditLog, canonical_json

ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "used"]


def args_hash(tool: str, arguments: dict[str, Any]) -> str:
    return hashlib.sha256(f"{tool}\n{canonical_json(arguments)}".encode()).hexdigest()


class Approval(BaseModel):
    id: uuid.UUID
    user_id: str
    run_id: uuid.UUID
    tool: str
    risk: Risk
    strength: Literal["normal", "strong"]
    args_hash: str
    display: dict[str, Any]
    status: ApprovalStatus
    expires_at: datetime

    @classmethod
    def from_row(cls, row: models.Approval) -> Approval:
        return cls(
            id=row.id,
            user_id=row.user_id,
            run_id=row.run_id,
            tool=row.tool,
            risk=Risk(row.risk),
            strength=row.strength,
            args_hash=row.args_hash,
            display=row.display,
            status=row.status,
            expires_at=_aware(row.expires_at),
        )


class ApprovalService:
    def __init__(self, store: ApprovalStore, audit: AuditLog, *, ttl: timedelta) -> None:
        self._store = store
        self._audit = audit
        self._ttl = ttl

    async def request(
        self,
        *,
        user_id: str,
        run_id: uuid.UUID,
        tool: str,
        risk: Risk,
        strong: bool,
        arguments: dict[str, Any],
        summary: str,
    ) -> Approval:
        digest = args_hash(tool, arguments)
        existing = await self._store.find(run_id, tool, digest, "pending")
        if existing is not None and _aware(existing.expires_at) > utcnow():
            return Approval.from_row(existing)
        row = await self._store.create(
            user_id=user_id,
            run_id=run_id,
            tool=tool,
            risk=int(risk),
            strength="strong" if strong else "normal",
            args_hash=digest,
            # Exactly what will be executed, shown verbatim: never a model-written paraphrase.
            display={"summary": summary, "arguments": redact(arguments)},
            expires_at=utcnow() + self._ttl,
        )
        await self._audit.record(
            user_id=user_id,
            actor="system",
            action="approval.requested",
            subject=f"{tool}",
            details={"approval_id": str(row.id), "run_id": str(run_id), "args_hash": digest},
        )
        return Approval.from_row(row)

    async def decide(
        self,
        *,
        user_id: str,
        approval_id: uuid.UUID,
        approve: bool,
        via: str,
        confirm_tool: str | None = None,
    ) -> Approval:
        row = await self._store.get(approval_id)
        if row is None or row.user_id != user_id:
            raise NotFoundError("approval not found")
        if row.status != "pending":
            raise ConflictError(f"approval already {row.status}")
        if _aware(row.expires_at) <= utcnow():
            await self._store.transition(approval_id, from_status="pending", to_status="expired")
            raise ConflictError("approval expired")
        if approve and row.strength == "strong" and confirm_tool != row.tool:
            raise BadRequestError(
                f"this action is destructive: repeat the tool name '{row.tool}' as confirm_tool"
            )
        target = "approved" if approve else "rejected"
        if not await self._store.transition(
            approval_id, from_status="pending", to_status=target, via=via
        ):
            raise ConflictError("approval already decided")
        await self._audit.record(
            user_id=user_id,
            actor="user",
            action=f"approval.{target}",
            subject=row.tool,
            details={"approval_id": str(approval_id), "run_id": str(row.run_id), "via": via},
        )
        updated = await self._store.get(approval_id)
        if updated is None:  # pragma: no cover - the row was just read
            raise NotFoundError("approval not found")
        return Approval.from_row(updated)

    async def consume_if_approved(
        self, run_id: uuid.UUID, tool: str, digest: str
    ) -> Approval | None:
        """Atomically spend an approved, unexpired approval matching these exact arguments."""
        row = await self._store.find(run_id, tool, digest, "approved")
        if row is None or _aware(row.expires_at) <= utcnow():
            return None
        if not await self._store.transition(row.id, from_status="approved", to_status="used"):
            return None  # lost a race: someone else used it
        return Approval.from_row(row)

    async def get(self, user_id: str, approval_id: uuid.UUID) -> Approval:
        row = await self._store.get(approval_id)
        if row is None or row.user_id != user_id:
            raise NotFoundError("approval not found")
        return Approval.from_row(row)

    async def list_pending(self, user_id: str) -> list[Approval]:
        rows = await self._store.list_pending(user_id)
        return [Approval.from_row(r) for r in rows if _aware(r.expires_at) > utcnow()]

"""Append-only, hash-chained audit trail.

Each entry's hash covers the previous entry's hash, so any later modification or deletion of
history is detectable with `verify()`. (On PostgreSQL a trigger also blocks rewrites.)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from gsoi_assistant.core.redaction import redact
from gsoi_assistant.db.stores import GENESIS_HASH, AuditStore


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def chain_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(payload)).encode()).hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    entries: int
    first_bad_seq: int | None = None


class AuditLog:
    def __init__(self, store: AuditStore) -> None:
        self._store = store

    async def record(
        self,
        *,
        user_id: str,
        actor: str,
        action: str,
        subject: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self._store.append(
            user_id=user_id,
            actor=actor,
            action=action,
            subject=subject[:256],
            details=redact(details or {}),
            hash_fn=chain_hash,
        )

    async def verify(self) -> VerifyResult:
        prev = GENESIS_HASH
        entries = await self._store.all()
        for e in entries:
            payload = {
                "id": str(e.id),
                "ts": e.ts.isoformat() if e.ts.tzinfo else e.ts.isoformat() + "+00:00",
                "user_id": e.user_id,
                "actor": e.actor,
                "action": e.action,
                "subject": e.subject,
                "details": e.details,
            }
            if e.prev_hash != prev or e.hash != chain_hash(prev, payload):
                return VerifyResult(False, len(entries), e.seq)
            prev = e.hash
        return VerifyResult(True, len(entries))

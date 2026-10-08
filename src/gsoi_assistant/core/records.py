from __future__ import annotations

import uuid

from pydantic import BaseModel

from gsoi_assistant.core.types import DataClass


class ModelCallRecord(BaseModel):
    """One LLM call, as persisted for observability and cost tracking."""

    id: uuid.UUID
    run_id: uuid.UUID | None
    profile: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    data_class_max: DataClass = DataClass.PRIVATE
    status: str  # "ok" | "error"
    error: str | None = None

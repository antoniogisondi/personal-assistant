from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from gsoi_assistant.core.events import UsageInfo
from gsoi_assistant.core.types import DataClass


class ChatRequestBody(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: uuid.UUID | None = None
    profile: str | None = Field(
        default=None, description="Model profile; defaults to the configured one."
    )
    data_class: DataClass = Field(
        default=DataClass.PRIVATE,
        description="Sensitivity of this message. SECRET is only ever processed by local models.",
    )


class ChatResponseBody(BaseModel):
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    content: str
    model: str
    usage: UsageInfo
    cost_usd: float


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str | None


class HealthBody(BaseModel):
    status: str

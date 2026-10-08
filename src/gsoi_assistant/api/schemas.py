from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

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
    channel: Literal["text", "voice"] = Field(
        default="text", description="'voice' makes the answer suitable to be read aloud."
    )


class BriefingRequestBody(BaseModel):
    channel: Literal["text", "voice"] = "voice"
    profile: str | None = None


class ApprovalOut(BaseModel):
    approval_id: uuid.UUID
    run_id: uuid.UUID
    tool: str
    risk: str
    strength: str
    display: dict[str, Any]
    expires_at: datetime


class ChatResponseBody(BaseModel):
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    status: str = Field(description="'done' or 'awaiting_approval'")
    content: str
    model: str
    usage: UsageInfo
    cost_usd: float
    approval: ApprovalOut | None = None


class DecisionBody(BaseModel):
    approve: bool
    confirm_tool: str | None = Field(
        default=None, description="For destructive actions: repeat the tool name to confirm."
    )


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str | None


class ToolCallOut(BaseModel):
    tool: str
    status: str
    decision: str
    risk: int | None
    latency_ms: int


class RunOut(BaseModel):
    run_id: uuid.UUID
    status: str
    tainted: bool
    cost_usd: float
    tool_calls: list[ToolCallOut]


class AuditVerifyOut(BaseModel):
    ok: bool
    entries: int
    first_bad_seq: int | None


class HealthBody(BaseModel):
    status: str


class ConnectionOut(BaseModel):
    provider: str
    connected: bool
    status: str
    scopes: list[str]


class ConnectStartOut(BaseModel):
    auth_url: str = Field(description="Open this URL in your browser to grant access.")

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class UsageInfo(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


class RunStarted(BaseModel):
    type: Literal["run_started"] = "run_started"
    run_id: uuid.UUID
    conversation_id: uuid.UUID


class TokenEvent(BaseModel):
    type: Literal["token"] = "token"
    delta: str


class FinalEvent(BaseModel):
    type: Literal["final"] = "final"
    run_id: uuid.UUID
    content: str
    model: str
    usage: UsageInfo
    cost_usd: float


class ToolCallStarted(BaseModel):
    type: Literal["tool_call_started"] = "tool_call_started"
    call_id: str
    tool: str
    arguments: dict[str, Any]


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    tool: str
    status: str  # ok | error | denied | rejected
    summary: str


class ApprovalRequiredEvent(BaseModel):
    type: Literal["approval_required"] = "approval_required"
    run_id: uuid.UUID
    approval_id: uuid.UUID
    tool: str
    risk: str
    strength: str  # normal | strong
    display: dict[str, Any]
    expires_at: datetime


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


AgentEvent = Annotated[
    RunStarted
    | TokenEvent
    | ToolCallStarted
    | ToolResultEvent
    | ApprovalRequiredEvent
    | FinalEvent
    | ErrorEvent,
    Field(discriminator="type"),
]

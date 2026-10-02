from __future__ import annotations

import uuid
from typing import Annotated, Literal

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


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


AgentEvent = Annotated[
    RunStarted | TokenEvent | FinalEvent | ErrorEvent, Field(discriminator="type")
]

"""Serializable run state: lets a run be suspended (waiting for approval) and resumed later,
even after a restart."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from gsoi_assistant.core.types import DataClass
from gsoi_assistant.llm.base import Message, ToolCall


class RunState(BaseModel):
    profile: str
    data_class: DataClass = DataClass.PRIVATE
    messages: list[Message]
    pending_calls: list[ToolCall] = Field(default_factory=list)
    awaiting_approval_id: uuid.UUID | None = None
    steps: int = 0
    tool_calls_made: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    tainted: bool = False
    elapsed_s: float = 0.0
    model: str = ""


class Budget(BaseModel):
    max_steps: int = 12
    max_tool_calls: int = 25
    max_calls_per_step: int = 8
    max_cost_usd: float = 0.50
    deadline_s: float = 120.0

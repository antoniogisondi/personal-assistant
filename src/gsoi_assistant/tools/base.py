"""Tool definitions. A tool is a typed, risk-labelled function the model may *request*;
it is only ever run by the ToolExecutor, after policy and approval checks."""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from gsoi_assistant.core.types import DataClass, Risk
from gsoi_assistant.db.stores import NoteStore, TaskStore

TIn = TypeVar("TIn", bound=BaseModel)
TOut = TypeVar("TOut", bound=BaseModel)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")  # domain.action


@dataclass(frozen=True)
class Services:
    """Everything a tool handler may use. Connectors add their own clients here."""

    notes: NoteStore
    tasks: TaskStore
    timezone: str = "UTC"


@dataclass(frozen=True)
class ToolContext:
    user_id: str
    run_id: uuid.UUID
    services: Services


@dataclass(frozen=True)
class ToolSpec(Generic[TIn, TOut]):
    name: str  # "email.search"
    description: str
    input_model: type[TIn]
    handler: Callable[[TIn, ToolContext], Awaitable[TOut]]
    risk: Risk
    output_data_class: DataClass = DataClass.PRIVATE
    untrusted_output: bool = False  # True when the result contains third-party content
    scopes: tuple[str, ...] = field(default_factory=tuple)
    timeout_s: float = 30.0
    summarize: Callable[[TIn], str] | None = None  # human-readable rendering for approvals

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ValueError(f"invalid tool name '{self.name}' (expected 'domain.action')")

    @property
    def domain(self) -> str:
        return self.name.split(".", 1)[0]

    @property
    def wire_name(self) -> str:
        """Name sent to the model. Providers reject dots in function names."""
        return self.name.replace(".", "__")

    def parameters_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def describe_call(self, args: TIn) -> str:
        return self.summarize(args) if self.summarize else f"{self.name}({args.model_dump_json()})"

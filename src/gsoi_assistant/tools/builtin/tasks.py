from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from gsoi_assistant.core.types import Risk
from gsoi_assistant.tools.base import ToolContext, ToolSpec
from gsoi_assistant.tools.registry import AnyTool


class CreateTaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    notes: str | None = Field(default=None, max_length=5000)
    due_at: datetime | None = Field(default=None, description="ISO 8601 date-time, optional.")


class CreateTaskOut(BaseModel):
    task_id: uuid.UUID


class ListTasksIn(BaseModel):
    status: str | None = Field(default="open", pattern="^(open|done)$")
    limit: int = Field(default=25, ge=1, le=50)


class TaskOut(BaseModel):
    task_id: uuid.UUID
    title: str
    status: str
    due_at: datetime | None
    notes: str | None


class ListTasksOut(BaseModel):
    tasks: list[TaskOut]


class CompleteTaskIn(BaseModel):
    task_id: uuid.UUID


class CompleteTaskOut(BaseModel):
    completed: bool


async def _create(args: CreateTaskIn, ctx: ToolContext) -> CreateTaskOut:
    tid = await ctx.services.tasks.create(
        ctx.user_id, args.title, args.notes, args.due_at, ctx.run_id
    )
    return CreateTaskOut(task_id=tid)


async def _list(args: ListTasksIn, ctx: ToolContext) -> ListTasksOut:
    rows = await ctx.services.tasks.list(ctx.user_id, args.status, args.limit)
    return ListTasksOut(
        tasks=[
            TaskOut(task_id=t.id, title=t.title, status=t.status, due_at=t.due_at, notes=t.notes)
            for t in rows
        ]
    )


async def _complete(args: CompleteTaskIn, ctx: ToolContext) -> CompleteTaskOut:
    return CompleteTaskOut(completed=await ctx.services.tasks.complete(ctx.user_id, args.task_id))


tasks_create = ToolSpec(
    name="tasks.create",
    description="Create a to-do item for the user.",
    input_model=CreateTaskIn,
    handler=_create,
    risk=Risk.WRITE_LOCAL,
    summarize=lambda a: (
        f"Create task “{a.title}”" + (f" due {a.due_at.isoformat()}" if a.due_at else "")
    ),
)
tasks_list = ToolSpec(
    name="tasks.list",
    description="List the user's tasks (open by default).",
    input_model=ListTasksIn,
    handler=_list,
    risk=Risk.READ,
)
tasks_complete = ToolSpec(
    name="tasks.complete",
    description="Mark a task as done.",
    input_model=CompleteTaskIn,
    handler=_complete,
    risk=Risk.WRITE_LOCAL,
    summarize=lambda a: f"Mark task {a.task_id} as done",
)

TASK_TOOLS: list[AnyTool] = [tasks_create, tasks_list, tasks_complete]

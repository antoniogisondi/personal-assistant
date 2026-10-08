from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from gsoi_assistant.core.types import Risk
from gsoi_assistant.tools.base import ToolContext, ToolSpec
from gsoi_assistant.tools.registry import AnyTool


class CreateNoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)


class CreateNoteOut(BaseModel):
    note_id: uuid.UUID


class SearchNotesIn(BaseModel):
    query: str = Field(
        default="", max_length=200, description="Text to look for; empty lists recent notes."
    )
    limit: int = Field(default=10, ge=1, le=25)


class NoteOut(BaseModel):
    note_id: uuid.UUID
    title: str
    body: str


class SearchNotesOut(BaseModel):
    notes: list[NoteOut]


class DeleteNoteIn(BaseModel):
    note_id: uuid.UUID


class DeleteNoteOut(BaseModel):
    deleted: bool


async def _create(args: CreateNoteIn, ctx: ToolContext) -> CreateNoteOut:
    return CreateNoteOut(
        note_id=await ctx.services.notes.create(ctx.user_id, args.title, args.body)
    )


async def _search(args: SearchNotesIn, ctx: ToolContext) -> SearchNotesOut:
    rows = await ctx.services.notes.search(ctx.user_id, args.query, args.limit)
    return SearchNotesOut(
        notes=[NoteOut(note_id=n.id, title=n.title, body=n.body[:2000]) for n in rows]
    )


async def _delete(args: DeleteNoteIn, ctx: ToolContext) -> DeleteNoteOut:
    return DeleteNoteOut(deleted=await ctx.services.notes.delete(ctx.user_id, args.note_id))


notes_create = ToolSpec(
    name="notes.create",
    description="Save a note for the user.",
    input_model=CreateNoteIn,
    handler=_create,
    risk=Risk.WRITE_LOCAL,
    summarize=lambda a: f"Create note “{a.title}”",
)
notes_search = ToolSpec(
    name="notes.search",
    description="Search the user's notes by text.",
    input_model=SearchNotesIn,
    handler=_search,
    risk=Risk.READ,
)
notes_delete = ToolSpec(
    name="notes.delete",
    description="Permanently delete a note by id. Irreversible.",
    input_model=DeleteNoteIn,
    handler=_delete,
    risk=Risk.DESTRUCTIVE,
    summarize=lambda a: f"Permanently delete note {a.note_id}",
)

NOTE_TOOLS: list[AnyTool] = [notes_create, notes_search, notes_delete]

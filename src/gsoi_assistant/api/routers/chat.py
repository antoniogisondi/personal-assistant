from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from sse_starlette import EventSourceResponse, ServerSentEvent

from gsoi_assistant.agent.chat import ChatCommand
from gsoi_assistant.api.deps import ContainerDep, UserDep
from gsoi_assistant.api.schemas import ChatRequestBody, ChatResponseBody, MessageOut
from gsoi_assistant.core.events import UsageInfo

router = APIRouter(prefix="/v1", tags=["chat"])


def _command(body: ChatRequestBody, user_id: str, container: ContainerDep) -> ChatCommand:
    profile = body.profile or container.settings.default_profile
    if profile not in container.settings.profiles:
        raise HTTPException(400, f"unknown profile '{profile}'")
    return ChatCommand(
        user_id=user_id,
        message=body.message,
        conversation_id=body.conversation_id,
        profile=profile,
        data_class=body.data_class,
    )


@router.post("/chat", response_model=ChatResponseBody)
async def chat(
    body: ChatRequestBody, container: ContainerDep, user_id: UserDep
) -> ChatResponseBody:
    result = await container.chat.reply(_command(body, user_id, container))
    return ChatResponseBody(
        run_id=result.run_id,
        conversation_id=result.conversation_id,
        content=result.content,
        model=result.model,
        usage=UsageInfo(**result.usage.model_dump()),
        cost_usd=result.cost_usd,
    )


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequestBody, container: ContainerDep, user_id: UserDep
) -> EventSourceResponse:
    cmd = _command(body, user_id, container)

    async def events() -> AsyncIterator[ServerSentEvent]:
        async for ev in container.chat.reply_stream(cmd):
            yield ServerSentEvent(event=ev.type, data=ev.model_dump_json())

    return EventSourceResponse(events())


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def messages(
    conversation_id: uuid.UUID, container: ContainerDep, user_id: UserDep
) -> list[MessageOut]:
    if not await container.repo.conversation_exists(user_id, conversation_id):
        raise HTTPException(404, "conversation not found")
    rows = await container.repo.history(conversation_id, 500)
    return [MessageOut(id=m.id, role=m.role, content=m.content) for m in rows]

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from sse_starlette import EventSourceResponse, ServerSentEvent

from gsoi_assistant.agent.prompts import BRIEFING_REQUEST
from gsoi_assistant.agent.service import ChatCommand, ChatResult
from gsoi_assistant.api.deps import ContainerDep, UserDep
from gsoi_assistant.api.schemas import (
    ApprovalOut,
    AuditVerifyOut,
    BriefingRequestBody,
    ChatRequestBody,
    ChatResponseBody,
    DecisionBody,
    MessageOut,
    RunOut,
    ToolCallOut,
)
from gsoi_assistant.core.events import AgentEvent

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
        channel=body.channel,
    )


def _body(r: ChatResult) -> ChatResponseBody:
    approval = None
    if r.approval is not None:
        a = r.approval
        approval = ApprovalOut(
            approval_id=a.approval_id,
            run_id=a.run_id,
            tool=a.tool,
            risk=a.risk,
            strength=a.strength,
            display=a.display,
            expires_at=a.expires_at,
        )
    return ChatResponseBody(
        run_id=r.run_id,
        conversation_id=r.conversation_id,
        status=r.status,
        content=r.content,
        model=r.model,
        usage=r.usage,
        cost_usd=r.cost_usd,
        approval=approval,
    )


def _sse(events: AsyncIterator[AgentEvent]) -> EventSourceResponse:
    async def gen() -> AsyncIterator[ServerSentEvent]:
        async for ev in events:
            yield ServerSentEvent(event=ev.type, data=ev.model_dump_json())

    return EventSourceResponse(gen())


@router.post("/chat", response_model=ChatResponseBody)
async def chat(
    body: ChatRequestBody, container: ContainerDep, user_id: UserDep
) -> ChatResponseBody:
    return _body(await container.agent.reply(_command(body, user_id, container)))


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequestBody, container: ContainerDep, user_id: UserDep
) -> EventSourceResponse:
    return _sse(container.agent.stream(_command(body, user_id, container)))


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def messages(
    conversation_id: uuid.UUID, container: ContainerDep, user_id: UserDep
) -> list[MessageOut]:
    if not await container.repo.conversation_exists(user_id, conversation_id):
        raise HTTPException(404, "conversation not found")
    rows = await container.repo.history(conversation_id, 500)
    return [MessageOut(id=m.id, role=m.role, content=m.content) for m in rows]


def _briefing_command(
    body: BriefingRequestBody, user_id: str, container: ContainerDep
) -> ChatCommand:
    profile = body.profile or container.settings.default_profile
    if profile not in container.settings.profiles:
        raise HTTPException(400, f"unknown profile '{profile}'")
    hour = datetime.now(ZoneInfo(container.settings.timezone)).hour
    greeting = "Buongiorno" if hour < 12 else "Buon pomeriggio" if hour < 18 else "Buonasera"
    return ChatCommand(
        user_id=user_id,
        message=BRIEFING_REQUEST.format(greeting=greeting, address=container.settings.user_address),
        profile=profile,
        channel=body.channel,
    )


@router.post("/briefing", response_model=ChatResponseBody)
async def briefing(
    body: BriefingRequestBody, container: ContainerDep, user_id: UserDep
) -> ChatResponseBody:
    """Today's briefing (calendar, email, tasks), phrased for speech by default."""
    return _body(await container.agent.reply(_briefing_command(body, user_id, container)))


@router.post("/briefing/stream")
async def briefing_stream(
    body: BriefingRequestBody, container: ContainerDep, user_id: UserDep
) -> EventSourceResponse:
    return _sse(container.agent.stream(_briefing_command(body, user_id, container)))


# ---- approvals ---------------------------------------------------------------


@router.get("/approvals", response_model=list[ApprovalOut])
async def pending_approvals(container: ContainerDep, user_id: UserDep) -> list[ApprovalOut]:
    return [
        ApprovalOut(
            approval_id=a.id,
            run_id=a.run_id,
            tool=a.tool,
            risk=a.risk.name,
            strength=a.strength,
            display=a.display,
            expires_at=a.expires_at,
        )
        for a in await container.approvals.list_pending(user_id)
    ]


@router.post("/approvals/{approval_id}/decision", response_model=ChatResponseBody)
async def decide(
    approval_id: uuid.UUID, body: DecisionBody, container: ContainerDep, user_id: UserDep
) -> ChatResponseBody:
    """Approve or reject a pending action; the suspended run then continues."""
    return _body(
        await container.agent.resume(user_id, approval_id, body.approve, body.confirm_tool)
    )


@router.post("/approvals/{approval_id}/decision/stream")
async def decide_stream(
    approval_id: uuid.UUID, body: DecisionBody, container: ContainerDep, user_id: UserDep
) -> EventSourceResponse:
    return _sse(
        container.agent.resume_stream(user_id, approval_id, body.approve, body.confirm_tool)
    )


# ---- inspection ---------------------------------------------------------------


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, container: ContainerDep, user_id: UserDep) -> RunOut:
    run = await container.repo.get_run(run_id)
    if run is None or run.user_id != user_id:
        raise HTTPException(404, "run not found")
    calls = await container.tool_calls.for_run(run_id)
    return RunOut(
        run_id=run.id,
        status=run.status,
        tainted=run.tainted,
        cost_usd=run.cost_usd,
        tool_calls=[
            ToolCallOut(
                tool=c.tool,
                status=c.status,
                decision=c.decision,
                risk=c.risk,
                latency_ms=c.latency_ms,
            )
            for c in calls
        ],
    )


@router.get("/audit/verify", response_model=AuditVerifyOut)
async def verify_audit(container: ContainerDep, _: UserDep) -> AuditVerifyOut:
    result = await container.audit.verify()
    return AuditVerifyOut(ok=result.ok, entries=result.entries, first_bad_seq=result.first_bad_seq)

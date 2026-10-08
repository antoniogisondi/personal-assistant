"""AgentService: owns the run lifecycle (create, run, suspend, resume, finish) around AgentLoop."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import structlog
from pydantic import BaseModel

from gsoi_assistant.agent.loop import AgentLoop, LoopContext
from gsoi_assistant.agent.prompts import SYSTEM_PROMPT_V2
from gsoi_assistant.agent.state import RunState
from gsoi_assistant.core.errors import (
    BadRequestError,
    BudgetExceededError,
    ConflictError,
    EgressDeniedError,
    GsoiError,
    LLMError,
    NotFoundError,
    UnknownProfileError,
)
from gsoi_assistant.core.events import (
    AgentEvent,
    ApprovalRequiredEvent,
    ErrorEvent,
    FinalEvent,
    RunStarted,
    ToolResultEvent,
    UsageInfo,
)
from gsoi_assistant.core.redaction import redact_text
from gsoi_assistant.core.types import DataClass
from gsoi_assistant.db.repositories import SqlRepository
from gsoi_assistant.llm.base import Message
from gsoi_assistant.llm.gateway import LLMGateway
from gsoi_assistant.security.approvals import ApprovalService

log = structlog.get_logger(__name__)


class ChatCommand(BaseModel):
    user_id: str
    message: str
    conversation_id: uuid.UUID | None = None
    profile: str
    data_class: DataClass = DataClass.PRIVATE


@dataclass(frozen=True)
class ChatResult:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    status: str  # "done" | "awaiting_approval"
    content: str
    model: str
    usage: UsageInfo
    cost_usd: float
    approval: ApprovalRequiredEvent | None = None


@dataclass(frozen=True)
class _Run:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: str
    data_class: DataClass


def error_code(exc: Exception) -> str:
    table: list[tuple[type[Exception], str]] = [
        (EgressDeniedError, "egress_denied"),
        (BudgetExceededError, "budget_exceeded"),
        (LLMError, "llm_error"),
        (NotFoundError, "not_found"),
        (ConflictError, "conflict"),
        (BadRequestError, "bad_request"),
        (UnknownProfileError, "unknown_profile"),
    ]
    for exc_type, code in table:
        if isinstance(exc, exc_type):
            return code
    return "internal_error"


class AgentService:
    def __init__(
        self,
        *,
        repo: SqlRepository,
        gateway: LLMGateway,
        loop: AgentLoop,
        approvals: ApprovalService,
        history_max_messages: int = 40,
    ) -> None:
        self._repo = repo
        self._gateway = gateway
        self._loop = loop
        self._approvals = approvals
        self._history_max = history_max_messages

    # ---- public API -------------------------------------------------------

    async def reply(self, cmd: ChatCommand) -> ChatResult:
        return await self._collect(self._start(cmd))

    def stream(self, cmd: ChatCommand) -> AsyncIterator[AgentEvent]:
        return self._as_stream(self._start(cmd))

    async def resume(
        self, user_id: str, approval_id: uuid.UUID, approve: bool, confirm_tool: str | None = None
    ) -> ChatResult:
        return await self._collect(self._resume(user_id, approval_id, approve, confirm_tool))

    def resume_stream(
        self, user_id: str, approval_id: uuid.UUID, approve: bool, confirm_tool: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        return self._as_stream(self._resume(user_id, approval_id, approve, confirm_tool))

    # ---- adapters: events -> result / safe stream -------------------------

    @staticmethod
    async def _collect(events: AsyncIterator[AgentEvent]) -> ChatResult:
        run_id = conversation_id = None
        async for ev in events:
            if isinstance(ev, RunStarted):
                run_id, conversation_id = ev.run_id, ev.conversation_id
            elif isinstance(ev, FinalEvent):
                if run_id is None or conversation_id is None:
                    raise RuntimeError("final event before run_started")
                return ChatResult(
                    run_id, conversation_id, "done", ev.content, ev.model, ev.usage, ev.cost_usd
                )
            elif isinstance(ev, ApprovalRequiredEvent):
                if run_id is None or conversation_id is None:
                    raise RuntimeError("approval event before run_started")
                return ChatResult(
                    run_id,
                    conversation_id,
                    "awaiting_approval",
                    "",
                    "",
                    UsageInfo(),
                    0.0,
                    approval=ev,
                )
        raise RuntimeError("agent finished without a result")  # pragma: no cover

    @staticmethod
    async def _as_stream(events: AsyncIterator[AgentEvent]) -> AsyncIterator[AgentEvent]:
        try:
            async for ev in events:
                yield ev
        except GsoiError as exc:
            yield ErrorEvent(code=error_code(exc), message=redact_text(str(exc)))
        except Exception as exc:
            log.exception("agent_stream_failed")
            yield ErrorEvent(code="internal_error", message=redact_text(type(exc).__name__))

    # ---- lifecycle --------------------------------------------------------

    async def _start(self, cmd: ChatCommand) -> AsyncIterator[AgentEvent]:
        # Reject before creating any state if the egress policy would block this anyway.
        self._gateway.check_egress(cmd.profile, cmd.data_class)
        if cmd.conversation_id is not None:
            if not await self._repo.conversation_exists(cmd.user_id, cmd.conversation_id):
                raise NotFoundError("conversation not found")
            conversation_id = cmd.conversation_id
        else:
            conversation_id = await self._repo.create_conversation(cmd.user_id, cmd.message[:60])
        history = await self._repo.history(conversation_id, self._history_max)
        run_id = await self._repo.create_run(
            user_id=cmd.user_id,
            conversation_id=conversation_id,
            user_request=cmd.message,
            route={"kind": "llm", "profile": cmd.profile, "reason": "explicit-profile"},
        )
        structlog.contextvars.bind_contextvars(run_id=str(run_id))
        await self._repo.add_message(
            conversation_id=conversation_id,
            run_id=run_id,
            role="user",
            content=cmd.message,
            data_class=cmd.data_class,
        )
        messages = [Message(role="system", content=SYSTEM_PROMPT_V2)]
        messages += [Message(role=m.role, content=m.content) for m in history]
        messages.append(Message(role="user", content=cmd.message))
        state = RunState(profile=cmd.profile, data_class=cmd.data_class, messages=messages)
        run = _Run(run_id, conversation_id, cmd.user_id, cmd.data_class)
        yield RunStarted(run_id=run_id, conversation_id=conversation_id)
        async for ev in self._drive(run, state):
            yield ev

    async def _resume(
        self, user_id: str, approval_id: uuid.UUID, approve: bool, confirm_tool: str | None
    ) -> AsyncIterator[AgentEvent]:
        approval = await self._approvals.decide(
            user_id=user_id,
            approval_id=approval_id,
            approve=approve,
            via="api",
            confirm_tool=confirm_tool,
        )
        run_row = await self._repo.get_run(approval.run_id)
        if run_row is None or run_row.user_id != user_id or run_row.state is None:
            raise NotFoundError("run not found")
        if run_row.status != "awaiting_approval":
            raise ConflictError(f"run is {run_row.status}, not awaiting approval")
        state = RunState.model_validate(run_row.state)
        if state.awaiting_approval_id != approval.id or not state.pending_calls:
            raise ConflictError("this approval does not match the run's pending action")
        state.awaiting_approval_id = None
        structlog.contextvars.bind_contextvars(run_id=str(approval.run_id))
        if run_row.conversation_id is None:
            raise ConflictError("run has no conversation")
        run = _Run(run_row.id, run_row.conversation_id, user_id, state.data_class)
        yield RunStarted(run_id=run.run_id, conversation_id=run.conversation_id)

        if not approve:
            call = state.pending_calls.pop(0)
            state.messages.append(
                Message(
                    role="tool",
                    tool_call_id=call.id,
                    content=json.dumps(
                        {
                            "error": (
                                "the user rejected this action. "
                                "Do not retry it; acknowledge and move on."
                            )
                        }
                    ),
                )
            )
            yield ToolResultEvent(
                call_id=call.id,
                tool=approval.tool,
                status="rejected",
                summary="rejected by the user",
            )
        await self._repo.save_run_state(
            run.run_id,
            status="running",
            state=state.model_dump(mode="json"),
            tainted=state.tainted,
            cost_usd=state.cost_usd,
        )
        async for ev in self._drive(run, state):
            yield ev

    async def _drive(self, run: _Run, state: RunState) -> AsyncIterator[AgentEvent]:
        """Run the loop and keep the run record consistent with whatever happens."""
        try:
            async for ev in self._loop.run(
                state, LoopContext(user_id=run.user_id, run_id=run.run_id)
            ):
                if isinstance(ev, FinalEvent):
                    await self._repo.add_message(
                        conversation_id=run.conversation_id,
                        run_id=run.run_id,
                        role="assistant",
                        content=ev.content,
                        data_class=run.data_class,
                    )
                    await self._repo.save_run_state(
                        run.run_id,
                        status="done",
                        state=None,
                        tainted=state.tainted,
                        cost_usd=state.cost_usd,
                    )
                    await self._repo.finish_run(
                        run.run_id, status="done", cost_usd=state.cost_usd, result=ev.content
                    )
                yield ev
        except asyncio.CancelledError:
            await asyncio.shield(
                self._repo.finish_run(run.run_id, status="cancelled", cost_usd=state.cost_usd)
            )
            raise
        except Exception as exc:
            log.warning("run_failed", error_type=type(exc).__name__, error=redact_text(str(exc)))
            await self._repo.finish_run(
                run.run_id,
                status="failed",
                cost_usd=state.cost_usd,
                result=redact_text(str(exc))[:500],
            )
            raise

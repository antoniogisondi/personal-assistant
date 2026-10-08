"""Phase-1 conversational service (no tools yet).

Owns the run lifecycle: conversation -> run -> LLM call(s) via the gateway -> persisted result.
The tool-using agent loop (Phase 2) will replace the single LLM call below.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import structlog
from pydantic import BaseModel

from gsoi_assistant.agent.prompts import SYSTEM_PROMPT_V1
from gsoi_assistant.core.errors import (
    EgressDeniedError,
    GsoiError,
    LLMError,
    NotFoundError,
    UnknownProfileError,
)
from gsoi_assistant.core.events import (
    ErrorEvent,
    FinalEvent,
    RunStarted,
    TokenEvent,
    UsageInfo,
)
from gsoi_assistant.core.redaction import redact_text
from gsoi_assistant.core.types import DataClass
from gsoi_assistant.db.repositories import SqlRepository
from gsoi_assistant.llm.base import ChatRequest, Message, Usage
from gsoi_assistant.llm.gateway import CallContext, LLMGateway

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
    content: str
    model: str
    usage: Usage
    cost_usd: float


def error_code(exc: Exception) -> str:
    if isinstance(exc, EgressDeniedError):
        return "egress_denied"
    if isinstance(exc, LLMError):
        return "llm_error"
    if isinstance(exc, NotFoundError):
        return "not_found"
    if isinstance(exc, UnknownProfileError):
        return "unknown_profile"
    return "internal_error"


class ChatService:
    def __init__(
        self, *, repo: SqlRepository, gateway: LLMGateway, history_max_messages: int = 40
    ) -> None:
        self._repo = repo
        self._gateway = gateway
        self._history_max = history_max_messages

    async def _prepare(self, cmd: ChatCommand) -> tuple[uuid.UUID, uuid.UUID, ChatRequest]:
        # Fail before creating any state if the policy would reject the request anyway.
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
            route={
                "kind": "cloud_or_local_llm",
                "profile": cmd.profile,
                "reason": "phase1-explicit",
            },
        )
        structlog.contextvars.bind_contextvars(run_id=str(run_id))
        await self._repo.add_message(
            conversation_id=conversation_id,
            run_id=run_id,
            role="user",
            content=cmd.message,
            data_class=cmd.data_class,
        )
        messages = [Message(role="system", content=SYSTEM_PROMPT_V1)]
        messages += [Message(role=m.role, content=m.content) for m in history]
        messages.append(Message(role="user", content=cmd.message))
        return conversation_id, run_id, ChatRequest(messages=messages)

    async def reply(self, cmd: ChatCommand) -> ChatResult:
        conversation_id, run_id, req = await self._prepare(cmd)
        try:
            resp = await self._gateway.chat(
                cmd.profile, req, CallContext(run_id=run_id, data_class=cmd.data_class)
            )
        except asyncio.CancelledError:
            await asyncio.shield(self._repo.finish_run(run_id, status="cancelled"))
            raise
        except Exception as exc:
            await self._fail(run_id, exc)
            raise
        content = resp.message.content or ""
        await self._repo.add_message(
            conversation_id=conversation_id,
            run_id=run_id,
            role="assistant",
            content=content,
            data_class=cmd.data_class,
        )
        await self._repo.finish_run(run_id, status="done", cost_usd=resp.cost_usd, result=content)
        return ChatResult(run_id, conversation_id, content, resp.model, resp.usage, resp.cost_usd)

    async def reply_stream(
        self, cmd: ChatCommand
    ) -> AsyncIterator[RunStarted | TokenEvent | FinalEvent | ErrorEvent]:
        try:
            conversation_id, run_id, req = await self._prepare(cmd)
        except GsoiError as exc:
            yield ErrorEvent(code=error_code(exc), message=redact_text(str(exc)))
            return
        yield RunStarted(run_id=run_id, conversation_id=conversation_id)
        parts: list[str] = []
        usage = Usage()
        model = ""
        cost = 0.0
        try:
            async for chunk in self._gateway.stream(
                cmd.profile, req, CallContext(run_id=run_id, data_class=cmd.data_class)
            ):
                if chunk.delta:
                    parts.append(chunk.delta)
                    yield TokenEvent(delta=chunk.delta)
                if chunk.usage is not None:
                    usage = chunk.usage
                    cost = chunk.cost_usd or 0.0
                    model = chunk.model or model
        except asyncio.CancelledError:
            await asyncio.shield(self._repo.finish_run(run_id, status="cancelled"))
            raise
        except Exception as exc:
            await self._fail(run_id, exc)
            yield ErrorEvent(code=error_code(exc), message=redact_text(str(exc)))
            return
        content = "".join(parts)
        await self._repo.add_message(
            conversation_id=conversation_id,
            run_id=run_id,
            role="assistant",
            content=content,
            data_class=cmd.data_class,
        )
        await self._repo.finish_run(run_id, status="done", cost_usd=cost, result=content)
        yield FinalEvent(
            run_id=run_id,
            content=content,
            model=model,
            usage=UsageInfo(**usage.model_dump()),
            cost_usd=cost,
        )

    async def _fail(self, run_id: uuid.UUID, exc: Exception) -> None:
        log.warning("run_failed", error_type=type(exc).__name__, error=redact_text(str(exc)))
        await self._repo.finish_run(run_id, status="failed", result=redact_text(str(exc))[:500])

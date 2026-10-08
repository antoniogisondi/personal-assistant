"""ToolExecutor: the only place where tools run.

Pipeline for every call: lookup -> validate -> policy -> (approval) -> execute with timeout
-> truncate / mark untrusted -> record (tool_calls + audit). Nothing about this pipeline can be
influenced by model output beyond the tool name and its (validated) arguments.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Literal

import structlog
from pydantic import ValidationError

from gsoi_assistant.core.errors import ToolError
from gsoi_assistant.core.redaction import redact, redact_text
from gsoi_assistant.core.types import Risk
from gsoi_assistant.db.stores import ToolCallStore
from gsoi_assistant.llm.base import ToolCall
from gsoi_assistant.security.approvals import Approval, ApprovalService, args_hash
from gsoi_assistant.security.audit import AuditLog
from gsoi_assistant.security.policy import Effect, PolicyEngine, PolicyInput
from gsoi_assistant.security.untrusted import wrap_untrusted
from gsoi_assistant.tools.base import Services, ToolContext
from gsoi_assistant.tools.registry import ToolRegistry

log = structlog.get_logger(__name__)

ResultStatus = Literal["ok", "error", "denied", "approval_required"]


@dataclass(frozen=True)
class ExecContext:
    user_id: str
    run_id: uuid.UUID
    tainted: bool = False


@dataclass(frozen=True)
class ExecResult:
    status: ResultStatus
    content: str  # what the model sees
    tainted: bool = False  # this result introduced untrusted content
    approval: Approval | None = None
    risk: Risk | None = None


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


class ToolExecutor:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PolicyEngine,
        approvals: ApprovalService,
        audit: AuditLog,
        tool_calls: ToolCallStore,
        services: Services,
        max_output_chars: int = 8000,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._approvals = approvals
        self._audit = audit
        self._tool_calls = tool_calls
        self._services = services
        self._max_output = max_output_chars

    async def execute(self, call: ToolCall, ctx: ExecContext) -> ExecResult:
        started = time.perf_counter()
        spec = self._registry.resolve(call.name)
        if spec is None:
            return await self._finish(
                call,
                ctx,
                started,
                "error",
                _error(f"unknown tool '{call.name}'"),
                tool=call.name,
                decision="deny",
            )
        tool = spec.name
        if call.arguments_error:
            return await self._finish(
                call,
                ctx,
                started,
                "error",
                _error(f"invalid arguments: {call.arguments_error}"),
                tool=tool,
                risk=spec.risk,
                decision="deny",
            )
        try:
            args = spec.input_model.model_validate(call.arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
            )
            return await self._finish(
                call,
                ctx,
                started,
                "error",
                _error(f"invalid arguments: {problems}"),
                tool=tool,
                risk=spec.risk,
                decision="deny",
            )
        arguments = args.model_dump(mode="json")
        digest = args_hash(tool, arguments)

        decision = self._policy.evaluate(
            PolicyInput(tool=tool, risk=spec.risk, tainted=ctx.tainted)
        )
        approval_id: uuid.UUID | None = None

        if decision.effect == Effect.DENY:
            return await self._finish(
                call,
                ctx,
                started,
                "denied",
                _error(f"not allowed: {decision.reason}"),
                tool=tool,
                risk=spec.risk,
                decision="deny",
                args=arguments,
                digest=digest,
            )

        if decision.effect in (Effect.REQUIRE_APPROVAL, Effect.REQUIRE_STRONG_APPROVAL):
            used = await self._approvals.consume_if_approved(ctx.run_id, tool, digest)
            if used is None:
                approval = await self._approvals.request(
                    user_id=ctx.user_id,
                    run_id=ctx.run_id,
                    tool=tool,
                    risk=spec.risk,
                    strong=decision.effect == Effect.REQUIRE_STRONG_APPROVAL,
                    arguments=arguments,
                    summary=spec.describe_call(args),
                )
                return await self._finish(
                    call,
                    ctx,
                    started,
                    "approval_required",
                    _error("waiting for the user's approval"),
                    tool=tool,
                    risk=spec.risk,
                    decision="require_approval",
                    args=arguments,
                    digest=digest,
                    approval=approval,
                )
            approval_id = used.id

        # --- run the tool ---------------------------------------------------
        tctx = ToolContext(user_id=ctx.user_id, run_id=ctx.run_id, services=self._services)
        try:
            output = await asyncio.wait_for(spec.handler(args, tctx), timeout=spec.timeout_s)
        except TimeoutError:
            return await self._finish(
                call,
                ctx,
                started,
                "error",
                _error("the tool timed out"),
                tool=tool,
                risk=spec.risk,
                decision="allow",
                args=arguments,
                digest=digest,
                approval_id=approval_id,
                error="timeout",
            )
        except ToolError as exc:  # expected, user-presentable failure (e.g. account not connected)
            return await self._finish(
                call,
                ctx,
                started,
                "error",
                _error(str(exc)),
                tool=tool,
                risk=spec.risk,
                decision="allow",
                args=arguments,
                digest=digest,
                approval_id=approval_id,
                error=redact_text(str(exc))[:300],
            )
        except Exception as exc:  # tool bugs must never crash the run, nor leak internals
            log.exception("tool_failed", tool=tool)
            return await self._finish(
                call,
                ctx,
                started,
                "error",
                _error("the tool failed"),
                tool=tool,
                risk=spec.risk,
                decision="allow",
                args=arguments,
                digest=digest,
                approval_id=approval_id,
                error=redact_text(f"{type(exc).__name__}: {exc}")[:300],
            )

        text = output.model_dump_json()
        if len(text) > self._max_output:
            text = (
                text[: self._max_output] + f'..."[truncated {len(text) - self._max_output} chars]'
            )
        if spec.untrusted_output:
            text = wrap_untrusted(f"tool:{tool}", text)
        return await self._finish(
            call,
            ctx,
            started,
            "ok",
            text,
            tool=tool,
            risk=spec.risk,
            decision="allow",
            args=arguments,
            digest=digest,
            approval_id=approval_id,
            tainted=spec.untrusted_output,
        )

    async def _finish(
        self,
        call: ToolCall,
        ctx: ExecContext,
        started: float,
        status: ResultStatus,
        content: str,
        *,
        tool: str,
        decision: str,
        risk: Risk | None = None,
        args: dict[str, object] | None = None,
        digest: str | None = None,
        approval: Approval | None = None,
        approval_id: uuid.UUID | None = None,
        tainted: bool = False,
        error: str | None = None,
    ) -> ExecResult:
        latency = int((time.perf_counter() - started) * 1000)
        approval_ref = approval.id if approval else approval_id
        await self._tool_calls.record(
            run_id=ctx.run_id,
            call_id=call.id,
            tool=tool,
            args_redacted=redact(args) if args is not None else None,
            args_hash=digest,
            risk=int(risk) if risk is not None else None,
            decision=decision,
            approval_id=approval_ref,
            status=status,
            latency_ms=latency,
            error=error or (content if status in ("error", "denied") else None),
        )
        await self._audit.record(
            user_id=ctx.user_id,
            actor="agent",
            action=f"tool.{status}",
            subject=tool,
            details={
                "run_id": str(ctx.run_id),
                "call_id": call.id,
                "risk": risk.name if risk is not None else None,
                "decision": decision,
                "args_hash": digest,
                "approval_id": str(approval_ref) if approval_ref else None,
                "tainted_run": ctx.tainted,
            },
        )
        return ExecResult(
            status=status, content=content, tainted=tainted, approval=approval, risk=risk
        )

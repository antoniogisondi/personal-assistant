"""The agent loop: the model decides, the executor acts.

    drain pending tool calls -> (budget check) -> ask the model -> repeat until a plain answer

The loop never runs a tool itself and never decides authorization; both belong to the
ToolExecutor. When a call needs the user's approval the loop persists its state and stops;
`AgentService.resume` continues it later.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from gsoi_assistant.agent.state import Budget, RunState
from gsoi_assistant.core.errors import BudgetExceededError
from gsoi_assistant.core.events import (
    AgentEvent,
    ApprovalRequiredEvent,
    FinalEvent,
    TokenEvent,
    ToolCallStarted,
    ToolResultEvent,
    UsageInfo,
)
from gsoi_assistant.core.redaction import redact
from gsoi_assistant.db.repositories import SqlRepository
from gsoi_assistant.llm.base import ChatRequest, Message, ToolDef, tool_call_from_parts
from gsoi_assistant.llm.gateway import CallContext, LLMGateway
from gsoi_assistant.tools.executor import ExecContext, ToolExecutor
from gsoi_assistant.tools.registry import ToolRegistry


@dataclass(frozen=True)
class LoopContext:
    user_id: str
    run_id: uuid.UUID


class AgentLoop:
    def __init__(
        self,
        *,
        gateway: LLMGateway,
        executor: ToolExecutor,
        registry: ToolRegistry,
        repo: SqlRepository,
        budget: Budget,
    ) -> None:
        self._gateway = gateway
        self._executor = executor
        self._registry = registry
        self._repo = repo
        self._budget = budget

    def _tool_defs(self, profile: str) -> list[ToolDef]:
        if not self._gateway.supports_tools(profile):
            return []
        return [
            ToolDef(name=t.wire_name, description=t.description, parameters=t.parameters_schema())
            for t in self._registry.all()
        ]

    async def run(self, state: RunState, rc: LoopContext) -> AsyncIterator[AgentEvent]:
        started = time.monotonic()
        base_elapsed = state.elapsed_s
        tools = self._tool_defs(state.profile)

        def tick() -> None:
            state.elapsed_s = base_elapsed + (time.monotonic() - started)

        while True:
            # 1. Execute (or resume) the tool calls the model asked for.
            while state.pending_calls:
                call = state.pending_calls[0]
                spec = self._registry.resolve(call.name)
                tool_name = spec.name if spec else call.name
                yield ToolCallStarted(
                    call_id=call.id, tool=tool_name, arguments=redact(call.arguments)
                )
                result = await self._executor.execute(
                    call, ExecContext(user_id=rc.user_id, run_id=rc.run_id, tainted=state.tainted)
                )
                if result.status == "approval_required" and result.approval is not None:
                    state.awaiting_approval_id = result.approval.id
                    tick()
                    await self._save(state, rc, "awaiting_approval")
                    a = result.approval
                    yield ApprovalRequiredEvent(
                        run_id=rc.run_id,
                        approval_id=a.id,
                        tool=a.tool,
                        risk=a.risk.name,
                        strength=a.strength,
                        display=a.display,
                        expires_at=a.expires_at,
                    )
                    return
                state.pending_calls.pop(0)
                state.tool_calls_made += 1
                state.tainted = state.tainted or result.tainted
                state.messages.append(
                    Message(role="tool", content=result.content, tool_call_id=call.id)
                )
                yield ToolResultEvent(
                    call_id=call.id,
                    tool=tool_name,
                    status=result.status,
                    summary=result.content[:200],
                )

            # 2. Budget.
            tick()
            self._check_budget(state)

            # 3. Ask the model.
            parts: list[str] = []
            acc: dict[int, dict[str, Any]] = {}
            async for chunk in self._gateway.stream(
                state.profile,
                ChatRequest(messages=state.messages, tools=tools),
                CallContext(run_id=rc.run_id, data_class=state.data_class),
            ):
                if chunk.delta:
                    parts.append(chunk.delta)
                    yield TokenEvent(delta=chunk.delta)
                if chunk.tool_call_delta:
                    d = chunk.tool_call_delta
                    slot = acc.setdefault(
                        int(d.get("index") or 0), {"id": None, "name": "", "args": []}
                    )
                    slot["id"] = d.get("id") or slot["id"]
                    slot["name"] += d.get("name") or ""
                    slot["args"].append(d.get("arguments_delta") or "")
                if chunk.usage is not None:
                    state.input_tokens += chunk.usage.input_tokens
                    state.output_tokens += chunk.usage.output_tokens
                    state.cached_tokens += chunk.usage.cached_tokens
                    state.cost_usd += chunk.cost_usd or 0.0
                    state.model = chunk.model or state.model
            state.steps += 1

            calls = [
                tool_call_from_parts(s["id"], s["name"], "".join(s["args"]))
                for _, s in sorted(acc.items())
            ]
            content = "".join(parts)
            state.messages.append(
                Message(role="assistant", content=content or None, tool_calls=calls)
            )

            if not calls:
                tick()
                yield FinalEvent(
                    run_id=rc.run_id,
                    content=content,
                    model=state.model,
                    usage=UsageInfo(
                        input_tokens=state.input_tokens,
                        output_tokens=state.output_tokens,
                        cached_tokens=state.cached_tokens,
                    ),
                    cost_usd=state.cost_usd,
                )
                return

            for extra in calls[self._budget.max_calls_per_step :]:
                extra.arguments_error = "too many tool calls in one step; ask for fewer"
            state.pending_calls = list(calls)

    def _check_budget(self, state: RunState) -> None:
        b = self._budget
        if state.steps >= b.max_steps:
            raise BudgetExceededError(f"step limit reached ({b.max_steps})")
        if state.tool_calls_made >= b.max_tool_calls:
            raise BudgetExceededError(f"tool call limit reached ({b.max_tool_calls})")
        if state.cost_usd >= b.max_cost_usd:
            raise BudgetExceededError(f"cost limit reached (${b.max_cost_usd:.2f})")
        if state.elapsed_s >= b.deadline_s:
            raise BudgetExceededError(f"time limit reached ({b.deadline_s:.0f}s)")

    async def _save(self, state: RunState, rc: LoopContext, status: str) -> None:
        await self._repo.save_run_state(
            rc.run_id,
            status=status,
            state=state.model_dump(mode="json"),
            tainted=state.tainted,
            cost_usd=state.cost_usd,
        )


__all__ = ["AgentLoop", "LoopContext"]

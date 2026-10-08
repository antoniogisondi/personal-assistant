"""Composition root: builds and owns all long-lived objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from gsoi_assistant.agent.loop import AgentLoop
from gsoi_assistant.agent.service import AgentService
from gsoi_assistant.agent.state import Budget
from gsoi_assistant.config.settings import Settings
from gsoi_assistant.db.base import make_engine, make_session_factory
from gsoi_assistant.db.repositories import SqlRepository
from gsoi_assistant.db.stores import ApprovalStore, AuditStore, NoteStore, TaskStore, ToolCallStore
from gsoi_assistant.llm.base import LLMProvider
from gsoi_assistant.llm.gateway import LLMGateway
from gsoi_assistant.llm.providers.factory import build_provider
from gsoi_assistant.security.approvals import ApprovalService
from gsoi_assistant.security.audit import AuditLog
from gsoi_assistant.security.policy import PolicyEngine, load_policy_config
from gsoi_assistant.security.secrets import EnvSecretStore, SecretStore
from gsoi_assistant.tools.base import Services
from gsoi_assistant.tools.builtin import BUILTIN_TOOLS
from gsoi_assistant.tools.executor import ToolExecutor
from gsoi_assistant.tools.registry import AnyTool, ToolRegistry


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    repo: SqlRepository
    providers: dict[str, LLMProvider]
    gateway: LLMGateway
    registry: ToolRegistry
    audit: AuditLog
    approvals: ApprovalService
    tool_calls: ToolCallStore
    executor: ToolExecutor
    agent: AgentService

    async def aclose(self) -> None:
        for provider in self.providers.values():
            await provider.aclose()
        await self.engine.dispose()


def build_container(
    settings: Settings,
    *,
    secrets: SecretStore | None = None,
    providers: dict[str, LLMProvider] | None = None,
    extra_tools: list[AnyTool] | None = None,
) -> Container:
    """`providers` / `extra_tools` let tests inject fakes; production builds from settings."""
    secrets = secrets or EnvSecretStore()
    engine = make_engine(settings.database_url.get_secret_value())
    sf = make_session_factory(engine)
    repo = SqlRepository(sf)
    built = providers or {
        name: build_provider(name, profile, secrets) for name, profile in settings.profiles.items()
    }
    gateway = LLMGateway(
        profiles=settings.profiles,
        providers=built,
        recorder=repo,
        max_attempts=settings.llm_max_attempts,
        backoff_initial_s=settings.llm_backoff_initial_s,
    )

    registry = ToolRegistry([*BUILTIN_TOOLS, *(extra_tools or [])])
    audit = AuditLog(AuditStore(sf))
    approvals = ApprovalService(
        ApprovalStore(sf), audit, ttl=timedelta(hours=settings.approval_ttl_hours)
    )
    tool_calls = ToolCallStore(sf)
    policy = PolicyEngine(load_policy_config(settings.policy_path), readonly=settings.readonly)
    executor = ToolExecutor(
        registry=registry,
        policy=policy,
        approvals=approvals,
        audit=audit,
        tool_calls=tool_calls,
        services=Services(notes=NoteStore(sf), tasks=TaskStore(sf), timezone=settings.timezone),
        max_output_chars=settings.tool_output_max_chars,
    )
    loop = AgentLoop(
        gateway=gateway,
        executor=executor,
        registry=registry,
        repo=repo,
        budget=Budget(
            max_steps=settings.agent_max_steps,
            max_tool_calls=settings.agent_max_tool_calls,
            max_calls_per_step=settings.agent_max_calls_per_step,
            max_cost_usd=settings.agent_max_cost_usd,
            deadline_s=settings.agent_deadline_s,
        ),
    )
    agent = AgentService(
        repo=repo,
        gateway=gateway,
        loop=loop,
        approvals=approvals,
        history_max_messages=settings.history_max_messages,
    )
    return Container(
        settings,
        engine,
        repo,
        built,
        gateway,
        registry,
        audit,
        approvals,
        tool_calls,
        executor,
        agent,
    )

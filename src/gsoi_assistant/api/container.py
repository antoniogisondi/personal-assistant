"""Composition root: builds and owns all long-lived objects."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from gsoi_assistant.agent.chat import ChatService
from gsoi_assistant.config.settings import Settings
from gsoi_assistant.db.base import make_engine, make_session_factory
from gsoi_assistant.db.repositories import SqlRepository
from gsoi_assistant.llm.base import LLMProvider
from gsoi_assistant.llm.gateway import LLMGateway
from gsoi_assistant.llm.providers.factory import build_provider
from gsoi_assistant.security.secrets import EnvSecretStore, SecretStore


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    repo: SqlRepository
    providers: dict[str, LLMProvider]
    gateway: LLMGateway
    chat: ChatService

    async def aclose(self) -> None:
        for provider in self.providers.values():
            await provider.aclose()
        await self.engine.dispose()


def build_container(
    settings: Settings,
    *,
    secrets: SecretStore | None = None,
    providers: dict[str, LLMProvider] | None = None,
) -> Container:
    """`providers` lets tests inject fakes; production builds them from profiles."""
    secrets = secrets or EnvSecretStore()
    engine = make_engine(settings.database_url.get_secret_value())
    repo = SqlRepository(make_session_factory(engine))
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
    chat = ChatService(
        repo=repo, gateway=gateway, history_max_messages=settings.history_max_messages
    )
    return Container(settings, engine, repo, built, gateway, chat)

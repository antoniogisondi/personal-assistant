"""LLMGateway: the single entry point to language models.

Responsibilities: provider lookup by *profile*, egress policy (data classification),
capability checks, retries with backoff, fallback to another profile, cost computation,
and recording every call. Nothing else in the system talks to a provider directly.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Protocol

import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from gsoi_assistant.config.settings import ModelProfile
from gsoi_assistant.core.errors import (
    CapabilityError,
    EgressDeniedError,
    LLMError,
    RetryableLLMError,
    UnknownProfileError,
)
from gsoi_assistant.core.ids import new_id
from gsoi_assistant.core.records import ModelCallRecord
from gsoi_assistant.core.redaction import redact_text
from gsoi_assistant.core.types import DataClass
from gsoi_assistant.llm.base import ChatRequest, ChatResponse, LLMProvider, StreamChunk, Usage

log = structlog.get_logger(__name__)


class CallRecorder(Protocol):
    async def record(self, record: ModelCallRecord) -> None: ...


class NullRecorder:
    async def record(self, record: ModelCallRecord) -> None:
        return None


@dataclass(frozen=True)
class CallContext:
    run_id: uuid.UUID | None = None
    data_class: DataClass = DataClass.PRIVATE


class LLMGateway:
    def __init__(
        self,
        *,
        profiles: Mapping[str, ModelProfile],
        providers: Mapping[str, LLMProvider],
        recorder: CallRecorder | None = None,
        max_attempts: int = 3,
        backoff_initial_s: float = 0.5,
    ) -> None:
        self._profiles = profiles
        self._providers = providers
        self._recorder = recorder or NullRecorder()
        self._max_attempts = max_attempts
        self._backoff = backoff_initial_s

    # ---- policy -----------------------------------------------------------

    def _provider(self, profile: str) -> LLMProvider:
        try:
            return self._providers[profile]
        except KeyError:
            raise UnknownProfileError(profile) from None

    def check_egress(self, profile: str, data_class: DataClass) -> None:
        """Block (not warn) when data is too sensitive for a non-local provider.

        SECRET data never leaves local models, regardless of configuration.
        """
        if self._provider(profile).capabilities.is_local:
            return
        if data_class >= DataClass.SECRET:
            raise EgressDeniedError(
                f"{data_class.name} data cannot be sent to cloud profile '{profile}'"
            )

    @staticmethod
    def _check_capabilities(provider: LLMProvider, req: ChatRequest) -> None:
        if req.tools and not provider.capabilities.tool_calling:
            raise CapabilityError(f"model '{provider.model}' does not support tool calling")
        if req.response_schema is not None and not provider.capabilities.structured_output:
            raise CapabilityError(f"model '{provider.model}' does not support structured output")

    def _fallback_for(self, profile: str, ctx: CallContext, tried: set[str]) -> str | None:
        fb = self._profiles[profile].fallback
        if fb is None or fb in tried or fb not in self._providers:
            return None
        try:
            self.check_egress(fb, ctx.data_class)
        except EgressDeniedError:
            log.warning("fallback_blocked_by_egress", profile=profile, fallback=fb)
            return None
        return fb

    def _cost(self, provider: LLMProvider, usage: Usage) -> float:
        caps = provider.capabilities
        return round(
            usage.input_tokens * caps.input_cost_per_mtok / 1_000_000
            + usage.output_tokens * caps.output_cost_per_mtok / 1_000_000,
            6,
        )

    async def _record(
        self,
        *,
        profile: str,
        provider: LLMProvider,
        ctx: CallContext,
        started: float,
        usage: Usage | None,
        cost: float,
        error: Exception | None,
    ) -> None:
        usage = usage or Usage()
        record = ModelCallRecord(
            id=new_id(),
            run_id=ctx.run_id,
            profile=profile,
            provider=provider.name,
            model=provider.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
            cost_usd=cost,
            data_class_max=ctx.data_class,
            status="ok" if error is None else "error",
            error=redact_text(f"{type(error).__name__}: {error}")[:500] if error else None,
        )
        try:
            await self._recorder.record(record)
        except Exception:  # observability must never break a user request
            log.exception("model_call_record_failed")
        log.info(
            "llm_call",
            profile=profile,
            provider=provider.name,
            model=provider.model,
            status=record.status,
            latency_ms=record.latency_ms,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cost_usd=record.cost_usd,
        )

    def _retrying(self) -> AsyncRetrying:
        return AsyncRetrying(
            retry=retry_if_exception_type(RetryableLLMError),
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential_jitter(initial=self._backoff, max=8.0, jitter=self._backoff),
            reraise=True,
        )

    # ---- chat -------------------------------------------------------------

    async def chat(
        self, profile: str, req: ChatRequest, ctx: CallContext | None = None
    ) -> ChatResponse:
        ctx = ctx or CallContext()
        self.check_egress(profile, ctx.data_class)
        tried: set[str] = set()
        current = profile
        while True:
            tried.add(current)
            provider = self._provider(current)
            self._check_capabilities(provider, req)
            started = time.perf_counter()
            try:
                async for attempt in self._retrying():
                    with attempt:
                        resp = await provider.chat(req)
            except LLMError as exc:
                await self._record(
                    profile=current,
                    provider=provider,
                    ctx=ctx,
                    started=started,
                    usage=None,
                    cost=0.0,
                    error=exc,
                )
                fallback = (
                    self._fallback_for(current, ctx, tried)
                    if isinstance(exc, RetryableLLMError)
                    else None
                )
                if fallback is None:
                    raise
                log.warning("llm_fallback", profile=current, fallback=fallback)
                current = fallback
                continue
            resp.profile, resp.provider = current, provider.name
            resp.cost_usd = self._cost(provider, resp.usage)
            await self._record(
                profile=current,
                provider=provider,
                ctx=ctx,
                started=started,
                usage=resp.usage,
                cost=resp.cost_usd,
                error=None,
            )
            return resp

    # ---- streaming --------------------------------------------------------

    async def stream(
        self, profile: str, req: ChatRequest, ctx: CallContext | None = None
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion. Retries/fallback only happen before the first chunk is emitted."""
        ctx = ctx or CallContext()
        self.check_egress(profile, ctx.data_class)
        tried: set[str] = set()
        current = profile
        while True:
            tried.add(current)
            provider = self._provider(current)
            self._check_capabilities(provider, req)
            started = time.perf_counter()
            emitted = False
            attempt = 0
            try:
                while True:
                    attempt += 1
                    try:
                        async for chunk in provider.stream(req):
                            emitted = True
                            if chunk.usage is not None:
                                chunk.cost_usd = self._cost(provider, chunk.usage)
                                await self._record(
                                    profile=current,
                                    provider=provider,
                                    ctx=ctx,
                                    started=started,
                                    usage=chunk.usage,
                                    cost=chunk.cost_usd,
                                    error=None,
                                )
                            yield chunk
                        return
                    except RetryableLLMError:
                        if emitted or attempt >= self._max_attempts:
                            raise
                        await _sleep(self._backoff * 2 ** (attempt - 1))
            except LLMError as exc:
                await self._record(
                    profile=current,
                    provider=provider,
                    ctx=ctx,
                    started=started,
                    usage=None,
                    cost=0.0,
                    error=exc,
                )
                fallback = (
                    self._fallback_for(current, ctx, tried)
                    if isinstance(exc, RetryableLLMError) and not emitted
                    else None
                )
                if fallback is None:
                    raise
                log.warning("llm_fallback", profile=current, fallback=fallback)
                current = fallback


async def _sleep(seconds: float) -> None:
    import asyncio

    if seconds > 0:
        await asyncio.sleep(seconds)

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from gsoi_assistant import __version__
from gsoi_assistant.api import setup_page
from gsoi_assistant.api.container import Container, build_container
from gsoi_assistant.api.routers import alerts, chat, connections, health
from gsoi_assistant.config.settings import Settings, get_settings
from gsoi_assistant.core.errors import (
    BadRequestError,
    BudgetExceededError,
    CapabilityError,
    ConflictError,
    ConnectorNotConnectedError,
    EgressDeniedError,
    NotFoundError,
    ProviderAuthError,
    ProviderBadRequestError,
    RateLimitedError,
    RetryableLLMError,
    UnknownProfileError,
)
from gsoi_assistant.core.redaction import redact_text
from gsoi_assistant.observability.logging import configure_logging

log = structlog.get_logger(__name__)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Domain error -> (HTTP status, public error code)
_ERROR_MAP: list[tuple[type[Exception], int, str]] = [
    (EgressDeniedError, 403, "egress_denied"),
    (NotFoundError, 404, "not_found"),
    (ConflictError, 409, "conflict"),
    (ConnectorNotConnectedError, 409, "connector_not_ready"),
    (BadRequestError, 400, "bad_request"),
    (BudgetExceededError, 422, "budget_exceeded"),
    (UnknownProfileError, 400, "unknown_profile"),
    (CapabilityError, 400, "capability_error"),
    (RateLimitedError, 503, "provider_rate_limited"),
    (RetryableLLMError, 503, "provider_unavailable"),
    (ProviderAuthError, 502, "provider_auth_error"),
    (ProviderBadRequestError, 502, "provider_bad_request"),
]


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(
        level=settings.log_level, json_logs=settings.json_logs, log_file=settings.log_file
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container or build_container(settings)
        await app.state.container.hub.load()
        log.info("startup", version=__version__, env=settings.env, profiles=list(settings.profiles))
        try:
            yield
        finally:
            await app.state.container.aclose()

    app = FastAPI(
        title="GSOI Personal Assistant",
        version=__version__,
        lifespan=lifespan,
        description="Collega i tuoi servizi dalla pagina [/setup](/setup).",
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    def _register(exc_type: type[Exception], status: int, code: str) -> None:
        async def handler(_: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(
                status_code=status,
                content={"error": {"code": code, "message": redact_text(str(exc))}},
            )

        app.add_exception_handler(exc_type, handler)

    for exc_type, status, code in _ERROR_MAP:
        _register(exc_type, status, code)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/setup")

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(connections.router)
    app.include_router(alerts.router)
    app.include_router(setup_page.router)
    return app


def run() -> None:  # pragma: no cover - process entry point
    settings = get_settings()
    uvicorn.run(
        "gsoi_assistant.api.main:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
    )

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from gsoi_assistant.core.redaction import redact


def _redact_processor(
    _: Any, __: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    return dict(redact(event_dict))


def configure_logging(*, level: str = "INFO", json_logs: bool = True) -> None:
    """Structured logging. request_id/run_id come from structlog contextvars; secrets are masked."""
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        _redact_processor,
        renderer,
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
    logging.basicConfig(level=level.upper(), stream=sys.stdout, format="%(message)s", force=True)

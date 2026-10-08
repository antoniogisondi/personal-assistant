from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, TextIO

import structlog

from gsoi_assistant.core.redaction import redact


def _redact_processor(
    _: Any, __: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    return dict(redact(event_dict))


def configure_logging(
    *, level: str = "INFO", json_logs: bool = True, log_file: Path | None = None
) -> None:
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
    stream: TextIO = sys.stdout
    if log_file is not None:  # e.g. a windowed desktop app, where stdout does not exist
        log_file.parent.mkdir(parents=True, exist_ok=True)
        stream = log_file.open("a", encoding="utf-8", buffering=1)
    elif sys.stdout is None:  # pragma: no cover - frozen windowed app without a log file
        stream = open(  # noqa: SIM115
            "/dev/null" if sys.platform != "win32" else "NUL", "w", encoding="utf-8"
        )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        cache_logger_on_first_use=False,
    )
    logging.basicConfig(level=level.upper(), stream=stream, format="%(message)s", force=True)

"""Secret redaction used by logging and by anything that persists error text."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|authorization|cookie|credential)", re.IGNORECASE
)
_PATTERNS = [
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"\bya29\.[0-9A-Za-z._-]{20,}"),
]


def redact_text(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact(value: Any) -> Any:
    """Recursively mask sensitive keys and secret-looking strings."""
    if isinstance(value, Mapping):
        return {
            k: REDACTED if isinstance(k, str) and _SENSITIVE_KEYS.search(k) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value

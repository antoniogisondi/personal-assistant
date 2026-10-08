from __future__ import annotations

from enum import IntEnum
from typing import Literal

Role = Literal["system", "user", "assistant", "tool"]


class DataClass(IntEnum):
    """Sensitivity of the data in a request. Ordered: higher is more sensitive."""

    PUBLIC = 0
    PRIVATE = 1  # email, calendar, personal files
    SECRET = 2  # credentials, health/financial data: never leaves local models


class Risk(IntEnum):
    """Blast radius of a tool. Drives the authorization policy."""

    READ = 0  # read-only (calendar.list_events, notes.search)
    WRITE_LOCAL = 1  # reversible, internal writes (tasks.create, notes.create)
    EXTERNAL = 2  # effects on other people or systems (email.send)
    DESTRUCTIVE = 3  # irreversible, financial or physical (delete, purchases)

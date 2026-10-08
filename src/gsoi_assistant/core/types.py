from __future__ import annotations

from enum import IntEnum
from typing import Literal

Role = Literal["system", "user", "assistant", "tool"]


class DataClass(IntEnum):
    """Sensitivity of the data in a request. Ordered: higher is more sensitive."""

    PUBLIC = 0
    PRIVATE = 1  # email, calendar, personal files
    SECRET = 2  # credentials, health/financial data: never leaves local models

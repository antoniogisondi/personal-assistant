from __future__ import annotations

import uuid


def new_id() -> uuid.UUID:
    return uuid.uuid4()

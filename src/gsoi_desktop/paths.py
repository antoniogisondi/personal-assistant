from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir


def app_home() -> Path:
    """Per-user data folder (database, logs, settings). `GSOI_DESKTOP_HOME` overrides it (tests)."""
    override = os.environ.get("GSOI_DESKTOP_HOME")
    path = Path(override) if override else Path(user_data_dir("GSOI", appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path

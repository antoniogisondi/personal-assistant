"""The master key that encrypts stored credentials.

Order of resolution: an explicit secret (env / secret manager) wins; otherwise a key is generated
on first use and kept in `data_dir/master.key` with owner-only permissions. Nobody has to create
or paste it. Losing the file only means connected accounts must be connected again.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from cryptography.fernet import Fernet

from gsoi_assistant.security.secrets import SecretNotFoundError, SecretStore

KEY_FILE = "master.key"


def resolve_master_key(secrets: SecretStore, name: str, data_dir: Path) -> str:
    try:
        return secrets.get(name).get_secret_value()
    except SecretNotFoundError:
        return _file_key(data_dir / KEY_FILE)


def _file_key(path: Path) -> str:
    if path.exists():
        return path.read_text("utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key().decode()
    try:  # O_EXCL: if two processes race, exactly one creates the file and the other reads it
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text("utf-8").strip()
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key)
    with contextlib.suppress(OSError):  # best effort on filesystems without POSIX modes (Windows)
        path.chmod(0o600)
    return key

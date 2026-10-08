"""Secret storage for the desktop app: Windows Credential Manager through `keyring`."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Protocol

from pydantic import SecretStr

from gsoi_assistant.security.secrets import SecretNotFoundError

SERVICE = "GSOI Assistant"


class WritableSecrets(Protocol):
    secure: bool

    def get(self, name: str) -> SecretStr: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...
    def has(self, name: str) -> bool: ...


class KeyringSecrets:
    """OS-protected storage (DPAPI-backed Credential Manager on Windows)."""

    secure = True

    def __init__(self, service: str = SERVICE) -> None:
        self._service = service

    def get(self, name: str) -> SecretStr:
        import keyring

        value = keyring.get_password(self._service, name)
        if not value:
            raise SecretNotFoundError(name)
        return SecretStr(value)

    def set(self, name: str, value: str) -> None:
        import keyring

        keyring.set_password(self._service, name, value)

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        with contextlib.suppress(PasswordDeleteError):
            keyring.delete_password(self._service, name)

    def has(self, name: str) -> bool:
        try:
            self.get(name)
        except SecretNotFoundError:
            return False
        return True


class FileSecrets:
    """Fallback when no OS keyring exists (e.g. headless Linux). Owner-only file, NOT encrypted:
    only meant for development and CI."""

    secure = False

    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self._path.read_text("utf-8"))
            return {str(k): str(v) for k, v in data.items()}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def get(self, name: str) -> SecretStr:
        data = self._read()
        if name not in data or not data[name]:
            raise SecretNotFoundError(name)
        return SecretStr(data[name])

    def set(self, name: str, value: str) -> None:
        data = self._read()
        data[name] = value
        self._write(data)

    def delete(self, name: str) -> None:
        data = self._read()
        if data.pop(name, None) is not None:
            self._write(data)

    def has(self, name: str) -> bool:
        return bool(self._read().get(name))


def choose_secret_store(home: Path) -> WritableSecrets:
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if not isinstance(backend, fail.Keyring):
            return KeyringSecrets()
    except Exception:  # noqa: S110  # nosec B110
        pass
    return FileSecrets(home / "secrets.json")

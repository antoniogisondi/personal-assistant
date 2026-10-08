"""SecretStore abstraction. Code asks for a secret by *name*; where it lives is pluggable."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from dotenv import dotenv_values
from pydantic import SecretStr


class SecretNotFoundError(KeyError):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        return (
            f"secret '{self.name}' not found: set it as an environment variable "
            f"or add '{self.name}=...' to the .env file (development only)"
        )


class SecretStore(Protocol):
    def get(self, name: str) -> SecretStr: ...


class EnvSecretStore:
    """Reads secrets from environment variables, then (development convenience) from `.env`.

    Real environment variables always win. Production-grade backends (KMS/Vault/sops) implement
    the same Protocol.
    """

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        dotenv_path: str | Path | None = ".env",
    ) -> None:
        self._environ = environ if environ is not None else os.environ
        self._dotenv_path = dotenv_path

    def get(self, name: str) -> SecretStr:
        value = self._environ.get(name)
        if not value and self._dotenv_path is not None:
            value = dotenv_values(self._dotenv_path).get(name)
        if not value:
            raise SecretNotFoundError(name)
        return SecretStr(value)

"""SecretStore abstraction. Code asks for a secret by *name*; where it lives is pluggable."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol

from pydantic import SecretStr


class SecretNotFoundError(KeyError):
    pass


class SecretStore(Protocol):
    def get(self, name: str) -> SecretStr: ...


class EnvSecretStore:
    """Reads secrets from environment variables. Suitable for dev and for Docker/K8s secrets
    injected as env. Production-grade backends (KMS/Vault/sops) implement the same Protocol.
    """

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = environ if environ is not None else os.environ

    def get(self, name: str) -> SecretStr:
        try:
            return SecretStr(self._environ[name])
        except KeyError:
            raise SecretNotFoundError(name) from None

"""Encryption of secrets at rest (OAuth tokens)."""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from gsoi_assistant.core.errors import GsoiError


class CryptoError(GsoiError):
    pass


class TokenCipher:
    """Authenticated encryption (Fernet: AES-128-CBC + HMAC) with a key from the SecretStore.

    Generate a key with `scripts/generate_master_key.py`.
    """

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise CryptoError(
                "the master key is not a valid Fernet key (32 url-safe base64-encoded bytes)"
            ) from exc

    def encrypt_json(self, value: dict[str, Any]) -> str:
        return self._fernet.encrypt(json.dumps(value).encode()).decode()

    def decrypt_json(self, token: str) -> dict[str, Any]:
        try:
            data = json.loads(self._fernet.decrypt(token.encode()))
        except (InvalidToken, ValueError) as exc:
            raise CryptoError("cannot decrypt stored credentials (wrong master key?)") from exc
        if not isinstance(data, dict):
            raise CryptoError("stored credentials are malformed")
        return data

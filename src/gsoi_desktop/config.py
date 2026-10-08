"""Non-secret user settings, stored as JSON next to the database. Secrets live in secrets_store."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class Provider(BaseModel):
    key: str
    label: str
    base_url: str
    needs_key: bool
    is_local: bool = False


# Base URLs are defaults the user can edit; model ids are never pre-filled (they change often).
PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider(
        key="deepseek", label="DeepSeek", base_url="https://api.deepseek.com/v1", needs_key=True
    ),
    "openai": Provider(
        key="openai", label="OpenAI", base_url="https://api.openai.com/v1", needs_key=True
    ),
    "ollama": Provider(
        key="ollama",
        label="Modello locale (Ollama)",
        base_url="http://localhost:11434/v1",
        needs_key=False,
        is_local=True,
    ),
}


def secret_name(provider: str) -> str:
    return f"{provider.upper()}_API_KEY"


class DesktopConfig(BaseModel):
    provider: str = "deepseek"
    model: str = ""
    base_url: str = PROVIDERS["deepseek"].base_url
    tool_calling: bool = True
    user_address: str = Field(default="signore", max_length=40)
    timezone: str = "Europe/Rome"
    autostart: bool = False
    read_aloud: bool = True
    voice_enabled: bool = False
    voice_model: Literal["base", "small"] = "small"
    microphone: str | None = None
    wake_threshold: float = Field(default=0.5, ge=0.2, le=0.95)

    def is_configured(self, has_key: bool) -> bool:
        provider = PROVIDERS.get(self.provider)
        if provider is None or not self.model.strip():
            return False
        return has_key or not provider.needs_key


def local_timezone(default: str = "Europe/Rome") -> str:
    try:
        from tzlocal import get_localzone_name

        return get_localzone_name() or default
    except Exception:
        return default


def config_path(home: Path) -> Path:
    return home / "config.json"


def load_config(home: Path) -> DesktopConfig:
    path = config_path(home)
    try:
        return DesktopConfig.model_validate(json.loads(path.read_text("utf-8")))
    except (OSError, ValueError, ValidationError):
        return DesktopConfig(timezone=local_timezone())


def save_config(home: Path, config: DesktopConfig) -> None:
    path = config_path(home)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a crash cannot leave a half-written file

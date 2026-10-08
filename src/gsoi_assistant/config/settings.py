"""Application settings, loaded from environment variables (and `.env` in development only).

Model names live exclusively here (as *profiles*); no other module hard-codes a model.
API keys are never stored in settings: a profile holds only the *name* of a secret
(`api_key_ref`), which the SecretStore resolves at runtime.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelProfile(BaseModel):
    provider: str  # label: "deepseek", "ollama", "openai", ...
    adapter: Literal["openai_compat"] = "openai_compat"
    model: str
    base_url: str
    api_key_ref: str | None = None  # name of the secret, NOT its value
    is_local: bool = False
    max_context: int = 32_000
    tool_calling: bool = True
    structured_output: bool = False
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    timeout_s: float = 60.0
    fallback: str | None = None  # name of another profile to try on transient failure


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GSOI_",
        env_nested_delimiter="__",
        env_file=".env",  # development convenience only; production uses real env / secrets
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool | None = None  # default: JSON unless env == "dev"

    database_url: SecretStr
    api_token: SecretStr = Field(description="Bearer token required by every /v1 endpoint.")
    api_host: str = "127.0.0.1"  # bind to localhost unless you deliberately expose it
    api_port: int = 8000
    owner_id: str = "owner"  # single-user for now; every row still carries a user_id

    profiles: dict[str, ModelProfile]
    default_profile: str = "reasoning"

    history_max_messages: int = 40
    llm_max_attempts: int = 3
    llm_backoff_initial_s: float = 0.5

    @field_validator("api_token")
    @classmethod
    def _token_strength(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 16:
            raise ValueError("GSOI_API_TOKEN must be at least 16 characters")
        return v

    @model_validator(mode="after")
    def _check_profiles(self) -> Settings:
        if self.default_profile not in self.profiles:
            raise ValueError(f"default_profile '{self.default_profile}' is not a defined profile")
        for name, p in self.profiles.items():
            if p.fallback is not None and p.fallback not in self.profiles:
                raise ValueError(f"profile '{name}' has unknown fallback '{p.fallback}'")
        return self

    @property
    def json_logs(self) -> bool:
        return self.log_json if self.log_json is not None else self.env != "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()

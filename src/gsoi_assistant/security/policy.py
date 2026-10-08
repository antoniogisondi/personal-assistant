"""Authorization policy: deterministic, data-driven, and impossible for the model to alter.

The policy answers one question for each tool call: allow, ask the user, or deny.
It is default-deny for anything it does not recognise.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from gsoi_assistant.core.types import Risk


class Effect(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    REQUIRE_STRONG_APPROVAL = "require_strong_approval"
    DENY = "deny"


_STRICTNESS = {
    Effect.ALLOW: 0,
    Effect.REQUIRE_APPROVAL: 1,
    Effect.REQUIRE_STRONG_APPROVAL: 2,
    Effect.DENY: 3,
}


def stricter(a: Effect, b: Effect) -> Effect:
    return a if _STRICTNESS[a] >= _STRICTNESS[b] else b


class PolicyError(ValueError):
    pass


class PolicyConfig(BaseModel):
    version: int = 1
    defaults: dict[str, Effect]
    taint: dict[str, Effect] = Field(default_factory=dict)
    tools: dict[str, Effect] = Field(default_factory=dict)

    @field_validator("defaults", "taint")
    @classmethod
    def _known_risks(cls, v: dict[str, Effect]) -> dict[str, Effect]:
        for key in v:
            if key not in Risk.__members__:
                raise ValueError(f"unknown risk level '{key}'")
        return v


@dataclass(frozen=True)
class PolicyInput:
    tool: str
    risk: Risk
    tainted: bool = False


@dataclass(frozen=True)
class Decision:
    effect: Effect
    reason: str


def load_policy_config(path: Path | None = None) -> PolicyConfig:
    if path is None:
        raw = (
            resources.files("gsoi_assistant.security")
            .joinpath("default_policy.yaml")
            .read_text("utf-8")
        )
    else:
        raw = path.read_text("utf-8")
    data: Any = yaml.safe_load(raw) or {}
    return PolicyConfig.model_validate(data)


class PolicyEngine:
    def __init__(self, config: PolicyConfig, *, readonly: bool = False) -> None:
        self._config = config
        self._readonly = readonly
        self._validate(config)

    @staticmethod
    def _validate(config: PolicyConfig) -> None:
        """Safety floor: whatever the file says, outward and destructive actions need the user."""
        for risk in (Risk.EXTERNAL, Risk.DESTRUCTIVE):
            effect = config.defaults.get(risk.name, Effect.DENY)
            if effect == Effect.ALLOW:
                raise PolicyError(f"policy may not auto-allow {risk.name} tools")
        if config.defaults.get(Risk.DESTRUCTIVE.name) == Effect.REQUIRE_APPROVAL:
            raise PolicyError("DESTRUCTIVE tools require strong approval (or deny)")
        for effect in config.taint.values():
            if effect == Effect.ALLOW:
                raise PolicyError("taint rules must be stricter than allow")

    def evaluate(self, inp: PolicyInput) -> Decision:
        if self._readonly and inp.risk > Risk.READ:
            return Decision(Effect.DENY, "read-only mode is enabled")

        base = self._config.defaults.get(inp.risk.name)
        if base is None:
            return Decision(Effect.DENY, f"no policy for risk {inp.risk.name}")
        effect, reason = base, f"default for {inp.risk.name}"

        if inp.tainted:
            taint_effect = self._config.taint.get(inp.risk.name)
            if taint_effect is not None and stricter(taint_effect, effect) != effect:
                effect, reason = taint_effect, "untrusted content is present in this run"

        override = self._config.tools.get(inp.tool)
        if override is not None:
            combined = stricter(override, effect)
            if combined != effect:
                effect, reason = combined, f"override for {inp.tool}"
        return Decision(effect, reason)

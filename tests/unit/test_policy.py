from pathlib import Path

import pytest

from gsoi_assistant.core.types import Risk
from gsoi_assistant.security.policy import (
    Effect,
    PolicyConfig,
    PolicyEngine,
    PolicyError,
    PolicyInput,
    load_policy_config,
)


def engine(**kw: object) -> PolicyEngine:
    return PolicyEngine(load_policy_config(), **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("risk", "tainted", "expected"),
    [
        (Risk.READ, False, Effect.ALLOW),
        (Risk.READ, True, Effect.ALLOW),
        (Risk.WRITE_LOCAL, False, Effect.ALLOW),
        (Risk.WRITE_LOCAL, True, Effect.REQUIRE_APPROVAL),  # untrusted content present
        (Risk.EXTERNAL, False, Effect.REQUIRE_APPROVAL),
        (Risk.EXTERNAL, True, Effect.REQUIRE_APPROVAL),
        (Risk.DESTRUCTIVE, False, Effect.REQUIRE_STRONG_APPROVAL),
        (Risk.DESTRUCTIVE, True, Effect.REQUIRE_STRONG_APPROVAL),
    ],
)
def test_default_policy_table(risk: Risk, tainted: bool, expected: Effect) -> None:
    assert engine().evaluate(PolicyInput("x.y", risk, tainted)).effect == expected


def test_readonly_kill_switch_denies_everything_but_reads() -> None:
    e = engine(readonly=True)
    assert e.evaluate(PolicyInput("a.b", Risk.READ)).effect == Effect.ALLOW
    for risk in (Risk.WRITE_LOCAL, Risk.EXTERNAL, Risk.DESTRUCTIVE):
        assert e.evaluate(PolicyInput("a.b", risk)).effect == Effect.DENY


def test_override_can_tighten_but_never_loosen() -> None:
    cfg = load_policy_config()
    cfg.tools = {"notes.create": Effect.REQUIRE_APPROVAL, "mail.send": Effect.ALLOW}
    e = PolicyEngine(cfg)
    assert (
        e.evaluate(PolicyInput("notes.create", Risk.WRITE_LOCAL)).effect == Effect.REQUIRE_APPROVAL
    )
    assert e.evaluate(PolicyInput("mail.send", Risk.EXTERNAL)).effect == Effect.REQUIRE_APPROVAL


def test_policy_that_autoallows_external_or_destructive_is_rejected() -> None:
    for risk in ("EXTERNAL", "DESTRUCTIVE"):
        defaults = {
            "READ": "allow",
            "WRITE_LOCAL": "allow",
            "EXTERNAL": "require_approval",
            "DESTRUCTIVE": "require_strong_approval",
        }
        defaults[risk] = "allow"
        with pytest.raises(PolicyError):
            PolicyEngine(PolicyConfig.model_validate({"defaults": defaults}))


def test_destructive_needs_strong_approval_in_any_policy() -> None:
    cfg = {
        "defaults": {
            "READ": "allow",
            "WRITE_LOCAL": "allow",
            "EXTERNAL": "require_approval",
            "DESTRUCTIVE": "require_approval",
        }
    }
    with pytest.raises(PolicyError):
        PolicyEngine(PolicyConfig.model_validate(cfg))


def test_missing_risk_level_is_denied_by_default() -> None:
    cfg = PolicyConfig.model_validate(
        {
            "defaults": {
                "READ": "allow",
                "EXTERNAL": "require_approval",
                "DESTRUCTIVE": "require_strong_approval",
            }
        }
    )
    assert PolicyEngine(cfg).evaluate(PolicyInput("a.b", Risk.WRITE_LOCAL)).effect == Effect.DENY


def test_unknown_risk_key_rejected() -> None:
    with pytest.raises(ValueError):
        PolicyConfig.model_validate({"defaults": {"NUCLEAR": "allow"}})


def test_custom_policy_file(tmp_path: Path) -> None:
    f = tmp_path / "p.yaml"
    f.write_text(
        "defaults: {READ: allow, WRITE_LOCAL: require_approval, EXTERNAL: deny, "
        "DESTRUCTIVE: deny}\n"
    )
    e = PolicyEngine(load_policy_config(f))
    assert e.evaluate(PolicyInput("a.b", Risk.WRITE_LOCAL)).effect == Effect.REQUIRE_APPROVAL
    assert e.evaluate(PolicyInput("a.b", Risk.EXTERNAL)).effect == Effect.DENY

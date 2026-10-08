import pytest
from pydantic import ValidationError

from gsoi_assistant.config.settings import ModelProfile, Settings

P = ModelProfile(provider="x", model="m", base_url="http://x/v1")


def build(**kw: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "sqlite+aiosqlite://",
        "api_token": "t" * 20,
        "profiles": {"reasoning": P},
    }
    base.update(kw)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_ok() -> None:
    assert build().default_profile == "reasoning"


def test_short_token_rejected() -> None:
    with pytest.raises(ValidationError):
        build(api_token="short")


def test_unknown_default_profile_rejected() -> None:
    with pytest.raises(ValidationError):
        build(default_profile="nope")


def test_unknown_fallback_rejected() -> None:
    with pytest.raises(ValidationError):
        build(profiles={"reasoning": P.model_copy(update={"fallback": "ghost"})})


def test_secrets_not_in_repr() -> None:
    s = build()
    assert "t" * 20 not in repr(s)
    assert "sqlite" not in repr(s)


def test_env_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GSOI_DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setenv("GSOI_API_TOKEN", "e" * 20)
    monkeypatch.setenv("GSOI_PROFILES__REASONING__PROVIDER", "deepseek")
    monkeypatch.setenv("GSOI_PROFILES__REASONING__MODEL", "some-model")
    monkeypatch.setenv("GSOI_PROFILES__REASONING__BASE_URL", "https://x/v1")
    monkeypatch.setenv("GSOI_PROFILES__REASONING__API_KEY_REF", "DEEPSEEK_API_KEY")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.profiles["reasoning"].api_key_ref == "DEEPSEEK_API_KEY"

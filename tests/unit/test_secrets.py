from pathlib import Path

import pytest

from gsoi_assistant.security.secrets import EnvSecretStore, SecretNotFoundError


def test_reads_environment() -> None:
    store = EnvSecretStore({"K": "from-env"}, dotenv_path=None)
    assert store.get("K").get_secret_value() == "from-env"


def test_falls_back_to_dotenv(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("DEEPSEEK_API_KEY=from-dotenv\n")
    assert (
        EnvSecretStore({}, dotenv_path=f).get("DEEPSEEK_API_KEY").get_secret_value()
        == "from-dotenv"
    )


def test_environment_wins_over_dotenv(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("K=from-dotenv\n")
    assert (
        EnvSecretStore({"K": "from-env"}, dotenv_path=f).get("K").get_secret_value() == "from-env"
    )


def test_missing_or_empty_secret_has_helpful_message(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("EMPTY=\n")
    store = EnvSecretStore({}, dotenv_path=f)
    for name in ("MISSING", "EMPTY"):
        with pytest.raises(SecretNotFoundError) as ei:
            store.get(name)
        assert name in str(ei.value) and ".env" in str(ei.value)

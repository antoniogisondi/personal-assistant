from gsoi_assistant.core.redaction import REDACTED, redact, redact_text


def test_masks_secret_looking_strings() -> None:
    assert "sk-abcdefghijklmnop" not in redact_text("key=sk-abcdefghijklmnop end")
    assert "abcdefghijklmnopq" not in redact_text("Authorization: Bearer abcdefghijklmnopq")
    assert "ghp_" not in redact_text("ghp_" + "a" * 30)


def test_masks_sensitive_keys_recursively() -> None:
    out = redact(
        {"api_key": "x", "nested": {"Authorization": "y", "ok": "fine"}, "l": [{"token": "z"}]}
    )
    assert out["api_key"] == REDACTED
    assert out["nested"]["Authorization"] == REDACTED
    assert out["nested"]["ok"] == "fine"
    assert out["l"][0]["token"] == REDACTED


def test_non_strings_untouched() -> None:
    assert redact({"n": 3, "b": True}) == {"n": 3, "b": True}

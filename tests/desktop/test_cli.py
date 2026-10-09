"""The command line is the first thing a user touches: every flag must parse and dispatch."""

from __future__ import annotations

import pytest

from gsoi_desktop import app


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    seen: list[tuple[str, object]] = []
    monkeypatch.setattr(app, "selftest", lambda *a, **k: seen.append(("selftest", None)) or 0)
    monkeypatch.setattr(app, "voice_test", lambda *a, **k: seen.append(("voice_test", None)) or 0)
    monkeypatch.setattr(
        app,
        "run_gui",
        lambda minimized, quit_after_ms=None: seen.append(("gui", (minimized, quit_after_ms))) or 0,
    )
    monkeypatch.setattr(app, "_record", lambda home, code: code)
    return seen


def test_mail_diag_takes_a_host_and_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    from gsoi_assistant.connectors.mail import diagnostics

    seen: list[str] = []
    monkeypatch.setattr(diagnostics, "diagnose", lambda host, say=print: seen.append(host))
    assert app.main(["--mail-diag", "smtp.example.it"]) == 0 and seen == ["smtp.example.it"]


def test_every_documented_flag_parses() -> None:
    parser = app.build_parser()
    for flag in ("--selftest", "--minimized", "--gui-selftest", "--voice-test"):
        assert getattr(parser.parse_args([flag]), flag.lstrip("-").replace("-", "_")) is True
    assert not any(vars(parser.parse_args([])).values())


def test_no_arguments_opens_the_app(calls: list[tuple[str, object]]) -> None:
    assert app.main([]) == 0
    assert calls == [("gui", (False, None))]


def test_minimized_is_passed_on(calls: list[tuple[str, object]]) -> None:
    app.main(["--minimized"])
    assert calls == [("gui", (True, None))]


def test_voice_test_runs_the_diagnostic(calls: list[tuple[str, object]]) -> None:
    assert app.main(["--voice-test"]) == 0
    assert calls == [("voice_test", None)]


def test_selftests_dispatch(calls: list[tuple[str, object]]) -> None:
    app.main(["--selftest"])
    app.main(["--gui-selftest"])
    assert calls == [("selftest", None), ("gui", (False, 1500))]


def test_unknown_flags_are_rejected() -> None:
    with pytest.raises(SystemExit):
        app.main(["--nope"])

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from gsoi_desktop.alerts import Alert, is_quiet, parse_alerts, spoken_summary
from gsoi_desktop.config import DesktopConfig
from gsoi_desktop.controller import AssistantController
from gsoi_desktop.ui.alert_watcher import AlertWatcher
from gsoi_desktop.ui.async_call import AsyncRunner
from gsoi_desktop.ui.main_window import MainWindow
from gsoi_desktop.ui.settings_dialog import SettingsDialog
from qt_helpers import FakeChat, FakeSpeaker, wait_until


def alert(kind: str = "email", n: int = 1, imp: bool = False) -> Alert:
    return Alert(kind, f"k{n}", f"Titolo {n}", "testo", f"Dico {n}.", imp)


def test_quiet_hours_across_midnight_and_within_a_day() -> None:
    assert is_quiet(23, 23, 7) and is_quiet(2, 23, 7) and not is_quiet(7, 23, 7)
    assert not is_quiet(12, 23, 7)
    assert is_quiet(13, 13, 15) and not is_quiet(15, 13, 15)
    assert not is_quiet(3, 5, 5)  # equal hours: no quiet period


def test_alerts_are_parsed_defensively_and_summarised_for_speech() -> None:
    parsed = parse_alerts({"alerts": [{"kind": "email", "key": "a", "title": "T"}, {"bad": 1}]})
    assert len(parsed) == 1 and parsed[0].spoken == "T"
    batch = [alert("email", i) for i in range(1, 6)] + [alert("event", 9, True)]
    said = spoken_summary(batch)
    assert said.startswith("Dico 9.") and said.endswith("E altre 3 novità.")  # events first


def test_controller_returns_parsed_alerts() -> None:
    class Api:
        def check_alerts(self, lead: int) -> dict[str, Any]:
            assert lead == 12
            return {"alerts": [{"kind": "event", "key": "e", "title": "Riunione", "spoken": "x"}]}

    ctl = AssistantController(Api())  # type: ignore[arg-type]
    assert [a.title for a in ctl.check_alerts(12)] == ["Riunione"]


def test_the_watcher_polls_when_enabled_and_never_overlaps(qapp: QApplication) -> None:
    got: list[Any] = []
    calls: list[int] = []

    def check(lead: int) -> list[Alert]:
        calls.append(lead)
        return [alert()] if len(calls) == 1 else []

    enabled = {"on": False}
    w = AlertWatcher(
        AsyncRunner(),
        check,
        enabled=lambda: enabled["on"],
        lead_minutes=lambda: 7,
        interval_ms=60_000,
    )
    w.alerts.connect(got.append)
    w.poll()
    assert calls == []  # disabled
    enabled["on"] = True
    w.poll()
    w.poll()  # in flight: ignored
    wait_until(lambda: got)
    assert calls == [7] and len(got[0]) == 1
    w.poll()
    wait_until(lambda: len(calls) == 2)
    w.stop()


def test_the_watcher_survives_a_failing_check(qapp: QApplication) -> None:
    def check(lead: int) -> list[Alert]:
        raise RuntimeError("offline")

    w = AlertWatcher(AsyncRunner(), check, enabled=lambda: True, lead_minutes=lambda: 5)
    w.poll()
    wait_until(lambda: not w._in_flight)
    w.poll()  # still able to try again
    wait_until(lambda: not w._in_flight)


def make_window() -> tuple[MainWindow, FakeSpeaker, list[bool]]:
    speaker = FakeSpeaker()
    w = MainWindow(
        FakeChat(), AsyncRunner(), speaker, is_configured=lambda: True, read_aloud=lambda: False
    )
    done: list[bool] = []
    w.voice_turn_finished.connect(lambda: done.append(True))
    return w, speaker, done


def test_announce_speaks_and_hands_the_microphone_back_when_finished(qapp: QApplication) -> None:
    w, speaker, done = make_window()
    assert w.announce("Nuova email da Marco.") and speaker.said == ["Nuova email da Marco."]
    assert done == []
    w.set_speaking(True)
    w.set_speaking(False)
    assert done == [True]


def test_announce_never_interrupts_a_conversation(qapp: QApplication) -> None:
    w, speaker, _ = make_window()
    w._busy = True
    assert not w.announce("x") and speaker.said == []
    w._busy = False
    w._voice_turn = True
    assert not w.announce("x")


def test_announce_without_a_working_voice_finishes_at_once(qapp: QApplication) -> None:
    w, speaker, done = make_window()
    speaker.enqueue = lambda text: None  # type: ignore[method-assign]  # nothing starts speaking
    assert w.announce("x") and done == [True]


def test_alert_settings_round_trip(qapp: QApplication) -> None:
    cfg = DesktopConfig(provider="deepseek", model="deepseek-chat")
    dialog = SettingsDialog(cfg, has_key=True, autostart_supported=True)
    dialog.alerts_lead.setValue(15)
    dialog.quiet_from.setValue(22)
    dialog.alerts_speak.setChecked(False)
    dialog._save()
    assert dialog.result_value is not None
    out = dialog.result_value.config
    assert out.alerts_lead_minutes == 15 and out.quiet_from == 22 and not out.alerts_speak


@pytest.mark.parametrize("n", [0, 61])
def test_lead_minutes_are_bounded(n: int) -> None:
    with pytest.raises(ValueError):
        DesktopConfig(alerts_lead_minutes=n)


def test_mail_panel_adds_lists_and_removes_accounts(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QDialog

    from gsoi_desktop.controller import MailAccountInfo
    from gsoi_desktop.ui import mail_panel
    from gsoi_desktop.ui.mail_panel import AddMailDialog, MailPanel

    class Ctl:
        def __init__(self) -> None:
            self.accounts: list[MailAccountInfo] = []
            self.added: list[tuple[Any, ...]] = []
            self.fail: str | None = None

        def mail_accounts(self) -> list[MailAccountInfo]:
            return list(self.accounts)

        def mail_add(self, address: str, password: str, servers: dict[str, Any]) -> MailAccountInfo:
            if self.fail:
                raise RuntimeError(self.fail)
            self.added.append((address, password, servers))
            a = MailAccountInfo("id1", "Tiscali", address)
            self.accounts.append(a)
            return a

        def mail_remove(self, account_id: str) -> None:
            self.accounts = [a for a in self.accounts if a.id != account_id]

    ctl = Ctl()
    panel = MailPanel(ctl, AsyncRunner())  # type: ignore[arg-type]
    wait_until(lambda: not panel.remove_button.isEnabled())

    def fill(self: AddMailDialog) -> int:
        self.address.setText("me@tiscali.it")
        self.password.setText("segreta")
        self._save()
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(mail_panel.AddMailDialog, "exec", fill)
    panel.add_button.click()
    wait_until(lambda: panel.list.count() == 1)
    assert ctl.added == [("me@tiscali.it", "segreta", {})] and "aggiunta" in panel.message.text()

    panel.list.setCurrentRow(0)
    panel.remove_button.click()
    wait_until(lambda: panel.list.count() == 0)

    ctl.fail = "Accesso rifiutato dal server di posta"
    panel.add_button.click()
    wait_until(lambda: "rifiutato" in panel.message.text())
    assert panel.add_button.isEnabled()


def test_add_dialog_needs_both_fields_and_servers_when_advanced(qapp: QApplication) -> None:
    from gsoi_desktop.ui.mail_panel import AddMailDialog

    d = AddMailDialog()
    d.address.setText("me@azienda.it")
    d._save()
    assert d.result_value is None and "password" in d.error.text()
    d.password.setText("x")
    d.advanced.setChecked(True)
    d._save()
    assert d.result_value is None and "server" in d.error.text()
    d.imap_host.setText("mail.azienda.it")
    d.smtp_host.setText("smtp.azienda.it")
    d.starttls.setChecked(True)
    d._save()
    assert d.result_value == (
        "me@azienda.it",
        "x",
        {
            "imap_host": "mail.azienda.it",
            "smtp_host": "smtp.azienda.it",
            "smtp_port": 587,
            "smtp_security": "starttls",
        },
    )


def test_mail_panel_asks_before_using_an_outdated_server_and_retries_with_consent(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QDialog

    from gsoi_desktop.controller import MailAccountInfo
    from gsoi_desktop.ui import mail_panel
    from gsoi_desktop.ui.mail_panel import AddMailDialog, MailPanel

    class Ctl:
        def __init__(self) -> None:
            self.tries: list[dict[str, Any]] = []
            self.accounts: list[MailAccountInfo] = []

        def mail_accounts(self) -> list[MailAccountInfo]:
            return list(self.accounts)

        def mail_add(self, address: str, password: str, servers: dict[str, Any]) -> MailAccountInfo:
            self.tries.append(servers)
            if not servers.get("smtp_legacy_tls"):
                raise RuntimeError("supporta solo protocolli di sicurezza datati (TLS 1.0/1.1)")
            a = MailAccountInfo("i", "Tiscali", address, True)
            self.accounts.append(a)
            return a

    def fill(self: AddMailDialog) -> int:
        self.address.setText("me@tiscali.it")
        self.password.setText("p")
        self._save()
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(mail_panel.AddMailDialog, "exec", fill)
    asked: list[str] = []
    ctl = Ctl()
    panel = MailPanel(ctl, AsyncRunner(), confirm=lambda reason: asked.append(reason) or True)  # type: ignore[arg-type]
    panel.add_button.click()
    wait_until(lambda: panel.list.count() == 1)
    assert len(asked) == 1 and ctl.tries == [{}, {"smtp_legacy_tls": True}]
    assert "sicurezza ridotta" in panel.list.item(0).text()

    declined = Ctl()
    p2 = MailPanel(declined, AsyncRunner(), confirm=lambda reason: False)  # type: ignore[arg-type]
    p2.add_button.click()
    wait_until(lambda: "troppo datato" in p2.message.text())
    assert declined.tries == [{}] and p2.add_button.isEnabled()

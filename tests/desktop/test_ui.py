from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog

from gsoi_desktop.client import ApiError
from gsoi_desktop.config import DesktopConfig
from gsoi_desktop.controller import Approval, GoogleState, Turn
from gsoi_desktop.ui import services_dialog
from gsoi_desktop.ui.approval_dialog import ApprovalDialog
from gsoi_desktop.ui.async_call import AsyncRunner
from gsoi_desktop.ui.icon import make_icon
from gsoi_desktop.ui.main_window import MainWindow
from gsoi_desktop.ui.services_dialog import ServicesDialog
from gsoi_desktop.ui.settings_dialog import SettingsDialog
from gsoi_desktop.ui.speech import Speaker
from gsoi_desktop.ui.tray import Tray


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def wait_until(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        QApplication.processEvents()
        if time.monotonic() > end:
            raise AssertionError("condition not reached in time")
        time.sleep(0.005)
    QApplication.processEvents()


APPROVAL = Approval(
    "a1",
    "email.send",
    "EXTERNAL",
    False,
    "Invia email a marco@example.com",
    {"to": ["marco@example.com"]},
)


class FakeChat:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.next: Turn | Exception = Turn(text="Ciao!")
        self.decision_result = Turn(text="Email inviata.")

    def send(self, text: str) -> Turn:
        self.calls.append(("send", text))
        if isinstance(self.next, Exception):
            raise self.next
        return self.next

    def briefing(self) -> Turn:
        self.calls.append(("briefing", None))
        return Turn(text="Buongiorno signore, hai due impegni.")

    def decide(self, approval: Approval, approve: bool) -> Turn:
        self.calls.append(("decide", approve))
        return self.decision_result

    def new_conversation(self) -> None:
        self.calls.append(("new", None))


class FakeSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.stopped = 0

    def speak(self, text: str) -> None:
        self.said.append(text)

    def stop(self) -> None:
        self.stopped += 1


def make_window(
    qapp: QApplication,
    chat: FakeChat | None = None,
    *,
    configured: bool = True,
    read_aloud: bool = True,
):  # type: ignore[no-untyped-def]
    chat = chat or FakeChat()
    speaker = FakeSpeaker()
    w = MainWindow(
        chat,
        AsyncRunner(),
        speaker,
        is_configured=lambda: configured,
        read_aloud=lambda: read_aloud,
    )
    return w, chat, speaker


# ---- main window -----------------------------------------------------------------------------


def test_unconfigured_window_guides_the_user_and_blocks_chat(qapp: QApplication) -> None:
    w, chat, _ = make_window(qapp, configured=False)
    assert not w.banner.isHidden() and "Impostazioni" in w.banner.text()
    assert (
        not w.input.isEnabled()
        and not w.send_button.isEnabled()
        and not w.briefing_button.isEnabled()
    )
    w.input.setText("ciao")
    w.submit()
    assert chat.calls == []


def test_sending_a_message_shows_the_answer_and_toggles_busy(qapp: QApplication) -> None:
    w, chat, _ = make_window(qapp)
    assert w.banner.isHidden() and w.input.isEnabled()
    w.input.setText("ciao assistente")
    w.submit()
    assert not w.input.isEnabled() and w.status.text()  # busy while waiting
    wait_until(lambda: "Ciao!" in w.transcript.toPlainText())
    assert "ciao assistente" in w.transcript.toPlainText()
    assert (
        w.input.isEnabled()
        and w.status.text() == ""
        and chat.calls == [("send", "ciao assistente")]
    )


def test_empty_input_and_double_submit_are_ignored(qapp: QApplication) -> None:
    w, chat, _ = make_window(qapp)
    w.submit()
    w.input.setText("uno")
    w.submit()
    w.input.setText("due")
    w.submit()  # still busy with "uno"
    wait_until(lambda: w.input.isEnabled())
    assert chat.calls == [("send", "uno")]


def test_model_text_is_never_rendered_as_html(qapp: QApplication) -> None:
    chat = FakeChat()
    chat.next = Turn(
        text='<script>alert(1)</script> <a href="http://evil.example">click</a> <b>bold</b>'
    )
    w, _, _ = make_window(qapp, chat)
    w.input.setText("x")
    w.submit()
    wait_until(lambda: "click" in w.transcript.toPlainText())
    plain = w.transcript.toPlainText()
    assert "<script>alert(1)</script>" in plain and "<b>bold</b>" in plain  # shown literally
    assert 'href="http://evil.example"' in plain


def test_errors_are_shown_in_plain_language(qapp: QApplication) -> None:
    chat = FakeChat()
    chat.next = ApiError(502, "La chiave API non è valida.")
    w, _, _ = make_window(qapp, chat)
    w.input.setText("x")
    w.submit()
    wait_until(lambda: "chiave API" in w.transcript.toPlainText())
    assert w.input.isEnabled()
    chat.next = RuntimeError("boom")
    w.input.setText("y")
    w.submit()
    wait_until(lambda: "boom" in w.transcript.toPlainText())


@pytest.mark.parametrize("approve", [True, False])
def test_approval_flow(qapp: QApplication, monkeypatch: pytest.MonkeyPatch, approve: bool) -> None:
    chat = FakeChat()
    chat.next = Turn(approval=APPROVAL)
    chat.decision_result = Turn(text="Email inviata." if approve else "Ok, non la mando.")
    seen: list[Approval] = []

    def fake_exec(self: ApprovalDialog) -> int:
        seen.append(self.approval)
        return int(QDialog.DialogCode.Accepted if approve else QDialog.DialogCode.Rejected)

    monkeypatch.setattr(ApprovalDialog, "exec", fake_exec)
    w, _, _ = make_window(qapp, chat)
    w.input.setText("scrivi a marco")
    w.submit()
    expected = "Email inviata." if approve else "Ok, non la mando."
    wait_until(lambda: expected in w.transcript.toPlainText())
    assert seen == [APPROVAL] and ("decide", approve) in chat.calls
    assert ("Approvato." if approve else "Rifiutato.") in w.transcript.toPlainText()
    assert w.input.isEnabled()


def test_briefing_is_read_aloud_only_when_enabled(qapp: QApplication) -> None:
    w, _, speaker = make_window(qapp, read_aloud=True)
    w.request_briefing()
    wait_until(lambda: speaker.said)
    assert speaker.said == ["Buongiorno signore, hai due impegni."]
    w2, _, speaker2 = make_window(qapp, read_aloud=False)
    w2.request_briefing()
    wait_until(lambda: "Buongiorno" in w2.transcript.toPlainText())
    assert speaker2.said == []


def test_new_chat_clears_and_stops_speech(qapp: QApplication) -> None:
    w, chat, speaker = make_window(qapp)
    w.add("assistant", "vecchio")
    w.new_chat()
    assert w.transcript.toPlainText() == "" and ("new", None) in chat.calls and speaker.stopped == 1


def test_closing_hides_to_the_tray_instead_of_quitting(qapp: QApplication) -> None:
    w, _, _ = make_window(qapp)
    w.show()
    event = QCloseEvent()
    w.closeEvent(event)
    assert not event.isAccepted() and not w.isVisible()
    w.hide_on_close = False
    event2 = QCloseEvent()
    w.closeEvent(event2)
    assert event2.isAccepted()


# ---- dialogs ---------------------------------------------------------------------------------


def test_approval_dialog_defaults_to_reject_and_shows_exact_details(qapp: QApplication) -> None:
    d = ApprovalDialog(APPROVAL)
    assert d.reject_button.isDefault() and d.approve_button.isEnabled()
    from PySide6.QtWidgets import QPlainTextEdit

    texts = [c.toPlainText() for c in d.findChildren(QPlainTextEdit)]
    assert any('"to"' in t and "marco@example.com" in t for t in texts)


def test_approval_summary_cannot_inject_html(qapp: QApplication) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    d = ApprovalDialog(replace(APPROVAL, summary="<a href='http://evil'>Approva</a>"))
    labels = [lbl for lbl in d.findChildren(QLabel) if "evil" in lbl.text()]
    assert labels and all(lbl.textFormat() == Qt.TextFormat.PlainText for lbl in labels)


def test_destructive_approval_requires_typing_the_tool_name(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QLineEdit

    strong = replace(APPROVAL, tool="notes.delete", risk="DESTRUCTIVE", strong=True)
    d = ApprovalDialog(strong)
    assert not d.approve_button.isEnabled()
    box = d.findChild(QLineEdit)
    box.setText("notes.dele")
    assert not d.approve_button.isEnabled()
    box.setText("notes.delete")
    assert d.approve_button.isEnabled()


def test_settings_validation(qapp: QApplication) -> None:
    d = SettingsDialog(DesktopConfig(), has_key=False, autostart_supported=True)
    d._save()
    assert d.result_value is None and "modello" in d.error.text()
    d.model.setText("some-model")
    d._save()
    assert d.result_value is None and "chiave" in d.error.text()
    d.api_key.setText("sk-abc")
    d.base_url.setText("not a url")
    d._save()
    assert d.result_value is None and "http" in d.error.text()
    d.base_url.setText("https://api.deepseek.com/v1")
    d._save()
    r = d.result_value
    assert r is not None and r.new_api_key == "sk-abc" and r.config.model == "some-model"
    assert r.config.provider == "deepseek" and r.config.user_address == "signore"


def test_settings_keep_the_stored_key_and_local_models_need_none(qapp: QApplication) -> None:
    d = SettingsDialog(DesktopConfig(model="m"), has_key=True, autostart_supported=False)
    d._save()
    assert d.result_value is not None and d.result_value.new_api_key is None  # unchanged
    assert not d.autostart.isEnabled()

    local = SettingsDialog(DesktopConfig(), has_key=False, autostart_supported=True)
    local.provider.setCurrentIndex(local.provider.findData("ollama"))
    assert local.base_url.text().startswith("http://localhost") and not local.api_key.isEnabled()
    local.model.setText("llama")
    local._save()
    assert local.result_value is not None and local.result_value.config.provider == "ollama"


# ---- services dialog -------------------------------------------------------------------------


class FakeController:
    def __init__(self, state: GoogleState) -> None:
        self.state = state
        self.url = "https://accounts.google.com/o/oauth2/v2/auth?x=1"
        self.disconnected = False

    def google_state(self) -> GoogleState:
        return self.state

    def google_connect_url(self) -> str:
        return self.url

    def google_disconnect(self) -> None:
        self.disconnected = True
        self.state = GoogleState(available=True, connected=False)


def services(ctrl: FakeController, opened: list[str]) -> ServicesDialog:
    return ServicesDialog(ctrl, AsyncRunner(), open_url=opened.append)  # type: ignore[arg-type]


def test_services_when_google_is_not_enabled(qapp: QApplication) -> None:
    d = services(FakeController(GoogleState(available=False, connected=False)), [])
    wait_until(lambda: d.state is not None)
    assert "non è ancora attivato" in d.status.text()
    assert d.connect_button.isHidden() and d.disconnect_button.isHidden()


def test_one_click_connect_opens_google_and_detects_completion(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services_dialog, "POLL_MS", 20)
    ctrl = FakeController(GoogleState(available=True, connected=False))
    opened: list[str] = []
    d = services(ctrl, opened)
    changed: list[bool] = []
    d.changed.connect(lambda: changed.append(True))
    wait_until(lambda: d.state is not None)
    assert not d.connect_button.isHidden()

    d.connect_button.click()
    wait_until(lambda: opened)
    assert opened == [ctrl.url] and "browser" in d.message.text()

    ctrl.state = GoogleState(
        available=True, connected=True, scopes=("gmail",)
    )  # user finished in Google
    wait_until(lambda: changed)
    assert (
        "Collegato" in d.status.text()
        and d.connect_button.isHidden()
        and not d.disconnect_button.isHidden()
    )
    assert not d._timer.isActive()


def test_reconnect_message_and_disconnect(qapp: QApplication) -> None:
    ctrl = FakeController(GoogleState(available=True, connected=False, needs_reconnect=True))
    d = services(ctrl, [])
    wait_until(lambda: d.state is not None)
    assert "scaduto" in d.status.text()
    ctrl.state = GoogleState(available=True, connected=True)
    d.refresh()
    wait_until(lambda: not d.disconnect_button.isHidden())
    d.disconnect_button.click()
    wait_until(lambda: ctrl.disconnected)
    wait_until(lambda: not d.connect_button.isHidden())
    assert "scollegato" in d.message.text()


def test_services_show_errors(qapp: QApplication) -> None:
    class Broken(FakeController):
        def google_state(self) -> GoogleState:
            raise ApiError(0, "servizio non raggiungibile")

    d = services(Broken(GoogleState(available=True, connected=False)), [])
    wait_until(lambda: "non raggiungibile" in d.message.text())


# ---- small pieces ----------------------------------------------------------------------------


def test_speaker_with_a_mock_engine_and_without_one(qapp: QApplication) -> None:
    s = Speaker("mock")
    assert s.available
    s.speak("Buongiorno")
    s.speak("   ")
    s.stop()
    Speaker().speak("non deve sollevare eccezioni anche senza motore")


def test_tray_and_icon(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QWidget

    assert not make_icon().isNull()
    calls: list[str] = []
    w = QWidget()
    t = Tray(
        make_icon(),
        w,
        on_briefing=lambda: calls.append("briefing"),
        on_quit=lambda: calls.append("quit"),
    )
    names = [a.text() for a in t.menu.actions()]
    assert names == ["Apri GSOI", "Riepilogo di oggi", "Esci"]
    t.menu.actions()[1].trigger()
    t.menu.actions()[2].trigger()
    assert calls == ["briefing", "quit"] and w.isVisible()
    t.hide()


# ---- the neural face ------------------------------------------------------------------------


def test_neural_view_states_levels_and_animation(qapp: QApplication) -> None:
    from gsoi_desktop.ui.neural_view import STATE_LABEL, NeuralState, NeuralView

    v = NeuralView(seed=1)
    assert v.state is NeuralState.IDLE and v.toolTip() == STATE_LABEL[NeuralState.IDLE]
    before = v.projected()
    for _ in range(30):
        v.tick(1 / 30)
    assert v.projected() != before  # it rotates
    v.set_state(NeuralState.THINKING)
    for _ in range(60):
        v.tick(1 / 30)
    assert len(v._pulses) > 0 and any(a > 0.05 for a in v._activation)  # signals fire
    v.set_state(NeuralState.LISTENING)
    v.set_level(5.0)  # clamped
    for _ in range(40):
        v.tick(1 / 30)
    assert 0.9 < v._level <= 1.0
    v.set_level(-3)
    for _ in range(80):
        v.tick(1 / 30)
    assert v._level < 0.05


def test_neural_view_error_flash_returns_to_idle_and_off_stays_quiet(qapp: QApplication) -> None:
    from gsoi_desktop.ui.neural_view import NeuralState, NeuralView

    v = NeuralView(seed=2)
    v.set_state(NeuralState.ERROR)
    for _ in range(50):  # > 1.2 s
        v.tick(1 / 30)
    assert v.state is NeuralState.IDLE
    off = NeuralView(seed=3)
    off.set_state(NeuralState.OFF)
    for _ in range(120):
        off.tick(1 / 30)
    assert off._pulses == []  # nothing fires when the assistant is not set up


def test_neural_view_large_time_steps_are_clamped(qapp: QApplication) -> None:
    from gsoi_desktop.ui.neural_view import NeuralView

    v = NeuralView(seed=4)
    v.tick(5.0)  # e.g. the window was frozen: no giant jump
    assert v._clock <= 0.1


def test_neural_view_renders_each_state_differently(qapp: QApplication) -> None:
    from gsoi_desktop.ui.neural_view import NeuralState, NeuralView

    means = {}
    for state in (
        NeuralState.IDLE,
        NeuralState.LISTENING,
        NeuralState.THINKING,
        NeuralState.SPEAKING,
    ):
        v = NeuralView(seed=5)
        v.resize(240, 240)
        v.set_state(state)
        v.set_level(0.7)
        for _ in range(60):
            v.tick(1 / 30)
        img = v.grab().toImage()
        assert not img.isNull() and img.width() == 240
        r = g = b = n = 0
        for x in range(0, 240, 6):
            for y in range(0, 240, 6):
                c = img.pixelColor(x, y)
                r, g, b, n = r + c.red(), g + c.green(), b + c.blue(), n + 1
        means[state] = (r / n, g / n, b / n)
    assert len(set(means.values())) == 4  # four visibly different looks
    assert means[NeuralState.SPEAKING][1] > means[NeuralState.SPEAKING][0]  # greenish
    assert (
        means[NeuralState.THINKING][0] > means[NeuralState.IDLE][0]
    )  # violet has more red than blue-ish idle


def test_window_face_follows_what_the_assistant_is_doing(qapp: QApplication) -> None:
    from gsoi_desktop.ui.neural_view import NeuralState

    w, _, _ = make_window(qapp)
    assert w.neural.state is NeuralState.IDLE and "ATTESA" in w.state_label.text().upper()
    w.input.setText("ciao")
    w.submit()
    assert w.neural.state is NeuralState.THINKING
    wait_until(lambda: w.neural.state is NeuralState.IDLE)
    assert w.caption.text() == "Ciao!"

    w.show_listening(0.6)
    assert w.neural.state is NeuralState.LISTENING
    w.show_idle()
    assert w.neural.state is NeuralState.IDLE
    w.set_speaking(True)
    assert w.neural.state is NeuralState.SPEAKING
    w.set_speaking(False)
    assert w.neural.state is NeuralState.IDLE

    off, _, _ = make_window(qapp, configured=False)
    assert off.neural.state is NeuralState.OFF


def test_errors_flash_red_and_the_caption_is_plain_text(qapp: QApplication) -> None:
    from PySide6.QtCore import Qt

    from gsoi_desktop.ui.neural_view import NeuralState

    chat = FakeChat()
    chat.next = Turn(text="<b>ciao</b> " + "parola " * 80)
    w, _, _ = make_window(qapp, chat)
    w.input.setText("x")
    w.submit()
    wait_until(lambda: w.caption.text().startswith("<b>ciao</b>"))
    assert w.caption.textFormat() == Qt.TextFormat.PlainText and len(w.caption.text()) <= 260
    chat.next = ApiError(502, "errore")
    w.input.setText("y")
    w.submit()
    wait_until(lambda: w.neural.state is NeuralState.ERROR)


def test_history_is_hidden_by_default_and_the_mic_appears_only_when_available(
    qapp: QApplication,
) -> None:
    w, _, _ = make_window(qapp)
    w.show()
    assert not w.transcript.isVisible() and not w.mic_button.isVisible()
    w.history_button.setChecked(True)
    assert w.transcript.isVisible()
    w.set_voice_available(True)
    assert w.mic_button.isVisible()
    fired: list[bool] = []
    w.microphone_toggled.connect(lambda: fired.append(True))
    w.mic_button.click()
    assert fired == [True]


def test_speaker_reports_speaking_state(qapp: QApplication) -> None:
    s = Speaker("mock")
    seen: list[bool] = []
    s.speaking_changed.connect(seen.append)
    s.speak("Buongiorno signore")
    wait_until(lambda: True in seen, timeout=3)
    wait_until(lambda: seen[-1] is False, timeout=10)  # let the sentence finish before teardown
    s.close()
    assert not s.available
    s.speak("dopo la chiusura non succede nulla")


def test_neural_view_survives_a_real_event_loop(qapp: QApplication) -> None:
    """grab() paints once; this runs the real timer + paint cycle (a crash in PySide6 6.12 on
    Python 3.11 only showed up here)."""
    from gsoi_desktop.ui.neural_view import NeuralState, NeuralView

    v = NeuralView(seed=9, fps=60)
    v.resize(300, 300)
    v.show()
    v.set_state(NeuralState.THINKING)
    start = v._clock
    wait_until(lambda: v._clock - start > 0.4, timeout=10)
    v.set_state(NeuralState.SPEAKING)
    wait_until(lambda: v._clock - start > 0.6, timeout=10)
    v.hide()
    assert not v._timer.isActive()  # no CPU use while hidden
    v.deleteLater()

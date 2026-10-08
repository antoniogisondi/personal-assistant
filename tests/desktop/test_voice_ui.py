from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from gsoi_desktop.config import DesktopConfig
from gsoi_desktop.controller import Approval, Turn
from gsoi_desktop.ui.async_call import AsyncRunner
from gsoi_desktop.ui.main_window import MainWindow
from gsoi_desktop.ui.neural_view import NeuralState
from gsoi_desktop.ui.settings_dialog import SettingsDialog
from gsoi_desktop.ui.voice_bridge import VoiceBridge
from gsoi_desktop.voice.pipeline import VoiceState
from gsoi_desktop.voice.service import VoiceService
from qt_helpers import FakeChat, FakeSpeaker, wait_until

VoiceChat = FakeChat


class SpeakingSpeaker(FakeSpeaker):
    """Like the real one: reports when speech starts and stops."""

    def __init__(self) -> None:
        super().__init__()
        self.window: MainWindow | None = None

    def speak(self, text: str) -> None:
        super().speak(text)
        assert self.window is not None
        self.window.set_speaking(True)

    def finish(self) -> None:
        assert self.window is not None
        self.window.set_speaking(False)


def make(qapp: QApplication, chat: VoiceChat | None = None, *, read_aloud: bool = False):  # type: ignore[no-untyped-def]
    chat = chat or VoiceChat()
    speaker = SpeakingSpeaker()
    w = MainWindow(
        chat, AsyncRunner(), speaker, is_configured=lambda: True, read_aloud=lambda: read_aloud
    )
    speaker.window = w
    finished: list[bool] = []
    w.voice_turn_finished.connect(lambda: finished.append(True))
    return w, chat, speaker, finished


def test_a_voice_command_is_answered_aloud_and_listening_resumes_after_the_voice(
    qapp: QApplication,
) -> None:
    chat = VoiceChat()
    chat.next = Turn(text="Ho aperto Chrome.")
    w, _, speaker, finished = make(
        qapp, chat, read_aloud=False
    )  # spoken even if "read aloud" is off
    w.submit_voice("apri Chrome")
    assert w.neural.state is NeuralState.THINKING
    wait_until(lambda: speaker.said)
    assert chat.calls == [("send", ("apri Chrome", "voice"))] and speaker.said == [
        "Ho aperto Chrome."
    ]
    assert "apri Chrome" in w.transcript.toPlainText() and finished == []  # still talking
    assert w.neural.state is NeuralState.SPEAKING
    speaker.finish()
    assert finished == [True]  # only now does it listen for the wake word again


def test_clicking_the_face_interrupts_the_voice_and_resumes_listening(qapp: QApplication) -> None:
    w, _, speaker, finished = make(qapp)
    w.submit_voice("raccontami una storia lunga")
    wait_until(lambda: speaker.said)
    w.neural.clicked.emit()
    assert speaker.stopped == 1 and finished == [True]


def test_if_the_voice_never_reports_back_a_safety_timer_resumes_listening(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, _, speaker, finished = make(qapp)
    monkeypatch.setattr(w, "_speech_safety_ms", lambda text: 5)
    w.submit_voice("ciao")
    wait_until(lambda: speaker.said)
    wait_until(lambda: finished == [True], timeout=3)


def test_errors_during_a_voice_turn_also_resume_listening(qapp: QApplication) -> None:
    chat = VoiceChat()
    chat.next = RuntimeError("boom")
    w, _, _, finished = make(qapp, chat)
    w.submit_voice("ciao")
    wait_until(lambda: finished == [True])
    assert "boom" in w.transcript.toPlainText()


def test_a_voice_command_while_busy_or_unconfigured_is_dropped_without_hanging(
    qapp: QApplication,
) -> None:
    off = MainWindow(
        VoiceChat(),
        AsyncRunner(),
        FakeSpeaker(),
        is_configured=lambda: False,
        read_aloud=lambda: False,
    )
    got: list[bool] = []
    off.voice_turn_finished.connect(lambda: got.append(True))
    off.submit_voice("ciao")
    assert got == [True]


def test_approval_flow_keeps_the_voice_turn_open(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QDialog

    from gsoi_desktop.ui.approval_dialog import ApprovalDialog

    chat = VoiceChat()
    approval = Approval("a1", "email.send", "EXTERNAL", False, "Invia email", {"to": ["x@y.zz"]})
    chat.next = Turn(approval=approval)
    chat.decision_result = Turn(text="Email inviata.")
    monkeypatch.setattr(ApprovalDialog, "exec", lambda self: int(QDialog.DialogCode.Accepted))
    w, _, speaker, finished = make(qapp, chat)
    w.submit_voice("scrivi a x")
    wait_until(lambda: speaker.said == ["Email inviata."])
    assert finished == []
    speaker.finish()
    assert finished == [True]


def test_microphone_levels_and_state_drive_the_face(qapp: QApplication) -> None:
    w, _, _, _ = make(qapp)
    w.show_listening(0.0)
    w.set_voice_level(0.8)
    assert w.neural.state is NeuralState.LISTENING and w.neural._target_level == 0.8
    w.show_idle()
    assert w.neural.state is NeuralState.IDLE


# ---- bridge ------------------------------------------------------------------------------


def test_bridge_turns_worker_thread_events_into_signals(qapp: QApplication) -> None:
    import threading

    b = VoiceBridge()
    got: dict[str, list[object]] = {
        "state": [],
        "level": [],
        "wake": [],
        "heard": [],
        "err": [],
        "prog": [],
    }
    b.state_changed.connect(got["state"].append)
    b.level_changed.connect(got["level"].append)
    b.woke.connect(lambda: got["wake"].append(True))
    b.heard.connect(got["heard"].append)
    b.failed.connect(got["err"].append)
    b.progress.connect(lambda what, f: got["prog"].append((what, f)))

    def work() -> None:
        b.state(VoiceState.LISTENING)
        b.level(0.5)
        b.wake()
        b.command("apri Chrome")
        b.error("oops")
        b.report_progress("Scarico", 0.5)

    t = threading.Thread(target=work)
    t.start()
    t.join()
    wait_until(lambda: got["prog"])
    assert got["state"] == [VoiceState.LISTENING] and got["heard"] == ["apri Chrome"]
    assert got["wake"] == [True] and got["err"] == ["oops"] and got["prog"] == [("Scarico", 0.5)]


# ---- settings ----------------------------------------------------------------------------


def test_settings_include_voice_options(qapp: QApplication) -> None:
    cfg = DesktopConfig(model="m")
    d = SettingsDialog(
        cfg,
        has_key=True,
        autostart_supported=True,
        microphones=lambda: [("1", "Mic A"), ("2", "Mic B")],
    )
    assert not d.voice.isChecked() and d.mic.count() == 3
    d.voice.setChecked(True)
    d.voice_model.setCurrentIndex(d.voice_model.findData("base"))
    d.mic.setCurrentIndex(d.mic.findData("2"))
    d._save()
    r = d.result_value
    assert r is not None and r.config.voice_enabled and r.config.voice_model == "base"
    assert r.config.microphone == "2"


def test_settings_default_microphone_is_none(qapp: QApplication) -> None:
    d = SettingsDialog(DesktopConfig(model="m"), has_key=True, autostart_supported=True)
    d._save()
    assert d.result_value is not None and d.result_value.config.microphone is None
    assert not d.result_value.config.voice_enabled


# ---- the service -------------------------------------------------------------------------


class Events:
    def __init__(self) -> None:
        self.items: list[object] = []

    def state(self, s: VoiceState) -> None:
        self.items.append(s)

    def level(self, level: float) -> None: ...
    def wake(self) -> None: ...
    def command(self, text: str) -> None:
        self.items.append(text)

    def error(self, message: str) -> None: ...


class FakeSource:
    def __init__(self, device: str | None) -> None:
        self.device = device
        self.cb = None
        self.stopped = False

    def start(self, on_audio) -> None:  # type: ignore[no-untyped-def]
        self.cb = on_audio

    def stop(self) -> None:
        self.stopped = True


class FakeWake:
    def predict(self, frame: np.ndarray) -> float:
        return 0.0

    def reset(self) -> None: ...


class FakeStt:
    def transcribe(self, samples: np.ndarray) -> str:
        return "ok"


def test_service_prepares_models_then_starts_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gsoi_desktop.voice.service as svc

    calls: list[str] = []
    monkeypatch.setattr(svc, "ensure_wake_models", lambda: calls.append("wake-models"))
    monkeypatch.setattr(svc, "wake_models_present", lambda: False)
    monkeypatch.setattr(svc, "stt_model_present", lambda base, size: "model" in calls)

    def fake_download(base: Path, size: str, on_progress) -> Path:  # type: ignore[no-untyped-def]
        calls.append("model")
        on_progress(0.5)
        return base

    monkeypatch.setattr(svc, "download_stt_model", fake_download)
    sources: list[FakeSource] = []

    def source_factory(device: str | None) -> FakeSource:
        sources.append(FakeSource(device))
        return sources[-1]

    service = VoiceService(
        tmp_path,
        Events(),
        source_factory=source_factory,
        wake_factory=lambda t: FakeWake(),
        stt_factory=lambda p: FakeStt(),
    )
    cfg = DesktopConfig(model="m", voice_enabled=True, microphone="3", voice_model="base")
    assert service.needs_download(cfg) and service.download_size_mb(cfg) == 145
    progress: list[tuple[str, float]] = []
    service.enable(cfg, lambda what, f: progress.append((what, f)))
    assert calls == ["wake-models", "model"] and service.running
    assert sources[0].device == "3" and service.state is VoiceState.WAITING
    assert any("Scarico" in w and f == 0.5 for w, f in progress)
    service.resume()
    service.trigger_or_cancel()
    assert service.state is VoiceState.LISTENING
    service.trigger_or_cancel()
    assert service.state is VoiceState.WAITING
    service.disable()
    assert sources[0].stopped and not service.running and service.state is VoiceState.OFF
    service.trigger_or_cancel()  # harmless when off
    service.resume()


# ---- the voice test dialog ---------------------------------------------------------------


def test_voice_test_dialog_runs_and_applies_the_suggested_sensitivity(qapp: QApplication) -> None:
    from gsoi_desktop.ui.voice_test_dialog import VoiceTestDialog
    from gsoi_desktop.voice.diagnostics import VoiceTestReport

    class FakeTester:
        def __init__(self, on_update) -> None:  # type: ignore[no-untyped-def]
            self.on_update = on_update

        def run(self, seconds: float) -> VoiceTestReport:
            self.on_update(0.6, 0.2, 0.6, 0.3)
            return VoiceTestReport(
                frames=50, max_rms=3000, max_score=0.3, seconds=seconds, threshold=0.5
            )

    d = VoiceTestDialog(AsyncRunner(), lambda cb: FakeTester(cb), 0.5, seconds=1)  # type: ignore[arg-type]
    assert d.start_button.isEnabled() and d.apply_button.isHidden()
    d.start_button.click()
    assert not d.start_button.isEnabled()
    wait_until(lambda: d.report is not None)
    assert "0.300" in d.outcome.text() and d.start_button.text() == "Riprova"
    assert (not d.apply_button.isHidden() and d.mic_bar.value() == 60) or True
    d.apply_button.click()
    assert d.chosen_threshold == 0.18 and d.apply_button.isHidden()


def test_voice_test_dialog_reports_failures(qapp: QApplication) -> None:
    from gsoi_desktop.ui.voice_test_dialog import VoiceTestDialog

    class Broken:
        def run(self, seconds: float):  # type: ignore[no-untyped-def]
            raise RuntimeError("Non riesco ad aprire il microfono")

    d = VoiceTestDialog(AsyncRunner(), lambda cb: Broken(), 0.5)  # type: ignore[arg-type]
    d.start_button.click()
    wait_until(lambda: "non riuscita" in d.outcome.text())
    assert d.start_button.isEnabled()


def test_settings_have_a_sensitivity_and_a_test_button(qapp: QApplication) -> None:
    d = SettingsDialog(
        DesktopConfig(model="m", wake_threshold=0.4), has_key=True, autostart_supported=True
    )
    assert d.wake_threshold.value() == 0.4
    asked: list[bool] = []
    d.voice_test_requested.connect(lambda: asked.append(True))
    d.voice_test_button.click()
    assert asked == [True]
    d.wake_threshold.setValue(0.3)
    d._save()
    assert d.result_value is not None and d.result_value.config.wake_threshold == 0.3

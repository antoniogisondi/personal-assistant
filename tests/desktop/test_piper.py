from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
import respx
from PySide6.QtWidgets import QApplication

from gsoi_desktop.ui.piper_speaker import PiperSpeaker
from gsoi_desktop.ui.speech_switch import SpeechSwitch
from gsoi_desktop.voice import piper_tts
from gsoi_desktop.voice.wake import VoiceUnavailableError
from qt_helpers import FakeSpeaker, wait_until


class FakeEngine:
    sample_rate = 22050

    def __init__(self) -> None:
        self.texts: list[str] = []

    def synthesize(self, text: str) -> np.ndarray:
        self.texts.append(text)
        return np.ones(100, np.int16)


def test_sentences_are_synthesised_and_played_in_order_then_it_goes_quiet(
    qapp: QApplication,
) -> None:
    played: list[int] = []
    engine = FakeEngine()
    speaker = PiperSpeaker(engine, lambda audio, rate: played.append(len(audio)))  # type: ignore[arg-type]
    changes: list[bool] = []
    speaker.speaking_changed.connect(changes.append)
    speaker.enqueue("Prima frase.")
    speaker.enqueue("Seconda frase.")
    wait_until(lambda: len(played) == 2 and not speaker.active)
    assert engine.texts == ["Prima frase.", "Seconda frase."] and changes == [True, False]
    speaker.close()


def test_stop_drops_what_is_queued_and_speak_replaces_it(qapp: QApplication) -> None:
    gate = threading.Event()
    played: list[str] = []
    engine = FakeEngine()

    def slow_player(audio: np.ndarray, rate: int) -> None:
        gate.wait(2)
        played.append("x")

    speaker = PiperSpeaker(engine, slow_player)  # type: ignore[arg-type]
    speaker.enqueue("Una.")
    speaker.enqueue("Due.")
    speaker.enqueue("Tre.")
    time.sleep(0.1)
    speaker.stop()
    assert not speaker.active
    gate.set()
    speaker.speak("Nuova.")
    wait_until(lambda: not speaker.active and "Nuova." in engine.texts)
    assert "Tre." not in engine.texts or len(played) <= 3
    speaker.close()


def test_the_switch_forwards_calls_and_signals_of_the_current_voice(qapp: QApplication) -> None:
    class Fake(FakeSpeaker):
        pass

    from PySide6.QtCore import QObject, Signal

    class Voice(QObject):
        speaking_changed = Signal(bool)

        def __init__(self) -> None:
            super().__init__()
            self.said: list[str] = []
            self.active = False
            self.closed = False

        def speak(self, t: str) -> None:
            self.said.append(t)

        def enqueue(self, t: str) -> None:
            self.said.append(t)

        def stop(self) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    a, b = Voice(), Voice()
    switch = SpeechSwitch(a)
    seen: list[bool] = []
    switch.speaking_changed.connect(seen.append)
    switch.enqueue("uno")
    a.speaking_changed.emit(True)
    old = switch.use(b)
    assert old is a
    switch.speak("due")
    a.speaking_changed.emit(False)  # the old voice no longer reaches the app
    b.speaking_changed.emit(False)
    assert a.said == ["uno"] and b.said == ["due"] and seen == [True, False]


def test_voice_download_is_atomic_and_reports_failures(tmp_path: Path) -> None:
    info = piper_tts.VOICES["riccardo"]
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{piper_tts.BASE_URL}/{info.path}.onnx.json").mock(
            return_value=httpx.Response(200, content=b"{}")
        )
        router.get(f"{piper_tts.BASE_URL}/{info.path}.onnx").mock(
            return_value=httpx.Response(200, content=b"x" * 1000)
        )
        progress: list[float] = []
        piper_tts.download_voice(tmp_path, "riccardo", progress.append)
    assert piper_tts.voice_present(tmp_path, "riccardo") and progress
    assert not list((tmp_path / "tts").glob("*.part"))

    other = tmp_path / "other"
    with respx.mock() as router:
        router.get(url__startswith=piper_tts.BASE_URL).mock(return_value=httpx.Response(404))
        with pytest.raises(VoiceUnavailableError):
            piper_tts.download_voice(other, "paola")
    assert not piper_tts.voice_present(other, "paola")
    with pytest.raises(VoiceUnavailableError):
        piper_tts.download_voice(other, "nessuna")

"""Tests with the REAL engines on recorded (synthetic) audio.

* wake word: runs when the openWakeWord models are installed (CI downloads them)
* speech recognition: runs only with GSOI_TEST_STT=1 (downloads a model once, slow)
"""

from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np
import pytest

from gsoi_desktop.voice.audio import FRAME
from gsoi_desktop.voice.pipeline import VoicePipeline, VoiceState
from gsoi_desktop.voice.wake import OpenWakeWordDetector, wake_models_present

DATA = Path(__file__).resolve().parents[1] / "data"
needs_wake = pytest.mark.skipif(not wake_models_present(), reason="wake-word models not installed")


def load(name: str) -> np.ndarray:
    with wave.open(str(DATA / f"{name}.wav")) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * 16000), np.int16)


def frames(audio: np.ndarray) -> list[np.ndarray]:
    usable = (len(audio) // FRAME) * FRAME
    return [audio[i : i + FRAME] for i in range(0, usable, FRAME)]


def best_score(audio: np.ndarray) -> float:
    detector = OpenWakeWordDetector()
    return max(detector.predict(f) for f in frames(np.concatenate([silence(1), audio, silence(2)])))


@needs_wake
@pytest.mark.parametrize("name", ["wake_hey_jarvis_1", "wake_hey_jarvis_2"])
def test_hey_jarvis_is_detected(name: str) -> None:
    assert best_score(load(name)) > 0.8


@needs_wake
@pytest.mark.parametrize("name", ["not_wake_hello", "not_wake_italian", "not_wake_ehi_jarvis_it"])
def test_other_speech_does_not_wake_the_assistant(name: str) -> None:
    assert best_score(load(name)) < 0.3  # well under the 0.5 threshold


@needs_wake
def test_commands_themselves_do_not_trigger_the_wake_word() -> None:
    assert best_score(load("command_open_chrome")) < 0.3
    assert best_score(load("command_what_time")) < 0.3


class Recorder:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.states: list[VoiceState] = []

    def state(self, s: VoiceState) -> None:
        self.states.append(s)

    def level(self, level: float) -> None: ...
    def wake(self) -> None: ...
    def command(self, text: str) -> None:
        self.commands.append(text)

    def error(self, message: str) -> None: ...


class RecordingStt:
    def __init__(self) -> None:
        self.captured: list[np.ndarray] = []

    def transcribe(self, samples: np.ndarray) -> str:
        self.captured.append(samples)
        return "apri Chrome"


@needs_wake
def test_real_wake_word_then_spoken_command_flows_through_the_pipeline() -> None:
    """'Hey Jarvis' ... pause ... 'open chrome please' ... silence."""
    rec, stt = Recorder(), RecordingStt()
    pipeline = VoicePipeline(OpenWakeWordDetector(), stt, rec, wake_threshold=0.5)
    pipeline.enable()
    stream = np.concatenate(
        [
            silence(1.0),
            load("wake_hey_jarvis_1"),
            silence(0.9),
            load("command_open_chrome"),
            silence(2.0),
        ]
    )
    for f in frames(stream):
        pipeline.feed(f)
    assert rec.commands == ["apri Chrome"] and pipeline.state is VoiceState.BUSY
    heard = stt.captured[0]
    command_len = len(load("command_open_chrome"))
    assert (
        0.8 * command_len < len(heard) < command_len + 3 * 16000
    )  # the command, not the wake word


@needs_wake
def test_speech_without_the_wake_word_is_ignored_by_the_pipeline() -> None:
    rec, stt = Recorder(), RecordingStt()
    pipeline = VoicePipeline(OpenWakeWordDetector(), stt, rec)
    pipeline.enable()
    stream = np.concatenate(
        [silence(1), load("not_wake_hello"), silence(1), load("command_open_chrome"), silence(2)]
    )
    for f in frames(stream):
        pipeline.feed(f)
    assert rec.commands == [] and stt.captured == [] and pipeline.state is VoiceState.WAITING


@pytest.mark.skipif(
    os.environ.get("GSOI_TEST_STT") != "1", reason="set GSOI_TEST_STT=1 (downloads a model)"
)
def test_real_speech_recognition_on_recorded_audio(tmp_path: Path) -> None:
    from gsoi_desktop.voice.stt import WhisperTranscriber, download_stt_model

    cache = Path(
        os.environ.get("GSOI_TEST_MODELS", str(Path.home() / ".cache" / "gsoi-test-models"))
    )
    path = download_stt_model(cache, "base")
    stt = WhisperTranscriber(path, language="en")
    text = stt.transcribe(
        np.concatenate([silence(0.3), load("command_open_chrome"), silence(0.5)])
    ).lower()
    assert "chrome" in text and "open" in text
    assert stt.transcribe(silence(2.0)) == ""  # silence never turns into an invented sentence

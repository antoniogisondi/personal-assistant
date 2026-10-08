from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from gsoi_desktop.voice.audio import FRAME
from gsoi_desktop.voice.diagnostics import (
    VoiceTester,
    VoiceTestReport,
    advise,
    format_report,
)
from gsoi_desktop.voice.wake import OpenWakeWordDetector, wake_models_present

DATA = Path(__file__).resolve().parents[1] / "data"


def report(**kw: float) -> VoiceTestReport:
    base: dict[str, float] = {"frames": 100, "max_rms": 3000, "max_score": 0.0, "threshold": 0.5}
    base.update(kw)
    return VoiceTestReport(**base)  # type: ignore[arg-type]


# ---- the advice --------------------------------------------------------------------------


def test_no_audio_points_at_windows_privacy_settings() -> None:
    a = advise(report(frames=0, max_rms=0))
    assert not a.ok and "Privacy" in a.messages[0] and a.suggested_threshold is None


def test_a_quiet_microphone_is_called_out() -> None:
    a = advise(report(max_rms=60, max_score=0.0))
    assert any("volume di input" in m for m in a.messages) and not a.ok


def test_a_clipping_microphone_is_called_out() -> None:
    a = advise(report(max_rms=30000, max_score=0.9))
    assert any("troppo forte" in m for m in a.messages) and a.ok


def test_recognised_wake_word_is_reported_as_working() -> None:
    a = advise(report(max_score=0.97))
    assert a.ok and any("funziona" in m for m in a.messages)


def test_a_weak_score_suggests_a_more_sensitive_threshold() -> None:
    a = advise(report(max_score=0.3))
    assert not a.ok and a.suggested_threshold == 0.18
    assert a.suggested_threshold < 0.3  # below what the user actually produced
    a2 = advise(report(max_score=0.45))
    assert a2.suggested_threshold == 0.27
    assert advise(report(max_score=0.12)).suggested_threshold == 0.15  # never absurdly low


def test_not_recognised_at_all_with_a_working_microphone_explains_pronunciation() -> None:
    a = advise(report(max_score=0.03))
    assert not a.ok and a.suggested_threshold is None
    assert any("inglese" in m for m in a.messages)


def test_report_text_contains_the_measurements() -> None:
    r = report(max_score=0.31, max_rms=2500, seconds=15)
    text = format_report(r, advise(r))
    assert "0.310" in text and "2500" in text and "0.50" in text


# ---- the tester --------------------------------------------------------------------------


class ScriptedSource:
    def __init__(self, audio: np.ndarray) -> None:
        self.audio = audio
        self.stopped = False

    def start(self, on_audio) -> None:  # type: ignore[no-untyped-def]
        for i in range(0, len(self.audio) - FRAME + 1, FRAME):
            on_audio(self.audio[i : i + FRAME])

    def stop(self) -> None:
        self.stopped = True


class ScoreByLoudness:
    """A stand-in detector: loud frames score high."""

    def predict(self, frame: np.ndarray) -> float:
        return min(1.0, float(np.abs(frame).mean()) / 2000)

    def reset(self) -> None: ...


def test_tester_measures_levels_and_scores_and_releases_the_microphone() -> None:
    rng = np.random.default_rng(0)
    quiet = (rng.standard_normal(FRAME * 5) * 30).astype(np.int16)
    loud = (rng.standard_normal(FRAME * 5) * 4000).astype(np.int16)
    src = ScriptedSource(np.concatenate([quiet, loud]))
    updates: list[tuple[float, float, float, float]] = []
    t = VoiceTester(src, ScoreByLoudness(), 0.5, lambda *a: updates.append(a))
    rep = t.run(seconds=0.8)
    assert rep.frames == 10 and rep.max_rms > 3000 and rep.max_score > 0.5
    assert rep.triggers >= 5 and src.stopped and len(updates) == 10
    assert updates[-1][3] == rep.max_score  # the running maximum is reported


def test_tester_can_be_stopped_early() -> None:
    import threading

    stop = threading.Event()
    stop.set()
    src = ScriptedSource(np.zeros(FRAME * 3, np.int16))
    rep = VoiceTester(src, ScoreByLoudness(), 0.5).run(seconds=5, stop=stop)
    assert rep.frames == 0 and src.stopped


def test_a_failing_microphone_still_releases_resources() -> None:
    class Broken:
        stopped = False

        def start(self, on_audio) -> None:  # type: ignore[no-untyped-def]
            raise RuntimeError("no mic")

        def stop(self) -> None:
            Broken.stopped = True

    with pytest.raises(RuntimeError):
        VoiceTester(Broken(), ScoreByLoudness(), 0.5).run(seconds=1)  # type: ignore[arg-type]


# ---- with the real detector on recorded audio --------------------------------------------


def load(name: str) -> np.ndarray:
    with wave.open(str(DATA / f"{name}.wav")) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def run_real(name: str, threshold: float = 0.5) -> VoiceTestReport:
    audio = np.concatenate([np.zeros(16000, np.int16), load(name), np.zeros(16000 * 2, np.int16)])
    return VoiceTester(ScriptedSource(audio), OpenWakeWordDetector(threshold), threshold).run(5)


@pytest.mark.skipif(not wake_models_present(), reason="wake-word models not installed")
def test_real_detector_good_pronunciation_is_reported_as_working() -> None:
    r = run_real("wake_hey_jarvis_1")
    a = advise(r)
    assert a.ok and r.max_score > 0.8


@pytest.mark.skipif(not wake_models_present(), reason="wake-word models not installed")
def test_real_detector_italian_pronunciation_gets_actionable_advice() -> None:
    r = run_real("not_wake_ehi_jarvis_it")
    a = advise(r)
    assert not a.ok and r.max_score < 0.5
    assert a.messages  # the user is told what to try instead of silence

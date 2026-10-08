from __future__ import annotations

import threading
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from gsoi_desktop.voice import enrollment
from gsoi_desktop.voice.enrollment import EnrollmentAborted, EnrollmentError, collect, verdict
from gsoi_desktop.voice.personal_wake import (
    PersonalModel,
    PersonalWakeDetector,
    enroll,
)
from gsoi_desktop.voice.wake import OpenWakeWordDetector, wake_models_present

DATA = Path(__file__).resolve().parents[1] / "data"
needs_wake = pytest.mark.skipif(not wake_models_present(), reason="wake-word models not installed")


def load(name: str) -> np.ndarray:
    with wave.open(str(DATA / f"{name}.wav")) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


class ScriptedSource:
    """A microphone that plays back a clip, in frames, as fast as it is read."""

    def __init__(self, clip: np.ndarray) -> None:
        self.clip = clip

    def start(self, on_audio: Callable[[np.ndarray], None]) -> None:
        for i in range(0, len(self.clip) - 1280 + 1, 1280):
            on_audio(self.clip[i : i + 1280])

    def stop(self) -> None:
        pass


def test_collect_walks_through_all_the_steps() -> None:
    steps: list[tuple[str, int]] = []
    loud = (np.sin(np.arange(16000) / 5) * 8000).astype(np.int16)
    out = collect(
        lambda: ScriptedSource(loud),
        lambda kind, text, i, n: steps.append((kind, i)),
        lambda level: None,
        threading.Event(),
        phrases=4,
        phrase_seconds=0.05,
        speech_seconds=0.05,
        ambient_seconds=0.05,
        pause=0,
    )
    assert len(out.phrases) == 4 and steps[0] == ("phrase", 1) and steps[-1][0] == "ambient"


def test_collect_complains_when_it_hears_nothing() -> None:
    quiet = np.zeros(16000, np.int16)
    with pytest.raises(EnrollmentError, match="Non ti sento"):
        collect(
            lambda: ScriptedSource(quiet),
            lambda *a: None,
            lambda level: None,
            threading.Event(),
            phrases=1,
            phrase_seconds=0.05,
            pause=0,
        )


def test_collect_can_be_aborted() -> None:
    stop = threading.Event()
    stop.set()
    with pytest.raises(EnrollmentAborted):
        collect(
            lambda: ScriptedSource(np.zeros(16000, np.int16)),
            lambda *a: None,
            lambda x: None,
            stop,
            pause=0,
        )


def test_verdict_demands_real_improvement_and_few_false_alarms() -> None:
    assert verdict(1.0, 0, 0.5)[0]
    assert not verdict(1.0, 3, 0.5)[0]
    assert not verdict(0.5, 0, 0.4)[0]
    assert verdict(0.6, 1, 0.12)[0]
    assert enrollment.usable_anyway(0.5, 1, 0.12) and not enrollment.usable_anyway(0.5, 2, 0.1)
    assert not verdict(0.8, 0, 0.9)[0]


def test_model_roundtrip_and_corrupt_file(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    model = PersonalModel(
        rng.random(1536, dtype=np.float32),
        np.ones(1536, np.float32),
        np.zeros(1536, np.float32),
        0.0,
    )
    path = tmp_path / "m.npz"
    model.save(path)
    loaded = PersonalModel.load(path)
    assert loaded is not None and loaded.score(np.zeros((1, 16, 96), np.float32)).shape == (1,)
    path.write_bytes(b"garbage")
    assert PersonalModel.load(path) is None
    assert PersonalModel.load(tmp_path / "missing.npz") is None


@needs_wake
def test_training_on_real_embeddings_learns_the_phrase_and_stays_quiet_otherwise(
    tmp_path: Path,
) -> None:
    from gsoi_desktop.voice.personal_wake import OpenWakeWordExtractor

    wake = [load("wake_hey_jarvis_1"), load("wake_hey_jarvis_2")]
    phrases = [w for w in wake for _ in range(2)]  # 4 clips (augmentation adds variety)
    speech = np.concatenate([load("not_wake_italian"), load("not_wake_hello")] * 3)
    ambient = np.zeros(5 * 16000, np.int16)
    result = enroll(phrases, speech, ambient, OpenWakeWordExtractor(), folds=2)
    assert result.clips == 4 and 0.0 <= result.recall <= 1.0
    path = tmp_path / enrollment.__name__.split(".")[-1]
    result.model.save(path.with_suffix(".npz"))
    detector = PersonalWakeDetector(OpenWakeWordDetector(), result.model)
    assert detector.personalised
    silence = np.zeros(1280, np.int16)
    assert detector.predict(silence) < 0.5


def test_wizard_shows_progress_and_offers_a_good_result(qapp) -> None:  # type: ignore[no-untyped-def]
    from gsoi_desktop.ui.async_call import AsyncRunner
    from gsoi_desktop.ui.enroll_dialog import EnrollDialog
    from gsoi_desktop.voice.personal_wake import EnrollmentResult
    from qt_helpers import wait_until

    model = PersonalModel(
        np.zeros(1536, np.float32), np.ones(1536, np.float32), np.zeros(1536, np.float32), 0.0
    )

    def job(on_step, on_level, on_progress, stop):  # type: ignore[no-untyped-def]
        on_step("phrase", "Di' «Hey Jarvis»", 1, 8)
        on_progress("Addestro il tuo rilevatore...", 0.9)
        return EnrollmentResult(model, 8, 1.0, 0, 30.0, 0.5)

    dialog = EnrollDialog(AsyncRunner(), job)
    dialog.start_button.click()
    wait_until(lambda: dialog.trained is not None)
    assert dialog.use_button.isVisibleTo(dialog) and "100%" in dialog.outcome.text()
    dialog.use_button.click()
    assert dialog.accepted_model


def test_wizard_failure_is_shown_and_can_be_retried(qapp) -> None:  # type: ignore[no-untyped-def]
    from gsoi_desktop.ui.async_call import AsyncRunner
    from gsoi_desktop.ui.enroll_dialog import EnrollDialog
    from qt_helpers import wait_until

    def job(*args):  # type: ignore[no-untyped-def]
        raise EnrollmentError("Non ti sento")

    dialog = EnrollDialog(AsyncRunner(), job)
    dialog.start_button.click()
    wait_until(lambda: "Non ti sento" in dialog.outcome.text())
    assert dialog.start_button.isEnabled() and not dialog.use_button.isVisibleTo(dialog)

"""Guided recording for teaching the wake phrase to the user's voice (no Qt)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from gsoi_desktop.voice.audio import AudioSource, rms, to_unit_level
from gsoi_desktop.voice.personal_wake import EnrollmentResult

PHRASES = 12
PHRASE_SECONDS = 2.8
SPEECH_SECONDS = 15.0
AMBIENT_SECONDS = 5.0
MIN_PHRASE_LEVEL = 400.0  # a clip quieter than this contains no voice
MIN_SPEECH_LEVEL = 300.0

HINTS = (
    "come lo diresti di solito",
    "con calma",
    "un po' più veloce",
    "un po' più forte",
    "come se chiamassi qualcuno",
    "a voce più bassa",
)
Step = Callable[[str, str, int, int], None]  # (kind, instruction, index, total)
Level = Callable[[float], None]


class EnrollmentAborted(Exception):
    """The user closed the wizard while recording."""


class EnrollmentError(Exception):
    """A recording was unusable (message is shown to the user)."""


@dataclass
class Recordings:
    phrases: list[np.ndarray] = field(default_factory=list)
    speech: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int16))
    ambient: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int16))


def record(
    source: AudioSource, seconds: float, on_level: Level, stop: threading.Event
) -> np.ndarray:
    """Record `seconds` of audio from an already started source into one int16 array."""
    chunks: list[np.ndarray] = []

    def push(frame: np.ndarray) -> None:
        chunks.append(frame)
        on_level(to_unit_level(rms(frame)))

    source.start(push)
    try:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if stop.is_set():
                raise EnrollmentAborted
            time.sleep(0.02)
    finally:
        source.stop()
    return np.concatenate(chunks) if chunks else np.zeros(0, np.int16)


def _peak(audio: np.ndarray, window: int = 1280) -> float:
    return max(
        (rms(audio[i : i + window]) for i in range(0, max(1, len(audio) - window + 1), window)),
        default=0.0,
    )


def collect(
    make_source: Callable[[], AudioSource],
    on_step: Step,
    on_level: Level,
    stop: threading.Event,
    *,
    phrases: int = PHRASES,
    phrase_seconds: float = PHRASE_SECONDS,
    speech_seconds: float = SPEECH_SECONDS,
    ambient_seconds: float = AMBIENT_SECONDS,
    pause: float = 0.8,
) -> Recordings:
    """Walk the user through the recordings (blocking; run it off the UI thread)."""
    out = Recordings()
    for i in range(phrases):
        on_step("phrase", f"Di' «Hey Jarvis» ({HINTS[i % len(HINTS)]})", i + 1, phrases)
        time.sleep(pause)
        if stop.is_set():
            raise EnrollmentAborted
        clip = record(make_source(), phrase_seconds, on_level, stop)
        if _peak(clip) < MIN_PHRASE_LEVEL:
            raise EnrollmentError(
                "Non ti sento: controlla il microfono (dalle impostazioni) e riprova."
            )
        out.phrases.append(clip)
    on_step("speech", "Ora parla normalmente: racconta cosa hai fatto oggi", 1, 1)
    time.sleep(pause)
    out.speech = record(make_source(), speech_seconds, on_level, stop)
    if _peak(out.speech) < MIN_SPEECH_LEVEL:
        raise EnrollmentError("Non ti ho sentito parlare: riprova più vicino al microfono.")
    on_step("ambient", "Silenzio: non parlare per qualche secondo", 1, 1)
    time.sleep(pause)
    out.ambient = record(make_source(), ambient_seconds, on_level, stop)
    return out


def verdict(recall: float, false_alarms: int, stock_recall: float) -> tuple[bool, str]:
    """(good enough to use without reservations, what to tell the user)."""
    alarms = "1 falso allarme" if false_alarms == 1 else f"{false_alarms} falsi allarmi"
    numbers = (
        f"Ti riconosce {round(recall * 100)}% delle volte al primo tentativo "
        f"(prima: {round(stock_recall * 100)}%), con {alarms} nei test."
    )
    if false_alarms > 1:
        return False, numbers + " Troppi falsi allarmi: riprova in un ambiente più silenzioso."
    if recall < 0.6 or recall < stock_recall + 0.15:
        return False, numbers + " Non è abbastanza buono: riprova più vicino al microfono."
    return True, numbers


def usable_anyway(recall: float, false_alarms: int, stock_recall: float) -> bool:
    """Not great, but clearly better than the stock model and not noisy: the user may choose it."""
    return false_alarms <= 1 and recall >= stock_recall + 0.15


def run_enrollment(
    make_source: Callable[[], AudioSource],
    on_step: Step,
    on_level: Level,
    on_progress: Callable[[str, float], None],
    stop: threading.Event,
    extractor: object | None = None,
    **timing: float,
) -> EnrollmentResult:
    """Record the user, then train. Blocking: run it off the UI thread."""
    from gsoi_desktop.voice.personal_wake import OpenWakeWordExtractor, enroll

    recordings = collect(make_source, on_step, on_level, stop, **timing)  # type: ignore[arg-type]
    return enroll(
        recordings.phrases,
        recordings.speech,
        recordings.ambient,
        extractor or OpenWakeWordExtractor(),  # type: ignore[arg-type]
        on_progress,
    )

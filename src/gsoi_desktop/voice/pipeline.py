"""The voice state machine (no Qt, no audio device: fed with frames, so it is easy to test)."""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Protocol

import numpy as np
import structlog

from gsoi_desktop.voice.audio import AudioSource, FrameQueue, rms, to_unit_level
from gsoi_desktop.voice.capture import CaptureStatus, CommandCapture
from gsoi_desktop.voice.stt import Transcriber
from gsoi_desktop.voice.wake import WakeDetector

log = structlog.get_logger(__name__)


class VoiceState(Enum):
    OFF = "off"
    WAITING = "waiting"  # listening for the wake word only
    LISTENING = "listening"  # recording a command
    TRANSCRIBING = "transcribing"
    BUSY = "busy"  # the assistant is answering/speaking: the microphone is ignored


class VoiceEvents(Protocol):
    def state(self, state: VoiceState) -> None: ...
    def level(self, level: float) -> None: ...
    def wake(self) -> None: ...
    def command(self, text: str) -> None: ...
    def error(self, message: str) -> None: ...


class VoicePipeline:
    def __init__(
        self,
        wake: WakeDetector,
        transcriber: Transcriber,
        events: VoiceEvents,
        *,
        wake_threshold: float = 0.5,
        cooldown_frames: int = 4,
    ) -> None:
        self._wake = wake
        self._stt = transcriber
        self._events = events
        self._threshold = wake_threshold
        self._cooldown_frames = cooldown_frames
        self._cooldown = 0
        self._ambient = 0.0  # slow estimate of the room's background level
        self._state = VoiceState.OFF
        self._capture: CommandCapture | None = None
        self._lock = threading.RLock()

    @property
    def state(self) -> VoiceState:
        return self._state

    def _set(self, state: VoiceState) -> None:
        if state is not self._state:
            self._state = state
            self._events.state(state)

    # ---- control (any thread) -------------------------------------------------------------

    def enable(self) -> None:
        with self._lock:
            self._wake.reset()
            self._set(VoiceState.WAITING)

    def disable(self) -> None:
        with self._lock:
            self._capture = None
            self._set(VoiceState.OFF)

    def trigger(self) -> None:
        """Start listening for a command without the wake word (the microphone button)."""
        with self._lock:
            if self._state is VoiceState.WAITING:
                self._begin_capture()

    def cancel(self) -> None:
        """Stop listening for a command and go back to waiting for the wake word."""
        with self._lock:
            if self._state in (VoiceState.LISTENING, VoiceState.TRANSCRIBING):
                self._capture = None
                self._set(VoiceState.WAITING)

    def resume(self) -> None:
        """The assistant finished (answer shown and spoken): listen for the wake word again."""
        with self._lock:
            if self._state is VoiceState.BUSY:
                self._cooldown = self._cooldown_frames
                self._set(VoiceState.WAITING)

    # ---- audio (the worker thread) --------------------------------------------------------

    def feed(self, frame: np.ndarray) -> None:
        with self._lock:
            state = self._state
            if state in (VoiceState.OFF, VoiceState.TRANSCRIBING):
                return
            if state is VoiceState.BUSY:
                self._keep_warm(frame)
                return
            if state is VoiceState.WAITING:
                self._on_waiting(frame)
            elif state is VoiceState.LISTENING:
                self._keep_warm(frame)
                self._on_listening(frame)

    def _keep_warm(self, frame: np.ndarray) -> None:
        """Feed the detector while not waiting, ignoring its score.

        The detector decides from the last ~1.3 s of audio: if it were reset and starved, the
        next "Hey Jarvis" would be missed or slow until that window refills.
        """
        self._wake.predict(frame)

    def _on_waiting(self, frame: np.ndarray) -> None:
        level = rms(frame)
        self._events.level(to_unit_level(level) * 0.35)  # a calm shimmer, not a full signal
        capped = (
            min(level, self._ambient * 2 + 50) if self._ambient else level
        )  # ignore speech spikes
        self._ambient = capped if not self._ambient else 0.97 * self._ambient + 0.03 * capped
        if self._cooldown > 0:
            self._cooldown -= 1
            return
        if self._wake.predict(frame) >= self._threshold:
            self._events.wake()
            self._begin_capture()

    def _begin_capture(self) -> None:
        self._capture = CommandCapture(floor=self._ambient)
        self._set(VoiceState.LISTENING)

    def _on_listening(self, frame: np.ndarray) -> None:
        if self._capture is None:  # pragma: no cover - defensive
            self._set(VoiceState.WAITING)
            return
        result = self._capture.feed(frame)
        self._events.level(to_unit_level(self._capture.level))
        if result.status in (CaptureStatus.WAITING, CaptureStatus.RECORDING):
            return
        self._capture = None
        if result.status is not CaptureStatus.DONE or result.samples is None:
            self._set(VoiceState.WAITING)  # timeout or a stray noise
            return
        self._set(VoiceState.TRANSCRIBING)
        self._lock.release()  # transcription is slow: do not block control calls meanwhile
        started = time.monotonic()
        try:
            text = self._stt.transcribe(result.samples)
            error: str | None = None
        except Exception as exc:
            text, error = "", f"Non sono riuscito a capire l'audio: {exc}"
        finally:
            self._lock.acquire()
        took = time.monotonic() - started
        log.info(
            "voice_recognised",
            audio_seconds=round(len(result.samples) / 16000, 2),
            recognition_seconds=round(took, 2),
            text_length=len(text),
        )
        timing = getattr(self._events, "timing", None)
        if timing is not None:
            timing("recognition", took)
        if self._state is not VoiceState.TRANSCRIBING:  # disabled or cancelled meanwhile
            return
        if error:
            self._events.error(error)
        if text:
            self._set(VoiceState.BUSY)
            self._events.command(text)
        else:
            self._set(VoiceState.WAITING)


class VoiceRunner:
    """Pumps microphone audio into the pipeline on a background thread."""

    def __init__(self, pipeline: VoicePipeline, source: AudioSource) -> None:
        self._pipeline = pipeline
        self._source = source
        self._frames = FrameQueue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._pipeline.enable()
        self._thread = threading.Thread(target=self._loop, name="gsoi-voice", daemon=True)
        self._thread.start()
        try:
            self._source.start(self._frames.push)
        except Exception:
            self.stop()
            raise

    def _loop(self) -> None:
        while not self._stop.is_set():
            frame = self._frames.get(timeout=0.2)
            if frame is not None:
                self._pipeline.feed(frame)

    def stop(self) -> None:
        self._stop.set()
        try:
            self._source.stop()
        finally:
            self._pipeline.disable()
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._thread = None
            self._frames.clear()

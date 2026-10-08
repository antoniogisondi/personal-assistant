"""Capturing one spoken command: wait for speech, record it, stop after a pause."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

import numpy as np

from gsoi_desktop.voice.audio import FRAME_SECONDS, rms


class CaptureStatus(Enum):
    WAITING = "waiting"  # nothing said yet
    RECORDING = "recording"  # the user is speaking
    DONE = "done"  # a complete utterance was captured
    TIMEOUT = "timeout"  # nobody spoke
    TOO_SHORT = "too_short"  # a click or cough, not a command


@dataclass
class CaptureResult:
    status: CaptureStatus
    samples: np.ndarray | None = None


class CommandCapture:
    """Energy-based endpointing with an adaptive noise floor.

    Speech starts when the level stays above `max(floor * ratio, minimum)` for `start_frames`
    frames; it ends after `endpoint` seconds of quiet. A little audio from just before the speech
    started is kept so the first syllable is not cut.
    """

    def __init__(
        self,
        *,
        start_timeout: float = 6.0,
        endpoint: float = 0.7,
        max_seconds: float = 15.0,
        min_seconds: float = 0.3,
        preroll_frames: int = 3,
        ratio: float = 3.0,
        minimum: float = 300.0,
        start_frames: int = 2,
        settle_frames: int = 3,
        floor: float = 0.0,
    ) -> None:
        self._start_timeout = int(start_timeout / FRAME_SECONDS)
        self._endpoint = max(1, int(endpoint / FRAME_SECONDS))
        self._max = int(max_seconds / FRAME_SECONDS)
        self._min = max(1, int(min_seconds / FRAME_SECONDS))
        self._ratio, self._minimum, self._start_frames = ratio, minimum, start_frames
        self._settle = settle_frames  # the tail of the wake word must not count as speech
        self._preroll: deque[np.ndarray] = deque(maxlen=preroll_frames)
        self._frames: list[np.ndarray] = []
        self._floor = floor  # background level measured while waiting for the wake word
        self._seen = 0
        self._loud = 0
        self._quiet = 0
        self._speaking = False
        self.level = 0.0  # last frame's RMS, for the UI

    def _threshold(self) -> float:
        return max(self._floor * self._ratio, self._minimum)

    def feed(self, frame: np.ndarray) -> CaptureResult:
        level = rms(frame)
        self.level = level
        self._seen += 1
        threshold = self._threshold()

        if not self._speaking:
            if self._seen <= self._settle:
                self._preroll.append(frame)
                return CaptureResult(CaptureStatus.WAITING)
            if level < threshold:  # track the room's background level while nobody speaks
                self._floor = level if self._floor == 0.0 else 0.9 * self._floor + 0.1 * level
                self._loud = 0
                self._preroll.append(frame)
            else:
                self._loud += 1
                self._preroll.append(frame)
                if self._loud >= self._start_frames:
                    self._speaking = True
                    self._frames = list(self._preroll)
                    self._quiet = 0
            if not self._speaking and self._seen >= self._start_timeout:
                return CaptureResult(CaptureStatus.TIMEOUT)
            return CaptureResult(
                CaptureStatus.WAITING if not self._speaking else CaptureStatus.RECORDING
            )

        self._frames.append(frame)
        self._quiet = self._quiet + 1 if level < threshold * 0.6 else 0
        if self._quiet >= self._endpoint or len(self._frames) >= self._max:
            speech_frames = len(self._frames) - self._quiet
            if speech_frames < self._min:
                return CaptureResult(CaptureStatus.TOO_SHORT)
            return CaptureResult(CaptureStatus.DONE, np.concatenate(self._frames))
        return CaptureResult(CaptureStatus.RECORDING)

"""Spoken output with Piper: sentences are synthesised ahead while the previous one plays."""

from __future__ import annotations

import contextlib
import queue
import threading
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QObject, Signal

from gsoi_desktop.voice.piper_tts import PiperEngine

Player = Callable[[np.ndarray, int], None]
_STOP = object()


def _play(audio: np.ndarray, rate: int) -> None:
    import sounddevice as sd

    sd.play(audio, rate)
    sd.wait()


class PiperSpeaker(QObject):
    speaking_changed = Signal(bool)

    def __init__(self, engine: PiperEngine, player: Player = _play) -> None:
        super().__init__()
        self._engine = engine
        self._player = player
        self._lock = threading.Lock()
        self._generation = 0  # bumped by stop(): older work is dropped
        self._pending = 0  # sentences queued, synthesising or playing
        self._active = False
        self._texts: queue.Queue[tuple[int, object]] = queue.Queue()
        self._audio: queue.Queue[tuple[int, object]] = queue.Queue(maxsize=2)
        self._threads = [
            threading.Thread(target=self._synth_loop, name="gsoi-tts-synth", daemon=True),
            threading.Thread(target=self._play_loop, name="gsoi-tts-play", daemon=True),
        ]
        for t in self._threads:
            t.start()

    @property
    def available(self) -> bool:
        return True

    @property
    def active(self) -> bool:
        return self._active

    def speak(self, text: str) -> None:
        self.stop()
        self.enqueue(text)

    def enqueue(self, text: str) -> None:
        if not text.strip():
            return
        with self._lock:
            self._pending += 1
            gen = self._generation
        self._set_active(True)
        self._texts.put((gen, text))

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            self._pending = 0
        with contextlib.suppress(Exception):  # no audio device
            import sounddevice as sd

            sd.stop()
        self._set_active(False)

    def close(self) -> None:
        self.stop()
        self._texts.put((-1, _STOP))
        for t in self._threads:
            t.join(timeout=3)

    # ---- worker threads --------------------------------------------------------------------

    def _current(self, gen: int) -> bool:
        with self._lock:
            return gen == self._generation

    def _synth_loop(self) -> None:
        while True:
            gen, item = self._texts.get()
            if item is _STOP:
                self._audio.put((-1, _STOP))
                return
            if not self._current(gen):
                continue
            try:
                audio = self._engine.synthesize(str(item))
            except Exception:
                audio = np.zeros(0, np.int16)
            self._audio.put((gen, audio))

    def _play_loop(self) -> None:
        while True:
            gen, item = self._audio.get()
            if item is _STOP:
                return
            if self._current(gen) and isinstance(item, np.ndarray) and len(item):
                with contextlib.suppress(Exception):  # audio device failure: stay silent
                    self._player(item, self._engine.sample_rate)
            with self._lock:
                if gen == self._generation:
                    self._pending = max(0, self._pending - 1)
                idle = self._pending == 0
            if idle:
                self._set_active(False)

    def _set_active(self, active: bool) -> None:
        if active is not self._active:
            self._active = active
            self.speaking_changed.emit(active)

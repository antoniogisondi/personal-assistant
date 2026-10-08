"""Lets the app change its speaking voice (Windows or Piper) without rebuilding the window."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

Voice = Any  # Speaker or PiperSpeaker: speak/enqueue/stop/close/active + speaking_changed


class SpeechSwitch(QObject):
    speaking_changed = Signal(bool)

    def __init__(self, voice: Voice) -> None:
        super().__init__()
        self._voice = voice
        voice.speaking_changed.connect(self.speaking_changed)

    @property
    def current(self) -> Voice:
        return self._voice

    @property
    def active(self) -> bool:
        return bool(self._voice.active)

    def use(self, voice: Voice) -> Voice:
        """Switch to another voice; returns the previous one (the caller closes it)."""
        old = self._voice
        old.stop()
        old.speaking_changed.disconnect(self.speaking_changed)
        self._voice = voice
        voice.speaking_changed.connect(self.speaking_changed)
        return old

    def speak(self, text: str) -> None:
        self._voice.speak(text)

    def enqueue(self, text: str) -> None:
        self._voice.enqueue(text)

    def stop(self) -> None:
        self._voice.stop()

    def close(self) -> None:
        self._voice.close()

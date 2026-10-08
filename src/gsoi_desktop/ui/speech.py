"""Spoken output through Qt's text-to-speech (Windows voices, offline)."""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QLocale, QObject, Signal


class Speaker(QObject):
    speaking_changed = Signal(bool)

    def __init__(self, engine: str | None = None) -> None:
        super().__init__()
        self._tts = None
        self._queue: list[str] = []
        self._active = False
        try:
            from PySide6.QtTextToSpeech import QTextToSpeech

            self._tts = QTextToSpeech(engine) if engine else QTextToSpeech()
            self._tts.stateChanged.connect(self._on_state)
            self._tts.setLocale(QLocale(QLocale.Language.Italian, QLocale.Country.Italy))
            italian = [
                v
                for v in self._tts.availableVoices()
                if v.locale().language() == QLocale.Language.Italian
            ]
            if italian:
                self._tts.setVoice(italian[0])
        except Exception:
            self._tts = None  # no speech engine on this machine: stay silent

    def _set_active(self, active: bool) -> None:
        if active is not self._active:
            self._active = active
            self.speaking_changed.emit(active)

    def _on_state(self, state: object) -> None:
        from PySide6.QtTextToSpeech import QTextToSpeech

        if state == QTextToSpeech.State.Speaking:
            self._set_active(True)
        elif state in (QTextToSpeech.State.Ready, QTextToSpeech.State.Error):
            self._next()

    def _next(self) -> None:
        """The current sentence ended: say the next queued one, or report silence."""
        if self._tts is None:
            return
        if self._queue:
            self._tts.say(self._queue.pop(0))
        else:
            self._set_active(False)

    @property
    def active(self) -> bool:
        """Speaking, or sentences are still queued."""
        return self._active

    @property
    def available(self) -> bool:
        return self._tts is not None

    def speak(self, text: str) -> None:
        """Say a text now, dropping whatever was queued."""
        self.stop()
        self.enqueue(text)

    def enqueue(self, text: str) -> None:
        """Say a text after what is already being said (sentence by sentence)."""
        if self._tts is None or not text.strip():
            return
        if self._active:
            self._queue.append(text)
        else:
            self._set_active(True)
            self._tts.say(text)

    def stop(self) -> None:
        self._queue.clear()
        if self._tts is not None:
            self._tts.stop()
        self._set_active(False)

    def close(self) -> None:
        """Stop speaking and release the engine (call before the app quits)."""
        self._queue.clear()
        if self._tts is not None:
            self._tts.stop()
            with contextlib.suppress(RuntimeError, TypeError):
                self._tts.stateChanged.disconnect(self._on_state)
            self._tts.deleteLater()
            self._tts = None

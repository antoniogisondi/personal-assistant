"""Spoken output through Qt's text-to-speech (Windows voices, offline)."""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QLocale, QObject, Signal


class Speaker(QObject):
    speaking_changed = Signal(bool)

    def __init__(self, engine: str | None = None) -> None:
        super().__init__()
        self._tts = None
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

    def _on_state(self, state: object) -> None:
        from PySide6.QtTextToSpeech import QTextToSpeech

        self.speaking_changed.emit(state == QTextToSpeech.State.Speaking)

    @property
    def available(self) -> bool:
        return self._tts is not None

    def speak(self, text: str) -> None:
        if self._tts is not None and text.strip():
            self._tts.say(text)

    def stop(self) -> None:
        if self._tts is not None:
            self._tts.stop()

    def close(self) -> None:
        """Stop speaking and release the engine (call before the app quits)."""
        if self._tts is not None:
            self._tts.stop()
            with contextlib.suppress(RuntimeError, TypeError):
                self._tts.stateChanged.disconnect(self._on_state)
            self._tts.deleteLater()
            self._tts = None

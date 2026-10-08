"""Delivers voice events (produced on background threads) to the UI thread as Qt signals."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from gsoi_desktop.voice.pipeline import VoiceState


class VoiceBridge(QObject):
    state_changed = Signal(object)  # VoiceState
    level_changed = Signal(float)
    woke = Signal()
    heard = Signal(str)
    failed = Signal(str)
    progress = Signal(str, float)

    # VoiceEvents protocol (called from worker threads; queued to the receiver's thread)
    def state(self, state: VoiceState) -> None:
        self.state_changed.emit(state)

    def level(self, level: float) -> None:
        self.level_changed.emit(level)

    def wake(self) -> None:
        self.woke.emit()

    def command(self, text: str) -> None:
        self.heard.emit(text)

    def error(self, message: str) -> None:
        self.failed.emit(message)

    def report_progress(self, what: str, fraction: float) -> None:
        self.progress.emit(what, fraction)

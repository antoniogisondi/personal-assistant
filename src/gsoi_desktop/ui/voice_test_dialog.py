from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gsoi_desktop.ui.async_call import AsyncRunner
from gsoi_desktop.voice.diagnostics import (
    Advice,
    VoiceTester,
    VoiceTestReport,
    advise,
    format_report,
)
from gsoi_desktop.voice.wake import WakeDetector

TEST_SECONDS = 15.0


def _threshold_label(threshold: float) -> str:
    return f"«Hey Jarvis» (soglia {round(threshold * 100)}%)"


class _Live(QObject):
    update = Signal(float, float, float, float)


class VoiceTestDialog(QDialog):
    """Say "Hey Jarvis" and watch what the microphone and the detector perceive."""

    def __init__(
        self,
        runner: AsyncRunner,
        make_tester: Callable[[Callable[[float, float, float, float], None]], VoiceTester],
        threshold: float,
        parent: QWidget | None = None,
        seconds: float = TEST_SECONDS,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Prova del microfono e di «Hey Jarvis»")
        self.setMinimumWidth(460)
        self._runner = runner
        self._make_tester = make_tester
        self._seconds = seconds
        self.threshold = threshold
        self.advice: Advice | None = None
        self.report: VoiceTestReport | None = None
        self.chosen_threshold: float | None = None

        self._live = _Live()
        self._live.update.connect(self._on_update)

        self.intro = QLabel(
            f"Premi «Avvia» e per {seconds:.0f} secondi di' più volte, ben chiaro, «Hey Jarvis»."
        )
        self.intro.setWordWrap(True)
        self.mic_bar = QProgressBar()
        self.mic_bar.setRange(0, 100)
        self.mic_bar.setFormat("Microfono")
        self.score_bar = QProgressBar()
        self.score_bar.setRange(0, 100)
        self.score_bar.setFormat(_threshold_label(threshold))
        self.outcome = QLabel("")
        self.outcome.setWordWrap(True)
        self.outcome.setTextFormat(Qt.TextFormat.PlainText)
        self.start_button = QPushButton("Avvia")
        self.start_button.clicked.connect(self._start)
        self.apply_button = QPushButton("Imposta la sensibilità consigliata")
        self.apply_button.setVisible(False)
        self.apply_button.clicked.connect(self._apply)
        self.close_button = QPushButton("Chiudi")
        self.close_button.clicked.connect(self.accept)

        row = QHBoxLayout()
        for b in (self.start_button, self.apply_button):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        for w in (self.intro, self.mic_bar, self.score_bar, self.outcome):
            layout.addWidget(w)
        layout.addLayout(row)

    def _start(self) -> None:
        self.start_button.setEnabled(False)
        self.apply_button.setVisible(False)
        self.outcome.setText("In ascolto...")
        tester = self._make_tester(self._live.update.emit)
        self._runner.run(lambda: tester.run(self._seconds), self._finished, self._failed)

    def _on_update(self, level: float, score: float, max_level: float, max_score: float) -> None:
        self.mic_bar.setValue(int(level * 100))
        self.score_bar.setValue(int(score * 100))
        hit = score >= self.threshold
        self.score_bar.setFormat(
            "«Hey Jarvis» riconosciuto!" if hit else _threshold_label(self.threshold)
        )

    def _finished(self, report: object) -> None:
        if not isinstance(report, VoiceTestReport):  # pragma: no cover
            return
        self.report = report
        self.advice = advise(report)
        self.outcome.setText(format_report(report, self.advice))
        self.start_button.setEnabled(True)
        self.start_button.setText("Riprova")
        self.apply_button.setVisible(self.advice.suggested_threshold is not None)

    def _failed(self, error: Exception) -> None:
        self.start_button.setEnabled(True)
        self.outcome.setText(f"Prova non riuscita: {error}")

    def _apply(self) -> None:
        if self.advice and self.advice.suggested_threshold is not None:
            self.chosen_threshold = self.advice.suggested_threshold
            self.threshold = self.chosen_threshold
            self.apply_button.setVisible(False)
            self.outcome.setText(
                self.outcome.text() + f"\n\nSensibilità impostata (soglia {self.threshold:.2f}). "
                "Chiudi e riprova a dire «Hey Jarvis»."
            )


def make_default_tester_factory(
    microphone: str | None, detector: WakeDetector, threshold: float
) -> Callable[[Callable[[float, float, float, float], None]], VoiceTester]:
    from gsoi_desktop.voice.audio import SoundDeviceSource

    def make(on_update: Callable[[float, float, float, float], None]) -> VoiceTester:
        return VoiceTester(SoundDeviceSource(microphone), detector, threshold, on_update)

    return make

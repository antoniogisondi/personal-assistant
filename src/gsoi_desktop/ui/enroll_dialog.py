from __future__ import annotations

import threading
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
from gsoi_desktop.voice.enrollment import usable_anyway, verdict
from gsoi_desktop.voice.personal_wake import EnrollmentResult

# The job runs the whole wizard off the UI thread and returns the training result.
Job = Callable[
    [
        Callable[[str, str, int, int], None],
        Callable[[float], None],
        Callable[[str, float], None],
        threading.Event,
    ],
    EnrollmentResult,
]


class _Live(QObject):
    step = Signal(str, str, int, int)
    level = Signal(float)
    progress = Signal(str, float)


class EnrollDialog(QDialog):
    """Teach «Hey Jarvis» to the user's own voice: say it a few times, talk, stay quiet."""

    def __init__(self, runner: AsyncRunner, job: Job, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insegna «Hey Jarvis» alla tua voce")
        self.setMinimumWidth(480)
        self._runner = runner
        self._job = job
        self._stop = threading.Event()
        self.trained: EnrollmentResult | None = None
        self.accepted_model = False

        self._live = _Live()
        self._live.step.connect(self._on_step)
        self._live.level.connect(self._on_level)
        self._live.progress.connect(self._on_progress)

        self.intro = QLabel(
            "Registrerò la tua voce mentre dici «Hey Jarvis» 12 volte, poi mentre parli e mentre "
            "sei in silenzio (circa un minuto). Resta alla distanza a cui userai l'assistente. "
            "Le registrazioni restano sul PC e non vengono salvate: si conserva solo il modello."
        )
        self.intro.setWordWrap(True)
        self.instruction = QLabel("")
        self.instruction.setWordWrap(True)
        self.instruction.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.counter = QLabel("")
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setFormat("Microfono")
        self.outcome = QLabel("")
        self.outcome.setWordWrap(True)
        self.outcome.setTextFormat(Qt.TextFormat.PlainText)
        self.start_button = QPushButton("Inizia")
        self.start_button.clicked.connect(self._start)
        self.use_button = QPushButton("Usa questo rilevatore")
        self.use_button.setVisible(False)
        self.use_button.clicked.connect(self._use)
        self.close_button = QPushButton("Annulla")
        self.close_button.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(self.start_button)
        row.addWidget(self.use_button)
        row.addStretch(1)
        row.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        for w in (self.intro, self.instruction, self.counter, self.level_bar, self.outcome):
            layout.addWidget(w)
        layout.addLayout(row)

    def _start(self) -> None:
        self.start_button.setEnabled(False)
        self.use_button.setVisible(False)
        self.outcome.setText("")
        self._stop.clear()
        job = self._job
        live = self._live
        stop = self._stop
        self._runner.run(
            lambda: job(live.step.emit, live.level.emit, live.progress.emit, stop),
            self._finished,
            self._failed,
        )

    def _on_step(self, kind: str, text: str, index: int, total: int) -> None:
        self.instruction.setText(text)
        self.counter.setText(f"{index} di {total}" if kind == "phrase" else "")

    def _on_level(self, level: float) -> None:
        self.level_bar.setValue(int(level * 100))

    def _on_progress(self, what: str, fraction: float) -> None:
        self.instruction.setText(what)
        self.counter.setText(f"{int(fraction * 100)}%")
        self.level_bar.setValue(0)

    def _finished(self, result: object) -> None:
        if not isinstance(result, EnrollmentResult):  # pragma: no cover
            return
        self.trained = result
        good, text = verdict(result.recall, result.false_alarms, result.stock_recall)
        self.instruction.setText("Fatto!" if good else "Risultato insufficiente")
        self.counter.setText("")
        self.outcome.setText(text)
        anyway = not good and usable_anyway(result.recall, result.false_alarms, result.stock_recall)
        self.use_button.setText(
            "Usa questo rilevatore" if good else "Usa comunque (meglio di prima)"
        )
        self.use_button.setVisible(good or anyway)
        if anyway:
            self.instruction.setText("Migliore di prima, ma non perfetto")
        self.start_button.setEnabled(True)
        self.start_button.setText("Rifai")

    def _failed(self, error: Exception) -> None:
        from gsoi_desktop.voice.enrollment import EnrollmentAborted

        if isinstance(error, EnrollmentAborted):
            return
        self.instruction.setText("")
        self.outcome.setText(str(error))
        self.start_button.setEnabled(True)
        self.start_button.setText("Riprova")

    def _use(self) -> None:
        self.accepted_model = True
        self.accept()

    def done(self, result: int) -> None:
        self._stop.set()
        super().done(result)

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from gsoi_desktop.client import ApiError
from gsoi_desktop.controller import Approval, Turn
from gsoi_desktop.ui.approval_dialog import ApprovalDialog
from gsoi_desktop.ui.async_call import AsyncRunner
from gsoi_desktop.ui.neural_view import STATE_LABEL, NeuralState, NeuralView


class Chat(Protocol):
    def send(self, text: str, channel: str = ...) -> Turn: ...
    def send_streaming(
        self, text: str, channel: str, on_sentence: Callable[[str], None]
    ) -> Turn: ...
    def briefing(self) -> Turn: ...
    def decide(self, approval: Approval, approve: bool) -> Turn: ...
    def new_conversation(self) -> None: ...


class SpeakerLike(Protocol):
    @property
    def active(self) -> bool: ...
    def speak(self, text: str) -> None: ...
    def enqueue(self, text: str) -> None: ...
    def stop(self) -> None: ...


def _bubble(role: str, text: str) -> str:
    body = html.escape(text).replace(
        "\n", "<br>"
    )  # model and email text are never rendered as HTML
    if role == "user":
        return (
            "<table width='100%'><tr><td width='20%'></td><td align='right'>"
            "<div style='background:#2457d6;color:#fff;padding:8px 12px;border-radius:12px'>"
            f"{body}</div></td></tr></table>"
        )
    if role == "error":
        return f"<p style='color:#b3261e'>{body}</p>"
    if role == "note":
        return f"<p style='color:#6b7280'><i>{body}</i></p>"
    return (
        "<table width='100%'><tr><td align='left'>"
        "<div style='background:#eef1f6;padding:8px 12px;border-radius:12px'>"
        f"{body}</div></td><td width='20%'></td></tr></table>"
    )


STYLE = """
QWidget#root { background: #0b1020; }
QLabel { color: #e6ecff; }
QLabel#caption { font-size: 17px; }
QLabel#state { color: #8a96b8; font-size: 12px; letter-spacing: 1px; }
QLabel#banner { background: #3a2f12; color: #ffe9a8; padding: 8px; border-radius: 8px; }
QPushButton {
  background: #172042; color: #dfe6ff; border: 1px solid #27325f;
  border-radius: 8px; padding: 6px 12px;
}
QPushButton:hover { background: #1f2a55; }
QPushButton:disabled { color: #5d6788; }
QPushButton:checked { background: #2457d6; border-color: #2457d6; }
QLineEdit {
  background: #121a38; color: #e6ecff; border: 1px solid #27325f;
  border-radius: 8px; padding: 7px 10px;
}
QTextBrowser { background: #0f1630; color: #e6ecff; border: 1px solid #27325f; border-radius: 8px; }
"""
CAPTION_LIMIT = 260


def _caption_text(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= CAPTION_LIMIT else one[: CAPTION_LIMIT - 1].rstrip() + "…"


class MainWindow(QMainWindow):
    open_settings = Signal()
    open_services = Signal()
    microphone_toggled = Signal()
    _sentence = Signal(str)  # from the worker thread: a sentence of the answer is ready
    voice_turn_finished = Signal()  # a spoken exchange is over: listen for the wake word again

    def __init__(
        self,
        chat: Chat,
        runner: AsyncRunner,
        speaker: SpeakerLike,
        *,
        is_configured: Callable[[], bool],
        read_aloud: Callable[[], bool],
        hide_on_close: bool = True,
    ) -> None:
        super().__init__()
        self._chat = chat
        self._runner = runner
        self._speaker = speaker
        self._is_configured = is_configured
        self._read_aloud = read_aloud
        self.hide_on_close = hide_on_close
        self._busy = False
        self.setWindowTitle("GSOI")
        self.resize(560, 700)

        self.neural = NeuralView()
        self.caption = QLabel("")
        self.caption.setObjectName("caption")
        self.caption.setWordWrap(True)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setMinimumHeight(120)  # room for ~5 lines so long answers are not clipped
        self.caption.setTextFormat(Qt.TextFormat.PlainText)  # answers are never rendered as HTML
        self.state_label = QLabel("")
        self.state_label.setObjectName("state")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner = QLabel("")
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.transcript = QTextBrowser()
        self.transcript.setOpenLinks(False)  # links inside answers must never be followed silently
        self.transcript.setOpenExternalLinks(False)
        self.transcript.setVisible(False)
        self.transcript.setMinimumHeight(180)
        self.status = QLabel("")
        self.status.setObjectName("state")
        self.input = QLineEdit()
        self.input.setPlaceholderText("Parla, oppure scrivi qui...")
        self.input.returnPressed.connect(self.submit)
        self.send_button = QPushButton("Invia")
        self.send_button.clicked.connect(self.submit)
        self.mic_button = QPushButton("🎤")
        self.mic_button.setToolTip("Parla")
        self.mic_button.setVisible(False)  # shown only when voice input is available
        self.mic_button.clicked.connect(self.microphone_toggled.emit)
        self.briefing_button = QPushButton("Riepilogo di oggi")
        self.briefing_button.clicked.connect(self.request_briefing)
        self.services_button = QPushButton("Servizi")
        self.services_button.clicked.connect(self.open_services.emit)
        self.settings_button = QPushButton("Impostazioni")
        self.settings_button.clicked.connect(self.open_settings.emit)
        self.history_button = QPushButton("Cronologia")
        self.history_button.setCheckable(True)
        self.history_button.toggled.connect(self.transcript.setVisible)
        self.new_button = QPushButton("Nuova chat")
        self.new_button.clicked.connect(self.new_chat)

        top = QHBoxLayout()
        for b in (self.briefing_button, self.services_button, self.settings_button):
            top.addWidget(b)
        top.addStretch(1)
        top.addWidget(self.history_button)
        top.addWidget(self.new_button)
        bottom = QHBoxLayout()
        bottom.addWidget(self.mic_button)
        bottom.addWidget(self.input, 1)
        bottom.addWidget(self.send_button)
        root = QWidget()
        root.setObjectName("root")
        root.setStyleSheet(STYLE)
        layout = QVBoxLayout(root)
        layout.addLayout(top)
        layout.addWidget(self.banner)
        layout.addWidget(self.neural, 3)
        layout.addWidget(self.state_label)
        layout.addWidget(self.caption)
        layout.addWidget(self.transcript, 2)
        layout.addWidget(self.status)
        layout.addLayout(bottom)
        self.setCentralWidget(root)
        self._speaking = False
        self._listening = False
        self._voice_turn = False
        self._awaiting_speech_end = False
        self._streamed = False
        self._sentence.connect(self._on_sentence)
        self._speech_timer = QTimer(self)
        self._speech_timer.setSingleShot(True)
        self._speech_timer.timeout.connect(self._finish_voice_turn)
        self.neural.clicked.connect(self.interrupt_speech)
        speaking_signal = getattr(speaker, "speaking_changed", None)
        if speaking_signal is not None:
            speaking_signal.connect(self.set_speaking)
        self.refresh_banner()

    # ---- the neural face ---------------------------------------------------------------

    def _refresh_visual(self) -> None:
        if not self._is_configured():
            state = NeuralState.OFF
        elif self._listening:
            state = NeuralState.LISTENING
        elif self._busy:
            state = NeuralState.THINKING
        elif self._speaking:
            state = NeuralState.SPEAKING
        else:
            state = NeuralState.IDLE
        if self.neural.state is not NeuralState.ERROR or state is not NeuralState.IDLE:
            self.neural.set_state(state)
        self.state_label.setText(STATE_LABEL[self.neural.state].upper())

    def set_speaking(self, speaking: bool) -> None:
        was = self._speaking
        self._speaking = speaking
        self._refresh_visual()
        if was and not speaking and self._awaiting_speech_end:
            self._finish_voice_turn()

    @staticmethod
    def _speech_safety_ms(text: str) -> int:
        """Upper bound for speaking a text (a safety net if the voice never reports back)."""
        return int((2.0 + len(text) / 10.0) * 1000)

    def interrupt_speech(self) -> None:
        """Click on the neural face: stop talking now."""
        self._speaker.stop()
        if self._awaiting_speech_end:
            self._finish_voice_turn()

    def _finish_voice_turn(self) -> None:
        self._speech_timer.stop()
        was_voice = self._voice_turn or self._awaiting_speech_end
        self._voice_turn = False
        self._awaiting_speech_end = False
        if was_voice:
            self.voice_turn_finished.emit()

    def set_voice_level(self, level: float) -> None:
        """Microphone level while waiting/listening (0..1)."""
        if not self._speaking:
            self.neural.set_level(level)

    def show_listening(self, level: float = 0.0) -> None:
        """Called by the voice input: the microphone is open (level 0..1 drives the animation)."""
        self._listening = True
        self.neural.set_level(level)
        self._refresh_visual()

    def show_idle(self) -> None:
        self._listening = False
        self._refresh_visual()

    def set_voice_available(self, available: bool) -> None:
        self.mic_button.setVisible(available)

    # ---- state ------------------------------------------------------------------------

    def refresh_banner(self) -> None:
        configured = self._is_configured()
        self.banner.setVisible(not configured)
        self.banner.setText(
            "Per iniziare, scegli il modello e inserisci la chiave in Impostazioni."
        )
        self._update_controls()
        self._refresh_visual()

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.status.setText(message)
        self._update_controls()
        self._refresh_visual()

    def _update_controls(self) -> None:
        enabled = self._is_configured() and not self._busy
        for w in (self.input, self.send_button, self.briefing_button):
            w.setEnabled(enabled)

    def add(self, role: str, text: str) -> None:
        if role in ("assistant", "error"):
            self.caption.setText(_caption_text(text))
        if role == "error":
            self.neural.set_state(NeuralState.ERROR)
            self.state_label.setText(STATE_LABEL[NeuralState.ERROR].upper())
        self.transcript.append(_bubble(role, text))
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---- actions ----------------------------------------------------------------------

    def submit(self) -> None:
        text = self.input.text().strip()
        if not text or self._busy or not self._is_configured():
            return
        self.input.clear()
        self.add("user", text)
        self._set_busy(True, "Sto pensando...")
        self._runner.run(lambda: self._chat.send(text), self._on_turn, self._on_error)

    def submit_voice(self, text: str) -> None:
        """A command recognised from the microphone: handled like typed text, but answered aloud."""
        if self._busy or not self._is_configured():
            self.voice_turn_finished.emit()
            return
        self._voice_turn = True
        self.add("user", text)
        self.caption.setText(_caption_text(text))
        self._set_busy(True, "Sto pensando...")
        self._streamed = False
        self._runner.run(
            lambda: self._chat.send_streaming(text, "voice", self._sentence.emit),
            lambda t: self._on_turn(t, speak=True),
            self._on_error,
        )

    def _on_sentence(self, text: str) -> None:
        """A sentence of the answer is ready: start saying it while the rest is still coming."""
        if self._voice_turn:
            self._streamed = True
            self.caption.setText(_caption_text(text))
            self._speaker.enqueue(text)

    def announce(self, text: str) -> bool:
        """Say something unprompted (a new-mail alert). Not while a conversation is going on."""
        if self._busy or self._voice_turn or self._awaiting_speech_end or self._speaking:
            return False
        self._voice_turn = True
        self._speaker.enqueue(text)
        self._awaiting_speech_end = bool(self._speaker.active)
        if self._awaiting_speech_end:
            self._speech_timer.start(self._speech_safety_ms(text))
        else:
            self._finish_voice_turn()
        return True

    def request_briefing(self) -> None:
        if self._busy or not self._is_configured():
            return
        self.add("note", "Preparo il riepilogo della giornata...")
        self._set_busy(True, "Raccolgo email, calendario e attività...")
        self._runner.run(
            self._chat.briefing, lambda t: self._on_turn(t, speak=True), self._on_error
        )

    def new_chat(self) -> None:
        self._chat.new_conversation()
        self._speaker.stop()
        self.transcript.clear()
        self.caption.setText("")

    def _on_turn(self, turn: object, speak: bool = False) -> None:
        if not isinstance(turn, Turn):  # pragma: no cover
            return
        if turn.approval is not None:
            self._ask_approval(turn.approval, speak)
            return
        self._set_busy(False)
        self.add("assistant", turn.text)
        spoken = speak and (self._voice_turn or self._read_aloud())
        streamed = self._streamed and self._voice_turn
        self._streamed = False
        if spoken and not streamed:
            self._speaker.speak(turn.text)
        if self._voice_turn:
            # Listen again only after the voice has finished (otherwise it would hear itself).
            self._awaiting_speech_end = spoken and (not streamed or self._speaker.active)
            if self._awaiting_speech_end:
                self._speech_timer.start(self._speech_safety_ms(turn.text))
            else:
                self._finish_voice_turn()
        self.input.setFocus()

    def _ask_approval(self, approval: Approval, speak: bool) -> None:
        self._streamed = False
        self._set_busy(False)
        self.add("note", f"Richiesta di permesso: {approval.summary}")
        dialog = ApprovalDialog(approval, self)
        approved = dialog.exec() == ApprovalDialog.DialogCode.Accepted
        self.add("note", "Approvato." if approved else "Rifiutato.")
        self._set_busy(True, "Procedo..." if approved else "Annullo...")
        self._runner.run(
            lambda: self._chat.decide(approval, approved),
            lambda t: self._on_turn(t, speak),
            self._on_error,
        )

    def _on_error(self, error: Exception) -> None:
        self._set_busy(False)
        self._finish_voice_turn()
        message = (
            error.message if isinstance(error, ApiError) else f"Qualcosa è andato storto: {error}"
        )
        self.add("error", message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.hide_on_close:
            event.ignore()  # keep running in the tray; "Esci" in the tray menu really quits
            self.hide()
            return
        super().closeEvent(event)

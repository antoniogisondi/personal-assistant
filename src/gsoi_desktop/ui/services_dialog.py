from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gsoi_desktop.controller import AssistantController, GoogleState
from gsoi_desktop.ui.async_call import AsyncRunner
from gsoi_desktop.ui.mail_panel import MailPanel

POLL_MS = 2000
POLL_LIMIT = 150  # about five minutes


class ServicesDialog(QDialog):
    """Connect external services. Everything an end user does here is one button."""

    changed = Signal()

    def __init__(
        self,
        controller: AssistantController,
        runner: AsyncRunner,
        parent: QWidget | None = None,
        open_url: Callable[[str], object] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Servizi collegati")
        self.setMinimumWidth(440)
        self._controller = controller
        self._runner = runner
        self._open_url = open_url or (lambda url: QDesktopServices.openUrl(QUrl(url)))
        self._polls = 0
        self.state: GoogleState | None = None

        self.title = QLabel("<b>Google</b> (Gmail e Calendar)")
        self.status = QLabel("Controllo...")
        self.status.setWordWrap(True)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.connect_button = QPushButton("Collega Google")
        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button = QPushButton("Scollega")
        self.disconnect_button.clicked.connect(self._disconnect)

        row = QHBoxLayout()
        row.addWidget(self.connect_button)
        row.addWidget(self.disconnect_button)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        for w in (self.title, self.status):
            layout.addWidget(w)
        layout.addLayout(row)
        layout.addWidget(self.message)
        self.mail = MailPanel(controller, runner, self)
        layout.addSpacing(10)
        layout.addWidget(self.mail)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self.refresh()

    # ---- state ------------------------------------------------------------------------

    def refresh(self) -> None:
        self._runner.run(self._controller.google_state, self._show_state, self._show_error)

    def _show_state(self, state: object) -> None:
        if not isinstance(state, GoogleState):  # pragma: no cover
            return
        self.state = state
        if not state.available:
            text = (
                "Google non è ancora attivato in questa versione di GSOI. "
                "Chiedi a chi ti ha fornito l'assistente di completare la configurazione."
            )
        elif state.connected:
            text = "Collegato. L'assistente può leggere email e calendario e preparare bozze; "
            text += "per inviare o creare eventi chiede sempre il tuo consenso."
        elif state.needs_reconnect:
            text = "Il collegamento è scaduto: premi il pulsante per ricollegare."
        else:
            text = "Non collegato. Premi il pulsante: si apre Google e scegli cosa consentire."
        self.status.setText(text)
        self.connect_button.setVisible(state.available and not state.connected)
        self.disconnect_button.setVisible(state.connected)
        if state.connected and self._timer.isActive():
            self._timer.stop()
            self.message.setText("")
            self.changed.emit()

    def _show_error(self, error: Exception) -> None:
        self.message.setText(f"Errore: {error}")

    # ---- actions ----------------------------------------------------------------------

    def _connect(self) -> None:
        self.connect_button.setEnabled(False)
        self._runner.run(
            self._controller.google_connect_url, self._open_google, self._connect_failed
        )

    def _open_google(self, url: object) -> None:
        self._open_url(str(url))
        self.message.setText("Completa l'autorizzazione nella finestra del browser...")
        self._polls = 0
        self._timer.start()

    def _connect_failed(self, error: Exception) -> None:
        self.connect_button.setEnabled(True)
        self._show_error(error)

    def _poll(self) -> None:
        self._polls += 1
        if self._polls > POLL_LIMIT:
            self._timer.stop()
            self.connect_button.setEnabled(True)
            self.message.setText("Tempo scaduto. Riprova.")
            return
        self.refresh()

    def _disconnect(self) -> None:
        self.disconnect_button.setEnabled(False)

        def done(_: object) -> None:
            self.disconnect_button.setEnabled(True)
            self.connect_button.setEnabled(True)
            self.message.setText("Account scollegato.")
            self.refresh()
            self.changed.emit()

        self._runner.run(self._controller.google_disconnect, done, self._show_error)

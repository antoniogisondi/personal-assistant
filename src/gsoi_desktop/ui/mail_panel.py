from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gsoi_desktop.controller import AssistantController, MailAccountInfo
from gsoi_desktop.ui.async_call import AsyncRunner


class AddMailDialog(QDialog):
    """Address + password; the mail servers are found by themselves for the common providers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Aggiungi una casella email")
        self.setMinimumWidth(420)
        self.address = QLineEdit()
        self.address.setPlaceholderText("nome@tiscali.it")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.advanced = QCheckBox("Il mio provider non è nell'elenco: imposto i server")
        self.imap_host = QLineEdit()
        self.imap_host.setPlaceholderText("imap.provider.it")
        self.smtp_host = QLineEdit()
        self.smtp_host.setPlaceholderText("smtp.provider.it")
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(465)
        self.starttls = QCheckBox("L'invio usa STARTTLS (porta 587)")
        self.starttls.toggled.connect(lambda on: self.smtp_port.setValue(587 if on else 465))
        self.server_widgets = (self.imap_host, self.smtp_host, self.smtp_port, self.starttls)
        for w in self.server_widgets:
            w.setVisible(False)
        self.advanced.toggled.connect(lambda on: [w.setVisible(on) for w in self.server_widgets])
        note = QLabel(
            "La password resta cifrata su questo PC. Se l'account ha la verifica in due passaggi, "
            "crea una «password per app» dalle impostazioni di sicurezza del provider."
        )
        note.setWordWrap(True)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setStyleSheet("color: #d96666;")
        form = QFormLayout()
        form.addRow("Indirizzo", self.address)
        form.addRow("Password", self.password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.advanced)
        for w in self.server_widgets:
            layout.addWidget(w)
        layout.addWidget(note)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.result_value: tuple[str, str, dict[str, Any]] | None = None

    def _save(self) -> None:
        address, password = self.address.text().strip(), self.password.text()
        if "@" not in address or not password:
            self.error.setText("Inserisci indirizzo e password.")
            return
        servers: dict[str, Any] = {}
        if self.advanced.isChecked():
            if not self.imap_host.text().strip() or not self.smtp_host.text().strip():
                self.error.setText("Indica i server in arrivo (IMAP) e in uscita (SMTP).")
                return
            servers = {
                "imap_host": self.imap_host.text().strip(),
                "smtp_host": self.smtp_host.text().strip(),
                "smtp_port": self.smtp_port.value(),
                "smtp_security": "starttls" if self.starttls.isChecked() else "ssl",
            }
        self.result_value = (address, password, servers)
        self.accept()


class MailPanel(QWidget):
    """The other mailboxes (Tiscali, Libero...): list, add, remove."""

    def __init__(
        self, controller: AssistantController, runner: AsyncRunner, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._runner = runner
        self.accounts: list[MailAccountInfo] = []
        title = QLabel("<b>Altre caselle email</b> (Tiscali, Libero, Aruba...)")
        self.list = QListWidget()
        self.list.setMaximumHeight(90)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.add_button = QPushButton("Aggiungi casella...")
        self.add_button.clicked.connect(self._add)
        self.remove_button = QPushButton("Rimuovi")
        self.remove_button.clicked.connect(self._remove)
        row = QHBoxLayout()
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for w in (title, self.list):
            layout.addWidget(w)
        layout.addLayout(row)
        layout.addWidget(self.message)
        self.refresh()

    def refresh(self) -> None:
        self._runner.run(self._controller.mail_accounts, self._show, self._failed)

    def _show(self, accounts: object) -> None:
        if not isinstance(accounts, list):  # pragma: no cover
            return
        self.accounts = accounts
        self.list.clear()
        for a in accounts:
            self.list.addItem(f"{a.label} — {a.address}")
        self.remove_button.setEnabled(bool(accounts))

    def _failed(self, error: Exception) -> None:
        self.message.setText(f"Errore: {error}")
        self.add_button.setEnabled(True)

    def _add(self) -> None:
        dialog = AddMailDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_value is None:
            return
        address, password, servers = dialog.result_value
        self.add_button.setEnabled(False)
        self.message.setText("Provo ad accedere alla casella...")

        def done(_: object) -> None:
            self.add_button.setEnabled(True)
            self.message.setText("Casella aggiunta: ora l'assistente può leggerla e inviare da lì.")
            self.refresh()

        self._runner.run(
            lambda: self._controller.mail_add(address, password, servers), done, self._failed
        )

    def _remove(self) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self.accounts):
            self.message.setText("Seleziona la casella da rimuovere.")
            return
        account = self.accounts[row]

        def done(_: object) -> None:
            self.message.setText(f"Casella {account.address} rimossa.")
            self.refresh()

        self._runner.run(lambda: self._controller.mail_remove(account.id), done, self._failed)

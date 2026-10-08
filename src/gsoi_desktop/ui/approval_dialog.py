from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from gsoi_desktop.controller import Approval

RISK_TEXT = {
    "EXTERNAL": "Ha effetto su altre persone o servizi.",
    "DESTRUCTIVE": "È irreversibile.",
    "WRITE_LOCAL": "Modifica dati sul tuo computer.",
}


class ApprovalDialog(QDialog):
    """Asks the user to allow one specific action, showing exactly what will be done."""

    def __init__(self, approval: Approval, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.approval = approval
        self.setWindowTitle("L'assistente chiede il tuo permesso")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        title = QLabel(approval.summary)
        title.setWordWrap(True)
        title.setTextFormat(
            Qt.TextFormat.PlainText
        )  # never interpret model/third-party text as HTML
        title.setStyleSheet("font-weight: 600; font-size: 15px;")
        layout.addWidget(title)

        risk = QLabel(RISK_TEXT.get(approval.risk, ""))
        risk.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(risk)

        layout.addWidget(QLabel("Dettagli esatti di ciò che verrà fatto:"))
        details = QPlainTextEdit(json.dumps(approval.arguments, indent=2, ensure_ascii=False))
        details.setReadOnly(True)
        details.setMinimumHeight(140)
        layout.addWidget(details)

        self._confirm: QLineEdit | None = None
        if approval.strong:
            layout.addWidget(
                QLabel(f"Azione irreversibile. Per confermare scrivi: {approval.tool}")
            )
            self._confirm = QLineEdit()
            self._confirm.textChanged.connect(self._refresh)
            layout.addWidget(self._confirm)

        self.buttons = QDialogButtonBox()
        self.approve_button = self.buttons.addButton(
            "Approva", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.reject_button = self.buttons.addButton(
            "Rifiuta", QDialogButtonBox.ButtonRole.RejectRole
        )
        self.reject_button.setDefault(True)  # Enter/Escape never approves by accident
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._refresh()

    def _refresh(self) -> None:
        ok = self._confirm is None or self._confirm.text().strip() == self.approval.tool
        self.approve_button.setEnabled(ok)

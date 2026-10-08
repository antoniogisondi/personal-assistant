from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from gsoi_desktop.config import PROVIDERS, DesktopConfig


@dataclass(frozen=True)
class SettingsResult:
    config: DesktopConfig
    new_api_key: str | None  # None: leave the stored key as it is


class SettingsDialog(QDialog):
    def __init__(
        self,
        config: DesktopConfig,
        *,
        has_key: bool,
        autostart_supported: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Impostazioni")
        self.setMinimumWidth(460)
        self._has_key = has_key
        self._config = config
        self.result_value: SettingsResult | None = None

        self.provider = QComboBox()
        for p in PROVIDERS.values():
            self.provider.addItem(p.label, p.key)
        self.provider.setCurrentIndex(max(0, self.provider.findData(config.provider)))
        self.base_url = QLineEdit(config.base_url)
        self.model = QLineEdit(config.model)
        self.model.setPlaceholderText(
            "l'identificativo del modello, dalla documentazione del provider"
        )
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.address = QLineEdit(config.user_address)
        self.autostart = QCheckBox("Avvia GSOI con Windows")
        self.autostart.setChecked(config.autostart and autostart_supported)
        self.autostart.setEnabled(autostart_supported)
        self.read_aloud = QCheckBox("Leggi ad alta voce il riepilogo della giornata")
        self.read_aloud.setChecked(config.read_aloud)
        self.tool_calling = QCheckBox("Il modello supporta l'uso di strumenti (consigliato)")
        self.tool_calling.setChecked(config.tool_calling)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #b3261e;")
        self.error.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Provider", self.provider)
        form.addRow("Indirizzo del servizio", self.base_url)
        form.addRow("Modello", self.model)
        form.addRow("Chiave API", self.api_key)
        form.addRow("Come ti chiamo", self.address)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        for w in (self.autostart, self.read_aloud, self.tool_calling, self.error, buttons):
            layout.addWidget(w)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self._sync_key_field()

    def _provider_changed(self) -> None:
        key = self.provider.currentData()
        self.base_url.setText(PROVIDERS[key].base_url)
        self._sync_key_field()

    def _sync_key_field(self) -> None:
        provider = PROVIDERS[self.provider.currentData()]
        self.api_key.setEnabled(provider.needs_key)
        stored = self._has_key and provider.key == self._config.provider
        self.api_key.setPlaceholderText(
            "già salvata: lascia vuoto per non cambiarla"
            if provider.needs_key and stored
            else (
                "incolla la chiave API" if provider.needs_key else "non serve per un modello locale"
            )
        )

    def _save(self) -> None:
        provider = PROVIDERS[self.provider.currentData()]
        key = self.api_key.text().strip()
        stored = self._has_key and provider.key == self._config.provider
        if not self.model.text().strip():
            self.error.setText("Inserisci l'identificativo del modello.")
            return
        if provider.needs_key and not key and not stored:
            self.error.setText("Inserisci la chiave API del provider.")
            return
        if not self.base_url.text().strip().startswith(("http://", "https://")):
            self.error.setText("L'indirizzo del servizio deve iniziare con http:// o https://")
            return
        cfg = self._config.model_copy(
            update={
                "provider": provider.key,
                "base_url": self.base_url.text().strip(),
                "model": self.model.text().strip(),
                "user_address": self.address.text().strip() or "signore",
                "autostart": self.autostart.isChecked(),
                "read_aloud": self.read_aloud.isChecked(),
                "tool_calling": self.tool_calling.isChecked(),
            }
        )
        self.result_value = SettingsResult(cfg, key or None)
        self.accept()

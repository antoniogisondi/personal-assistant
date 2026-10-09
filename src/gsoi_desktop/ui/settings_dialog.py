from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gsoi_desktop.config import PROVIDERS, DesktopConfig


@dataclass(frozen=True)
class SettingsResult:
    config: DesktopConfig
    new_api_key: str | None  # None: leave the stored key as it is


class SettingsDialog(QDialog):
    voice_test_requested = Signal()
    enroll_requested = Signal()

    def __init__(
        self,
        config: DesktopConfig,
        *,
        has_key: bool,
        autostart_supported: bool,
        microphones: Callable[[], list[tuple[str, str]]] | None = None,
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
        self.voice = QCheckBox("Comando vocale: di' «Hey Jarvis» e poi parla")
        self.voice.setChecked(config.voice_enabled)
        self.voice_model = QComboBox()
        self.voice_model.addItem("Preciso (consigliato, scarica ~480 MB una volta)", "small")
        self.voice_model.addItem(
            "Massimo (turbo, ~1,6 GB: serve una scheda video NVIDIA, altrimenti è lento)", "turbo"
        )
        self.voice_model.addItem("Leggero (più veloce, ~145 MB)", "base")
        self.voice_model.setCurrentIndex(max(0, self.voice_model.findData(config.voice_model)))
        self.mic = QComboBox()
        self.mic.addItem("Microfono predefinito", None)
        for device_id, label in microphones() if microphones else []:
            self.mic.addItem(label, device_id)
        self.mic.setCurrentIndex(max(0, self.mic.findData(config.microphone)))
        self.wake_threshold = QDoubleSpinBox()
        self.wake_threshold.setRange(0.15, 0.95)
        self.wake_threshold.setSingleStep(0.05)
        self.wake_threshold.setDecimals(2)
        self.wake_threshold.setValue(config.wake_threshold)
        self.wake_threshold.setToolTip("Più basso = più sensibile (scatta più facilmente)")
        self.end_pause = QDoubleSpinBox()
        self.end_pause.setRange(0.7, 6.0)
        self.end_pause.setSingleStep(0.5)
        self.end_pause.setDecimals(1)
        self.end_pause.setSuffix(" s")
        self.end_pause.setValue(config.end_pause)
        self.end_pause.setToolTip(
            "Dopo quanti secondi di silenzio un comando lungo viene eseguito. "
            "I comandi brevi («apri Chrome») partono dopo meno di un secondo."
        )
        self.tts = QComboBox()
        self.tts.addItem("Paola (naturale, femminile)", ("piper", "paola"))
        self.tts.addItem("Riccardo (naturale, maschile)", ("piper", "riccardo"))
        self.tts.addItem("Voce di Windows (robotica)", ("windows", "paola"))
        wanted = (config.tts, config.tts_voice)
        self.tts.setCurrentIndex(max(0, self.tts.findData(wanted)))
        self.alerts = QCheckBox("Avvisami di nuove email e appuntamenti in arrivo")
        self.alerts.setChecked(config.alerts)
        self.alerts_speak = QCheckBox("...anche a voce (non durante la fascia silenziosa)")
        self.alerts_speak.setChecked(config.alerts_speak)
        self.alerts_lead = QSpinBox()
        self.alerts_lead.setRange(1, 60)
        self.alerts_lead.setSuffix(" min prima")
        self.alerts_lead.setValue(config.alerts_lead_minutes)
        self.quiet_from = QSpinBox()
        self.quiet_from.setRange(0, 23)
        self.quiet_from.setSuffix(":00")
        self.quiet_from.setValue(config.quiet_from)
        self.quiet_to = QSpinBox()
        self.quiet_to.setRange(0, 23)
        self.quiet_to.setSuffix(":00")
        self.quiet_to.setValue(config.quiet_to)
        self.voice_test_button = QPushButton("Prova il microfono e «Hey Jarvis»...")
        self.voice_test_button.clicked.connect(self.voice_test_requested.emit)
        self.enroll_button = QPushButton("Insegna «Hey Jarvis» alla tua voce...")
        self.enroll_button.clicked.connect(self.enroll_requested.emit)
        self.voice_note = QLabel(
            "Il riconoscimento avviene sul tuo computer: l'audio non esce da qui. "
            "Parla in inglese per «Hey Jarvis»; il comando che segue puoi dirlo in italiano."
        )
        self.voice_note.setWordWrap(True)
        self.voice_note.setStyleSheet("color: #6b7280;")
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
        voice_form = QFormLayout()
        voice_form.addRow("Voce dell'assistente", self.tts)
        voice_form.addRow("Qualità voce", self.voice_model)
        voice_form.addRow("Microfono", self.mic)
        voice_form.addRow("Pausa per concludere un comando lungo", self.end_pause)
        voice_form.addRow("Soglia «Hey Jarvis» (più bassa = più sensibile)", self.wake_threshold)
        for w in (self.autostart, self.read_aloud, self.tool_calling, self.voice):
            layout.addWidget(w)
        layout.addLayout(voice_form)
        alerts_form = QFormLayout()
        alerts_form.addRow("Promemoria appuntamenti", self.alerts_lead)
        alerts_form.addRow("Fascia silenziosa dalle", self.quiet_from)
        alerts_form.addRow("alle", self.quiet_to)
        layout.addWidget(self.alerts)
        layout.addWidget(self.alerts_speak)
        layout.addLayout(alerts_form)
        layout.addWidget(self.voice_test_button)
        layout.addWidget(self.enroll_button)
        layout.addWidget(self.voice_note)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
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
                "voice_enabled": self.voice.isChecked(),
                "voice_model": self.voice_model.currentData(),
                "microphone": self.mic.currentData(),
                "wake_threshold": round(self.wake_threshold.value(), 2),
                "end_pause": round(self.end_pause.value(), 1),
                "alerts": self.alerts.isChecked(),
                "alerts_speak": self.alerts_speak.isChecked(),
                "alerts_lead_minutes": self.alerts_lead.value(),
                "quiet_from": self.quiet_from.value(),
                "quiet_to": self.quiet_to.value(),
                "tts": self.tts.currentData()[0],
                "tts_voice": self.tts.currentData()[1],
            }
        )
        self.result_value = SettingsResult(cfg, key or None)
        self.accept()

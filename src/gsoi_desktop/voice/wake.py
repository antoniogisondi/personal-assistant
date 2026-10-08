"""Wake word ("Hey Jarvis") with openWakeWord: small, local, and cheap enough to run always."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import numpy as np

WAKE_MODEL = "hey_jarvis"


class VoiceUnavailableError(Exception):
    """A voice component (model or library) is missing."""


class WakeDetector(Protocol):
    def predict(self, frame: np.ndarray) -> float: ...
    def reset(self) -> None: ...


def wake_models_present() -> bool:
    try:
        import openwakeword

        base = Path(openwakeword.__file__).parent / "resources" / "models"
    except Exception:
        return False
    names = ("melspectrogram.onnx", "embedding_model.onnx", f"{WAKE_MODEL}_v0.1.onnx")
    return all((base / n).exists() for n in names)


def ensure_wake_models() -> None:
    """Download the (few MB) wake-word models if they are not there yet."""
    if wake_models_present():
        return
    try:
        from openwakeword import utils

        utils.download_models([WAKE_MODEL])
    except Exception as exc:
        raise VoiceUnavailableError(
            "Non riesco a scaricare il modello della parola di attivazione: controlla la rete."
        ) from exc


class OpenWakeWordDetector:
    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold
        try:
            from openwakeword.model import Model

            self._model: Any = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
        except Exception as exc:
            raise VoiceUnavailableError(f"Parola di attivazione non disponibile: {exc}") from exc

    def predict(self, frame: np.ndarray) -> float:
        scores: dict[str, float] = self._model.predict(frame)
        return float(max(scores.values())) if scores else 0.0

    def reset(self) -> None:
        self._model.reset()  # forget buffered audio (e.g. the assistant's own voice)

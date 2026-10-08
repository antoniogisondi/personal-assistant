"""Natural Italian speech with Piper (local neural voices, no cloud, no API key)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from gsoi_desktop.voice.wake import VoiceUnavailableError

BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT"


@dataclass(frozen=True)
class PiperVoiceInfo:
    key: str
    label: str
    path: str  # under BASE_URL, without extension
    size_mb: int


VOICES: dict[str, PiperVoiceInfo] = {
    v.key: v
    for v in (
        PiperVoiceInfo(
            "paola", "Paola (femminile, naturale)", "paola/medium/it_IT-paola-medium", 63
        ),
        PiperVoiceInfo(
            "riccardo",
            "Riccardo (maschile, più leggera)",
            "riccardo/x_low/it_IT-riccardo-x_low",
            25,
        ),
    )
}
DEFAULT_VOICE = "paola"


def voice_dir(base: Path) -> Path:
    return base / "tts"


def voice_files(base: Path, key: str) -> tuple[Path, Path]:
    name = VOICES[key].path.rsplit("/", 1)[-1]
    return voice_dir(base) / f"{name}.onnx", voice_dir(base) / f"{name}.onnx.json"


def piper_installed() -> bool:
    try:
        import piper  # noqa: F401
    except Exception:
        return False
    return True


def voice_present(base: Path, key: str) -> bool:
    model, config = voice_files(base, key)
    return model.is_file() and config.is_file()


def download_voice(
    base: Path, key: str, on_progress: Callable[[float], None] | None = None
) -> None:
    """Fetch a voice once into the app's data folder (atomic: a half download is never used)."""
    if key not in VOICES:
        raise VoiceUnavailableError(f"Voce sconosciuta: {key}")
    if voice_present(base, key):
        return
    voice_dir(base).mkdir(parents=True, exist_ok=True)
    model, config = voice_files(base, key)
    info = VOICES[key]
    try:
        for suffix, target in ((".onnx.json", config), (".onnx", model)):
            tmp = target.with_suffix(target.suffix + ".part")
            with httpx.stream(
                "GET", f"{BASE_URL}/{info.path}{suffix}", follow_redirects=True, timeout=60
            ) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0)) or info.size_mb * 1024 * 1024
                done = 0
                with tmp.open("wb") as out:
                    for chunk in resp.iter_bytes(1 << 16):
                        out.write(chunk)
                        done += len(chunk)
                        if on_progress and suffix == ".onnx":
                            on_progress(min(0.99, done / total))
            tmp.replace(target)
    except (httpx.HTTPError, OSError) as exc:
        for leftover in (model, config):
            leftover.unlink(missing_ok=True)
        raise VoiceUnavailableError(
            "Non riesco a scaricare la voce. Controlla la connessione e riprova."
        ) from exc


class PiperEngine:
    """Turns a sentence into audio (int16 mono). Blocking: call it off the UI thread."""

    def __init__(self, base: Path, key: str = DEFAULT_VOICE) -> None:
        model, config = voice_files(base, key)
        try:
            from piper import PiperVoice

            self._voice: Any = PiperVoice.load(str(model), config_path=str(config))
        except Exception as exc:
            raise VoiceUnavailableError(f"Voce non disponibile: {exc}") from exc
        self.sample_rate = int(self._voice.config.sample_rate)

    def synthesize(self, text: str) -> np.ndarray:
        parts = [
            np.asarray(c.audio_int16_array, dtype=np.int16) for c in self._voice.synthesize(text)
        ]
        return np.concatenate(parts) if parts else np.zeros(0, np.int16)

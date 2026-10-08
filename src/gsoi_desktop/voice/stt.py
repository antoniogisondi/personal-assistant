"""Local speech recognition (faster-whisper) and cleaning of what it returns."""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from gsoi_desktop.voice.audio import SAMPLE_RATE
from gsoi_desktop.voice.wake import VoiceUnavailableError

MODEL_SIZES = {"base": 145, "small": 484}  # approximate download size in MB
PROMPT = (
    "Comandi per l'assistente personale: apri Chrome, apri Spotify, riepilogo della giornata, "
    "email, calendario, appuntamenti, note, attività."
)


class Transcriber(Protocol):
    def transcribe(self, samples: np.ndarray) -> str: ...


# Phrases Whisper tends to invent when it hears silence or noise.
_PHANTOMS = (
    "sottotitoli",
    "amara.org",
    "grazie per la visione",
    "grazie a tutti per la visione",
    "iscriviti al canale",
    "alla prossima",
    "qtss",
    "www.",
)
_WAKE_REMNANT = re.compile(
    r"^\s*(?:(?:ehi|hey|ehy|ok|okay|ciao)[\s,.!?]+)?(?:jarvis|giarvis|garvis|yarvis|jarvi|jarvish)\b[\s,.!?:;-]*",
    re.IGNORECASE,
)


def normalize_gain(
    samples: np.ndarray, target_peak: int = 20_000, max_gain: float = 10.0
) -> np.ndarray:
    """Bring a quiet recording up to a healthy level. Low microphone volume is a common reason
    for poor recognition; very loud audio is left alone."""
    peak = int(np.abs(samples).max()) if samples.size else 0
    if peak <= 0 or peak >= target_peak:
        return samples
    gain = min(max_gain, target_peak / peak)
    boosted: np.ndarray = np.clip(samples.astype(np.float32) * gain, -32768, 32767)
    return boosted.astype(np.int16)


def build_prompt(app_names: list[str]) -> str:
    """The prompt biases Whisper towards the words this user is likely to say."""
    names = [n for n in app_names if 2 <= len(n) <= 24][:25]
    if not names:
        return PROMPT
    return PROMPT + " " + ", ".join(f"apri {n}" for n in names) + "."


def default_threads() -> int:
    return max(4, min(8, (os.cpu_count() or 4) // 2))


def clean_transcript(text: str) -> str:
    """Drop invented phrases and the wake word if it leaked into the recording."""
    text = " ".join(text.split())
    lowered = text.lower()
    if not re.search(r"\w", text) or any(p in lowered for p in _PHANTOMS):
        return ""
    for _ in range(2):
        stripped = _WAKE_REMNANT.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    text = text.strip(" ,.;:-")
    return text if len(text) >= 2 and re.search(r"[A-Za-zÀ-ÿ0-9]{2}", text) else ""


class WhisperTranscriber:
    def __init__(
        self, model_dir: Path, *, language: str = "it", cpu_threads: int | None = None
    ) -> None:
        self._language = language
        self._prompt = PROMPT
        self.last_seconds = 0.0
        self._lock = threading.Lock()
        try:
            from faster_whisper import WhisperModel

            self._model: Any = WhisperModel(
                str(model_dir),
                device="cpu",
                compute_type="int8",
                cpu_threads=cpu_threads or default_threads(),
            )
        except Exception as exc:
            raise VoiceUnavailableError(f"Modello vocale non disponibile: {exc}") from exc

    def set_vocabulary(self, app_names: list[str]) -> None:
        self._prompt = build_prompt(app_names)

    def warm_up(self) -> None:
        """The first recognition is slower (model initialisation): do it before the user speaks."""
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.int16))

    def transcribe(self, samples: np.ndarray) -> str:
        started = time.monotonic()
        audio = normalize_gain(samples).astype(np.float32) / 32768.0
        with self._lock:
            segments, _ = self._model.transcribe(
                audio,
                language=self._language,
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=self._prompt,
                no_speech_threshold=0.6,
            )
            text = " ".join(s.text.strip() for s in segments)
        self.last_seconds = time.monotonic() - started
        return clean_transcript(text)


def model_dir(base: Path, size: str) -> Path:
    return base / f"whisper-{size}"


def stt_model_present(base: Path, size: str) -> bool:
    return (model_dir(base, size) / "model.bin").exists()


def download_stt_model(
    base: Path, size: str, on_progress: Callable[[float], None] | None = None
) -> Path:
    """Download the recognition model once (to the app's data folder). Progress is estimated from
    the folder size, since the downloader does not report it."""
    if size not in MODEL_SIZES:
        raise VoiceUnavailableError(f"Modello vocale sconosciuto: {size}")
    target = model_dir(base, size)
    if stt_model_present(base, size):
        return target
    target.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()

    def watch() -> None:
        expected = MODEL_SIZES[size] * 1024 * 1024
        while not stop.wait(0.7):
            done = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
            if on_progress:
                on_progress(min(0.99, done / expected))

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        from faster_whisper.utils import download_model

        download_model(size, output_dir=str(target))
    except Exception as exc:
        raise VoiceUnavailableError(
            "Non riesco a scaricare il modello vocale. Controlla la connessione e riprova."
        ) from exc
    finally:
        stop.set()
        watcher.join(timeout=2)
    if on_progress:
        on_progress(1.0)
    return target

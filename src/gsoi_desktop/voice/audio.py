"""Audio basics: format, level meter and the microphone source."""

from __future__ import annotations

import queue
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

SAMPLE_RATE = 16_000  # what both the wake-word model and Whisper expect
FRAME = 1_280  # 80 ms: the wake-word model's native step
FRAME_SECONDS = FRAME / SAMPLE_RATE


def rms(frame: np.ndarray) -> float:
    """Root-mean-square level of an int16 frame (0..32767)."""
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(frame.astype(np.float64)))))


def to_unit_level(value: float) -> float:
    """Map an RMS value to a 0..1 level suitable for animation (log-ish, so quiet speech shows)."""
    if value <= 1.0:
        return 0.0
    return float(min(1.0, max(0.0, (np.log10(value) - 1.8) / 2.2)))  # ~60 -> 0, ~10000 -> 1


class AudioSource(Protocol):
    def start(self, on_audio: Callable[[np.ndarray], None]) -> None: ...
    def stop(self) -> None: ...


class MicrophoneError(Exception):
    pass


def list_microphones() -> list[tuple[str, str]]:
    """(device id, label) of the available input devices; empty when audio is not available."""
    try:
        import sounddevice as sd

        devices: Any = sd.query_devices()
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, d in enumerate(devices):
        name = str(d.get("name", "")).strip()
        if d.get("max_input_channels", 0) > 0 and name and name not in seen:
            seen.add(name)
            out.append((str(index), name))
    return out


class SoundDeviceSource:
    """Microphone through PortAudio: 16 kHz, mono, int16 (the device resamples if needed)."""

    def __init__(self, device: str | None = None) -> None:
        self._device = int(device) if device and device.isdigit() else device
        self._stream: Any = None

    def start(self, on_audio: Callable[[np.ndarray], None]) -> None:
        try:
            import sounddevice as sd

            def callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
                on_audio(indata[:, 0].copy())

            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=FRAME,
                device=self._device,
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise MicrophoneError(f"Non riesco ad aprire il microfono: {exc}") from exc

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()


class FrameQueue:
    """Re-chunks arbitrary audio blocks into fixed frames and drops old audio if the consumer lags
    (better to lose a little than to grow without bound or answer late)."""

    def __init__(self, max_frames: int = 400) -> None:
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_frames)
        self._rest = np.zeros(0, dtype=np.int16)
        self.dropped = 0

    def push(self, block: np.ndarray) -> None:
        data = np.concatenate([self._rest, block.astype(np.int16, copy=False)])
        usable = (len(data) // FRAME) * FRAME
        self._rest = data[usable:]
        for i in range(0, usable, FRAME):
            frame = data[i : i + FRAME]
            try:
                self._q.put_nowait(frame)
            except queue.Full:
                try:
                    self._q.get_nowait()  # discard the oldest
                    self.dropped += 1
                except queue.Empty:  # pragma: no cover - consumer drained it meanwhile
                    pass
                self._q.put_nowait(frame)

    def get(self, timeout: float = 0.2) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def clear(self) -> None:
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._rest = np.zeros(0, dtype=np.int16)

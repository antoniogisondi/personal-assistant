"""Brings the voice parts together: prepares the models, opens the microphone, runs the pipeline."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from gsoi_desktop.config import DesktopConfig
from gsoi_desktop.voice.audio import AudioSource, SoundDeviceSource
from gsoi_desktop.voice.pipeline import VoiceEvents, VoicePipeline, VoiceRunner, VoiceState
from gsoi_desktop.voice.stt import (
    MODEL_SIZES,
    Transcriber,
    WhisperTranscriber,
    download_stt_model,
    model_dir,
    stt_model_present,
)
from gsoi_desktop.voice.wake import (
    OpenWakeWordDetector,
    WakeDetector,
    ensure_wake_models,
    wake_models_present,
)

Progress = Callable[[str, float], None]  # (what is happening, 0..1)


class VoiceService:
    def __init__(
        self,
        models_dir: Path,
        events: VoiceEvents,
        *,
        source_factory: Callable[[str | None], AudioSource] = SoundDeviceSource,
        wake_factory: Callable[[float], WakeDetector] = OpenWakeWordDetector,
        stt_factory: Callable[[Path], Transcriber] = WhisperTranscriber,
    ) -> None:
        self._models_dir = models_dir
        self._events = events
        self._source_factory = source_factory
        self._wake_factory = wake_factory
        self._stt_factory = stt_factory
        self._pipeline: VoicePipeline | None = None
        self._runner: VoiceRunner | None = None
        self._stt: Transcriber | None = None
        self._vocabulary: list[str] = []

    def set_vocabulary(self, names: list[str]) -> None:
        """Words the user is likely to say (installed programs), to help recognition."""
        self._vocabulary = names

    @property
    def running(self) -> bool:
        return self._runner is not None

    @property
    def state(self) -> VoiceState:
        return self._pipeline.state if self._pipeline else VoiceState.OFF

    def needs_download(self, config: DesktopConfig) -> bool:
        return not wake_models_present() or not stt_model_present(
            self._models_dir, config.voice_model
        )

    def download_size_mb(self, config: DesktopConfig) -> int:
        return (
            0
            if stt_model_present(self._models_dir, config.voice_model)
            else MODEL_SIZES[config.voice_model]
        )

    def enable(self, config: DesktopConfig, on_progress: Progress | None = None) -> None:
        """Blocking: fetch what is missing, load the models, open the microphone."""
        self.disable()
        report = on_progress or (lambda what, fraction: None)
        report("Preparo la parola di attivazione...", 0.0)
        ensure_wake_models()
        if not stt_model_present(self._models_dir, config.voice_model):
            download_stt_model(
                self._models_dir,
                config.voice_model,
                lambda f: report(
                    f"Scarico il modello vocale ({MODEL_SIZES[config.voice_model]} MB)", f
                ),
            )
        report("Carico il riconoscimento vocale...", 1.0)
        stt = self._stt_factory(model_dir(self._models_dir, config.voice_model))
        self._stt = stt
        if self._vocabulary and hasattr(stt, "set_vocabulary"):
            stt.set_vocabulary(self._vocabulary)
        if hasattr(stt, "warm_up"):
            report("Preparo il riconoscimento vocale...", 1.0)
            stt.warm_up()
        wake = self._wake_factory(config.wake_threshold)
        pipeline = VoicePipeline(
            wake,
            stt,
            self._events,
            wake_threshold=config.wake_threshold,
            end_pause=config.end_pause,
        )
        runner = VoiceRunner(pipeline, self._source_factory(config.microphone))
        runner.start()  # raises if the microphone cannot be opened
        self._pipeline, self._runner = pipeline, runner

    def pause(self) -> None:
        """Release the microphone (e.g. for the voice test) but keep the models loaded."""
        if self._runner is not None:
            self._runner.stop()

    def unpause(self) -> None:
        if self._runner is not None:
            self._runner.start()

    def disable(self) -> None:
        runner, self._runner = self._runner, None
        if runner is not None:
            runner.stop()
        self._pipeline = None
        stt, self._stt = self._stt, None
        if stt is not None and hasattr(stt, "close"):
            stt.close()  # frees the graphics memory the recognition model held

    def resume(self) -> None:
        if self._pipeline is not None:
            self._pipeline.resume()

    def trigger_or_cancel(self) -> None:
        """The microphone button: start listening now, or stop if already listening."""
        if self._pipeline is None:
            return
        if self._pipeline.state is VoiceState.LISTENING:
            self._pipeline.cancel()
        else:
            self._pipeline.trigger()

from __future__ import annotations

import sys
import threading
import time
import types
from collections.abc import Callable
from typing import ClassVar

import numpy as np
import pytest

from gsoi_desktop.voice.audio import (
    FRAME,
    FrameQueue,
    SoundDeviceSource,
    list_microphones,
    rms,
    to_unit_level,
)
from gsoi_desktop.voice.capture import CaptureStatus, CommandCapture
from gsoi_desktop.voice.pipeline import VoicePipeline, VoiceRunner, VoiceState
from gsoi_desktop.voice.stt import clean_transcript

RNG = np.random.default_rng(1234)


def noise(frames: int, amp: float) -> list[np.ndarray]:
    return [(RNG.standard_normal(FRAME) * amp).astype(np.int16) for _ in range(frames)]


def speech(seconds: float, amp: float = 3000) -> list[np.ndarray]:
    n = int(seconds * 16000 / FRAME)
    t = np.arange(FRAME) / 16000
    out = []
    for i in range(n):
        tone = np.sin(2 * np.pi * (180 + 40 * (i % 5)) * t) * amp * 0.7
        out.append((tone + RNG.standard_normal(FRAME) * amp * 0.3).astype(np.int16))
    return out


def quiet(seconds: float, amp: float = 40) -> list[np.ndarray]:
    return noise(int(seconds * 16000 / FRAME), amp)


def run_capture(frames: list[np.ndarray], **kw: float) -> tuple[CaptureStatus, np.ndarray | None]:
    cap = CommandCapture(**kw)  # type: ignore[arg-type]
    for f in frames:
        r = cap.feed(f)
        if r.status in (CaptureStatus.DONE, CaptureStatus.TIMEOUT, CaptureStatus.TOO_SHORT):
            return r.status, r.samples
    return CaptureStatus.RECORDING, None


# ---- audio helpers -----------------------------------------------------------------------


def test_levels() -> None:
    assert rms(np.zeros(FRAME, np.int16)) == 0.0 and rms(np.zeros(0, np.int16)) == 0.0
    assert rms(np.full(FRAME, 1000, np.int16)) == pytest.approx(1000)
    assert to_unit_level(0) == 0 and to_unit_level(40) == 0
    assert 0 < to_unit_level(500) < to_unit_level(3000) < to_unit_level(20000) <= 1.0


def test_frame_queue_rechunks_arbitrary_blocks_and_drops_the_oldest() -> None:
    q = FrameQueue(max_frames=3)
    q.push(np.arange(1000, dtype=np.int16))
    assert q.get(timeout=0.01) is None  # not a full frame yet
    q.push(np.arange(1000, 3000, dtype=np.int16))
    first = q.get(timeout=0.01)
    assert first is not None and len(first) == FRAME and first[0] == 0 and first[-1] == FRAME - 1
    for _ in range(6):  # the consumer is stuck: keep only the newest
        q.push(np.ones(FRAME, np.int16))
    assert q.dropped > 0
    drained = 0
    while q.get(timeout=0.001) is not None:
        drained += 1
    assert drained <= 3
    q.clear()


# ---- command capture ---------------------------------------------------------------------


def test_silence_times_out() -> None:
    status, samples = run_capture(quiet(8), start_timeout=6.0)
    assert status is CaptureStatus.TIMEOUT and samples is None


def test_a_command_is_captured_and_ends_after_a_pause() -> None:
    frames = quiet(1.0) + speech(1.5) + quiet(2.0)
    status, samples = run_capture(frames, floor=40)
    assert status is CaptureStatus.DONE and samples is not None
    seconds = len(samples) / 16000
    assert 1.5 < seconds < 3.2  # speech + a little lead-in + the closing pause


def test_the_wake_word_tail_right_after_the_trigger_is_not_mistaken_for_speech() -> None:
    tail = speech(0.16)  # "…vis" still ringing when the detector fires
    frames = tail + quiet(1.5) + speech(1.2) + quiet(2.0)
    status, samples = run_capture(frames, floor=40)
    assert status is CaptureStatus.DONE and samples is not None
    assert len(samples) / 16000 > 1.2  # the real command was kept, not discarded as a short blip


def test_a_click_is_not_a_command() -> None:
    status, _ = run_capture(quiet(0.8) + speech(0.16) + quiet(2.0), floor=40, min_seconds=0.4)
    assert status is CaptureStatus.TOO_SHORT


def test_endless_speech_is_cut_at_the_maximum() -> None:
    status, samples = run_capture(quiet(0.5) + speech(20), floor=40, max_seconds=5.0)
    assert status is CaptureStatus.DONE and samples is not None and len(samples) / 16000 <= 5.6


def test_a_noisy_room_is_not_mistaken_for_speech_but_speech_still_is_heard() -> None:
    room = 450.0  # fan / street noise, louder than the fixed minimum threshold
    status, _ = run_capture(noise(80, room), floor=room, start_timeout=5.0)
    assert status is CaptureStatus.TIMEOUT
    frames = noise(15, room) + speech(1.5, amp=6000) + noise(20, room)
    status2, samples = run_capture(frames, floor=room)
    assert status2 is CaptureStatus.DONE and samples is not None


def test_capture_exposes_the_level_for_the_animation() -> None:
    cap = CommandCapture()
    cap.feed(speech(0.1, 4000)[0])
    assert cap.level > 1000


# ---- transcript cleaning -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Apri Chrome", "Apri Chrome"),
        ("  apri   spotify  ", "apri spotify"),
        ("Jarvis, apri Chrome", "apri Chrome"),
        ("Ehi Jarvis apri Chrome", "apri Chrome"),
        ("hey giarvis che ore sono?", "che ore sono?"),
        ("Jarvis", ""),
        ("Sottotitoli creati dalla comunità Amara.org", ""),
        ("Grazie per la visione!", ""),
        ("...", ""),
        ("", ""),
        ("a", ""),
        ("Quante email ho oggi?", "Quante email ho oggi?"),
        (
            "Mi dici la tua opinione su Jarvis il film?",
            "Mi dici la tua opinione su Jarvis il film?",
        ),
    ],
)
def test_clean_transcript(raw: str, expected: str) -> None:
    assert clean_transcript(raw) == expected


# ---- the pipeline ------------------------------------------------------------------------


class FakeWake:
    def __init__(self) -> None:
        self.script: list[float] = []
        self.resets = 0
        self.calls = 0
        self.heard: list[int] = []

    def predict(self, frame: np.ndarray) -> float:
        self.calls += 1
        self.heard.append(int(np.abs(frame).max()))
        return self.script.pop(0) if self.script else 0.0

    def reset(self) -> None:
        self.resets += 1


class FakeStt:
    def __init__(self, text: str = "apri Chrome") -> None:
        self.text = text
        self.heard: list[int] = []
        self.fail = False

    def transcribe(self, samples: np.ndarray) -> str:
        self.heard.append(len(samples))
        if self.fail:
            raise RuntimeError("model crashed")
        return self.text


class Recorder:
    def __init__(self) -> None:
        self.states: list[VoiceState] = []
        self.levels: list[float] = []
        self.wakes = 0
        self.commands: list[str] = []
        self.errors: list[str] = []

    def state(self, state: VoiceState) -> None:
        self.states.append(state)

    def level(self, level: float) -> None:
        self.levels.append(level)

    def wake(self) -> None:
        self.wakes += 1

    def command(self, text: str) -> None:
        self.commands.append(text)

    def error(self, message: str) -> None:
        self.errors.append(message)


def make_pipeline(text: str = "apri Chrome") -> tuple[VoicePipeline, FakeWake, FakeStt, Recorder]:
    wake, stt, rec = FakeWake(), FakeStt(text), Recorder()
    p = VoicePipeline(wake, stt, rec, wake_threshold=0.5)
    return p, wake, stt, rec


def feed_all(p: VoicePipeline, frames: list[np.ndarray]) -> None:
    for f in frames:
        p.feed(f)


def test_the_microphone_is_ignored_while_off() -> None:
    p, wake, _, rec = make_pipeline()
    feed_all(p, speech(1))
    assert wake.calls == 0 and rec.states == [] and p.state is VoiceState.OFF


def test_wake_word_then_command_then_busy_then_resume() -> None:
    p, wake, stt, rec = make_pipeline("apri Chrome")
    p.enable()
    wake.script = [0.0, 0.0, 0.93]
    feed_all(p, quiet(0.5))  # three frames feed the detector (the rest score 0)
    assert rec.wakes == 1 and p.state is VoiceState.LISTENING
    feed_all(p, quiet(0.5) + speech(1.2) + quiet(1.5))
    assert rec.commands == ["apri Chrome"] and p.state is VoiceState.BUSY
    assert rec.states == [
        VoiceState.WAITING, VoiceState.LISTENING, VoiceState.TRANSCRIBING, VoiceState.BUSY
    ]  # fmt: skip
    wake.script = [0.99]
    feed_all(p, speech(1))  # while the assistant answers, nothing is heard (not even the wake word)
    assert p.state is VoiceState.BUSY and rec.wakes == 1 and len(stt.heard) == 1
    resets = wake.resets
    p.resume()
    assert p.state is VoiceState.WAITING and wake.resets == resets  # no cold restart


def test_detector_stays_warm_while_busy_or_listening() -> None:
    p, wake, _, _ = make_pipeline()
    p.enable()
    wake.script = [0.9]
    p.feed(quiet(0.1)[0])  # wake -> LISTENING
    calls = wake.calls
    feed_all(p, speech(0.5))
    assert wake.calls > calls  # still fed during the command, score ignored


def test_after_resume_a_short_cooldown_ignores_the_tail_of_the_assistants_voice() -> None:
    p, wake, _, rec = make_pipeline()
    p.enable()
    wake.script = [0.9]
    p.feed(quiet(0.1)[0])
    feed_all(p, speech(1) + quiet(1.5))
    p.resume()
    wake.script = [0.99] * 5
    feed_all(p, quiet(0.25))  # inside the cooldown: no trigger
    assert rec.wakes == 1


def test_silence_after_the_wake_word_goes_back_to_waiting() -> None:
    p, wake, stt, rec = make_pipeline()
    p.enable()
    wake.script = [0.9]
    p.feed(quiet(0.1)[0])
    assert p.state is VoiceState.LISTENING
    feed_all(p, quiet(7))
    assert p.state is VoiceState.WAITING and rec.commands == [] and stt.heard == []


def test_empty_or_phantom_transcripts_are_dropped() -> None:
    p, wake, stt, rec = make_pipeline("Sottotitoli creati dalla comunità Amara.org")
    stt.text = ""
    p.enable()
    wake.script = [0.9]
    p.feed(quiet(0.1)[0])
    feed_all(p, quiet(0.5) + speech(1.0) + quiet(1.5))
    assert rec.commands == [] and p.state is VoiceState.WAITING


def test_recognition_errors_are_reported_and_the_pipeline_recovers() -> None:
    p, wake, stt, rec = make_pipeline()
    stt.fail = True
    p.enable()
    wake.script = [0.9]
    p.feed(quiet(0.1)[0])
    feed_all(p, quiet(0.5) + speech(1.0) + quiet(1.5))
    assert len(rec.errors) == 1 and "capire" in rec.errors[0]
    assert p.state is VoiceState.WAITING and rec.commands == []


def test_the_microphone_button_starts_listening_without_the_wake_word() -> None:
    p, _, _, rec = make_pipeline()
    p.trigger()  # ignored while off
    assert p.state is VoiceState.OFF
    p.enable()
    p.trigger()
    assert p.state is VoiceState.LISTENING and rec.wakes == 0
    p.cancel()
    assert p.state is VoiceState.WAITING


def test_disable_during_listening_stops_everything() -> None:
    p, _, _, rec = make_pipeline()
    p.enable()
    p.trigger()
    p.disable()
    feed_all(p, speech(1))
    assert p.state is VoiceState.OFF and rec.commands == []


def test_disabling_while_transcribing_discards_the_result() -> None:
    wake, rec = FakeWake(), Recorder()
    release = threading.Event()
    entered = threading.Event()

    class SlowStt:
        def transcribe(self, samples: np.ndarray) -> str:
            entered.set()
            release.wait(2)
            return "apri Chrome"

    p = VoicePipeline(wake, SlowStt(), rec)
    p.enable()
    p.trigger()
    t = threading.Thread(target=lambda: feed_all(p, quiet(0.5) + speech(1.0) + quiet(1.5)))
    t.start()
    assert entered.wait(3)
    p.disable()  # the user turned voice off while it was recognising
    release.set()
    t.join(3)
    assert rec.commands == [] and p.state is VoiceState.OFF


def test_levels_are_reported_for_the_animation() -> None:
    p, _, _, rec = make_pipeline()
    p.enable()
    feed_all(p, speech(0.5, 5000))
    assert max(rec.levels) > 0.05
    assert max(rec.levels) <= 0.35  # calm while only waiting for the wake word


# ---- runner and microphone ---------------------------------------------------------------


class FakeSource:
    def __init__(self) -> None:
        self.callback: Callable[[np.ndarray], None] | None = None
        self.stopped = False
        self.fail = False

    def start(self, on_audio: Callable[[np.ndarray], None]) -> None:
        if self.fail:
            raise RuntimeError("no microphone")
        self.callback = on_audio

    def stop(self) -> None:
        self.stopped = True


def test_runner_feeds_the_pipeline_from_the_microphone_thread() -> None:
    p, wake, _, rec = make_pipeline()
    wake.script = [0.0, 0.0, 0.95]
    src = FakeSource()
    runner = VoiceRunner(p, src)
    runner.start()
    assert src.callback is not None and p.state is VoiceState.WAITING
    block = np.concatenate(quiet(0.4))
    for i in range(0, len(block), 700):  # blocks that do not line up with the frame size
        src.callback(block[i : i + 700])
    end = time.monotonic() + 3
    while rec.wakes == 0 and time.monotonic() < end:
        time.sleep(0.01)
    assert rec.wakes == 1
    runner.stop()
    assert src.stopped and p.state is VoiceState.OFF
    runner.stop()  # idempotent


def test_runner_reports_a_missing_microphone() -> None:
    p, *_ = make_pipeline()
    src = FakeSource()
    src.fail = True
    runner = VoiceRunner(p, src)
    with pytest.raises(RuntimeError, match="no microphone"):
        runner.start()
    assert p.state is VoiceState.OFF


def test_sounddevice_source_asks_for_16k_mono_int16(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class FakeStream:
        def __init__(self, **kw: object) -> None:
            seen.update(kw)
            self.cb = kw["callback"]

        def start(self) -> None:
            seen["started"] = True

        def stop(self) -> None:
            seen["stopped"] = True

        def close(self) -> None:
            seen["closed"] = True

    fake = types.SimpleNamespace(
        InputStream=FakeStream,
        query_devices=lambda: [
            {"name": "Speakers", "max_input_channels": 0},
            {"name": "Mic A", "max_input_channels": 1},
            {"name": "Mic A", "max_input_channels": 2},  # duplicate name is listed once
            {"name": "Mic B", "max_input_channels": 2},
        ],
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    got: list[np.ndarray] = []
    src = SoundDeviceSource("3")
    src.start(got.append)
    assert seen["samplerate"] == 16000 and seen["channels"] == 1 and seen["dtype"] == "int16"
    assert seen["blocksize"] == FRAME and seen["device"] == 3
    block = np.arange(FRAME * 2, dtype=np.int16).reshape(FRAME, 2)[:, :1]
    seen["callback"](block, FRAME, None, None)  # type: ignore[operator]
    assert len(got) == 1 and got[0].shape == (FRAME,)
    src.stop()
    assert seen["stopped"] and seen["closed"]
    assert list_microphones() == [("1", "Mic A"), ("3", "Mic B")]


def test_microphone_errors_become_a_readable_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from gsoi_desktop.voice.audio import MicrophoneError

    def boom(**kw: object) -> None:
        raise OSError("Invalid device")

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=boom))
    with pytest.raises(MicrophoneError, match="microfono"):
        SoundDeviceSource().start(lambda a: None)


# ---- recognition helpers -----------------------------------------------------------------


def test_quiet_recordings_are_brought_up_but_loud_ones_are_left_alone() -> None:
    from gsoi_desktop.voice.stt import normalize_gain

    quiet_audio = (np.sin(np.linspace(0, 200, 16000)) * 1500).astype(np.int16)
    boosted = normalize_gain(quiet_audio)
    assert np.abs(boosted).max() > 10000 and boosted.dtype == np.int16
    assert np.abs(boosted).max() <= 32767

    very_quiet = (np.sin(np.linspace(0, 200, 16000)) * 20).astype(np.int16)
    assert (
        np.abs(normalize_gain(very_quiet)).max() <= 20 * 10 + 1
    )  # gain is capped (no noise blow-up)

    loud_audio = (np.sin(np.linspace(0, 200, 16000)) * 25000).astype(np.int16)
    assert normalize_gain(loud_audio) is loud_audio
    silence_audio = np.zeros(1600, np.int16)
    assert normalize_gain(silence_audio) is silence_audio
    assert normalize_gain(np.zeros(0, np.int16)).size == 0


def test_the_recognition_prompt_is_neutral_with_a_few_program_names() -> None:
    from gsoi_desktop.voice.stt import PROMPT, build_prompt

    assert build_prompt([]) == PROMPT and "apri" not in PROMPT
    p = build_prompt(["Google Chrome", "Spotify", "x", "A" * 40])
    assert "Google Chrome" in p and "Spotify" in p and "apri" not in p
    assert "A" * 40 not in p and ", x" not in p  # too short / too long names are skipped
    many = build_prompt([f"Programma {i}" for i in range(100)])
    assert many.count("Programma") <= 8  # the prompt stays short


def test_the_pipeline_reports_how_long_recognition_took() -> None:
    p, wake, _, rec = make_pipeline()
    seen: list[tuple[str, float]] = []
    rec.timing = lambda stage, seconds: seen.append((stage, seconds))  # type: ignore[attr-defined]
    p.enable()
    wake.script = [0.9]
    p.feed(quiet(0.1)[0])
    feed_all(p, quiet(0.5) + speech(1.0) + quiet(1.5))
    assert seen and seen[0][0] == "recognition" and seen[0][1] >= 0


def test_the_command_ends_a_little_sooner_after_a_pause() -> None:
    status, samples = run_capture(quiet(0.5) + speech(1.0) + quiet(2.0), floor=40)
    assert status is CaptureStatus.DONE and samples is not None
    assert len(samples) / 16000 < 1.0 + 0.9  # speech plus roughly the 0.7 s closing pause


def test_a_long_command_survives_a_thinking_pause_but_a_short_one_does_not_wait() -> None:
    # 3 s of speech, 1 s pause, 2 s more speech: still one command
    status, samples = run_capture(
        quiet(0.5) + speech(3.0) + quiet(1.0) + speech(2.0) + quiet(2.0), floor=40
    )
    assert status is CaptureStatus.DONE and samples is not None
    assert len(samples) / 16000 > 5.5
    # 1 s of speech then a 1 s pause: already over (short commands answer quickly)
    status, samples = run_capture(quiet(0.5) + speech(1.0) + quiet(1.0) + speech(2.0), floor=40)
    assert samples is not None and len(samples) / 16000 < 2.5


def test_the_end_pause_setting_controls_how_long_a_long_command_waits() -> None:
    long_cmd = quiet(0.5) + speech(3.0)
    # a 4 s pause then more speech: with a 5 s end pause it is still the same command
    _, samples = run_capture(
        long_cmd + quiet(4.0) + speech(2.0) + quiet(6.0), floor=40, long_endpoint=5.0
    )
    assert samples is not None and len(samples) / 16000 > 8.5
    _, early = run_capture(long_cmd + quiet(4.0) + speech(2.0) + quiet(6.0), floor=40)
    assert early is not None and len(early) / 16000 < 5.5


def test_the_detector_never_hears_the_assistants_own_voice() -> None:
    p, wake, _, rec = make_pipeline()
    p.enable()
    wake.script = [0.9]
    p.feed(quiet(0.1)[0])
    feed_all(p, quiet(0.5) + speech(1.2) + quiet(1.5))  # the command, then BUSY
    assert p.state is VoiceState.BUSY
    before = len(wake.heard)
    feed_all(p, speech(2.0))  # the assistant's voice through the speakers
    assert len(wake.heard) > before and max(wake.heard[before:]) == 0
    p.resume()
    wake.script = [0.99] * 3  # a (false) high score right after resume is ignored by the cooldown
    feed_all(p, quiet(0.2))
    assert rec.wakes == 1


class _FakeWhisper:
    created: ClassVar[list[tuple[str, str]]] = []
    fail_cuda = False

    def __init__(self, path: str, device: str, compute_type: str, cpu_threads: int) -> None:
        self.created.append((device, compute_type))
        self.beam = 0

    def transcribe(self, audio: np.ndarray, **kw: object):  # type: ignore[no-untyped-def]
        if self.created[-1][0] == "cuda" and self.fail_cuda:
            raise RuntimeError("Library cublas64_12.dll is not found")
        self.beam = int(kw["beam_size"])  # type: ignore[call-overload]

        class Seg:
            text = " apri Chrome "

        return [Seg()], None


@pytest.fixture
def fake_whisper(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    import faster_whisper

    _FakeWhisper.created = []
    _FakeWhisper.fail_cuda = False
    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeWhisper)
    from gsoi_desktop.voice import stt

    monkeypatch.setattr(stt, "cuda_available", lambda: True)
    return _FakeWhisper


def test_whisper_uses_the_graphics_card_with_a_wider_search(fake_whisper, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from gsoi_desktop.voice.stt import WhisperTranscriber

    t = WhisperTranscriber(tmp_path)
    assert t.device == "cuda" and fake_whisper.created == [("cuda", "float16")]
    assert t.transcribe(speech(1.0)[0]) == "apri Chrome"


def test_whisper_falls_back_to_the_cpu_when_the_card_does_not_work(fake_whisper, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from gsoi_desktop.voice.stt import WhisperTranscriber

    fake_whisper.fail_cuda = True
    t = WhisperTranscriber(tmp_path)
    assert t.device == "cpu" and fake_whisper.created == [("cuda", "float16"), ("cpu", "int8")]
    assert t.transcribe(speech(1.0)[0]) == "apri Chrome"


def test_whisper_can_be_forced_onto_the_cpu(fake_whisper, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from gsoi_desktop.voice.stt import WhisperTranscriber

    t = WhisperTranscriber(tmp_path, device="cpu")
    assert t.device == "cpu" and fake_whisper.created == [("cpu", "int8")]


def test_switching_voice_off_releases_the_recognition_model(fake_whisper, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from gsoi_desktop.voice.stt import WhisperTranscriber

    t = WhisperTranscriber(tmp_path)
    assert t.transcribe(speech(1.0)[0]) == "apri Chrome"
    t.close()
    assert t._model is None and t.transcribe(speech(1.0)[0]) == ""


def test_the_service_frees_the_model_when_voice_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path

    from gsoi_desktop.config import DesktopConfig
    from gsoi_desktop.voice.service import VoiceService

    closed: list[bool] = []

    class Stt:
        def transcribe(self, samples: np.ndarray) -> str:
            return ""

        def close(self) -> None:
            closed.append(True)

    class Source:
        def start(self, on_audio) -> None:  # type: ignore[no-untyped-def]
            pass

        def stop(self) -> None:
            pass

    service = VoiceService(
        Path("."),
        types.SimpleNamespace(
            state=lambda s: None,
            level=lambda x: None,
            wake=lambda: None,
            command=lambda t: None,
            error=lambda m: None,
        ),  # type: ignore[arg-type]
        source_factory=lambda mic: Source(),
        wake_factory=lambda th: FakeWake(),
        stt_factory=lambda d: Stt(),
    )
    import gsoi_desktop.voice.service as svc

    monkeypatch.setattr(svc, "ensure_wake_models", lambda: None)
    monkeypatch.setattr(svc, "stt_model_present", lambda base, size: True)
    service.enable(DesktopConfig())
    assert closed == []
    service.disable()
    assert closed == [True]

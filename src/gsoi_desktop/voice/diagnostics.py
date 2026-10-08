"""Voice test: shows what the microphone and the wake-word detector actually perceive.

When "Hey Jarvis" does not wake the assistant, the cause is one of: the microphone is too quiet or
the wrong one, the pronunciation scores low, or the threshold is too strict. This measures each.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from gsoi_desktop.voice.audio import AudioSource, FrameQueue, rms, to_unit_level
from gsoi_desktop.voice.wake import WakeDetector

Update = Callable[[float, float, float, float], None]  # (level, score, max level, max score)


@dataclass
class VoiceTestReport:
    frames: int = 0
    max_rms: float = 0.0
    max_score: float = 0.0
    seconds: float = 0.0
    triggers: int = 0  # frames at/over the threshold
    threshold: float = 0.5


@dataclass
class Advice:
    ok: bool
    messages: list[str] = field(default_factory=list)
    suggested_threshold: float | None = None


def advise(report: VoiceTestReport) -> Advice:
    """Turn the measurements into plain-language next steps."""
    if report.frames == 0:
        return Advice(
            False,
            [
                "Dal microfono non arriva nessun audio. Controlla in Impostazioni di Windows > "
                "Privacy e sicurezza > Microfono che l'accesso per le app desktop sia attivo, "
                "e scegli il microfono giusto nelle impostazioni di GSOI."
            ],
        )
    msgs: list[str] = []
    if report.max_rms < 200:
        msgs.append(
            "Il microfono sente pochissimo: alza il volume di input in Impostazioni di Windows > "
            "Sistema > Suono > Input, avvicinati, oppure scegli un altro microfono."
        )
    elif report.max_rms > 28000:
        msgs.append(
            "Il segnale è troppo forte (distorce): abbassa il volume di input del microfono."
        )
    if report.max_score >= report.threshold:
        msgs.append("«Hey Jarvis» è stato riconosciuto: la parola di attivazione funziona.")
        return Advice(True, msgs)
    if report.max_score >= 0.1:
        suggested = round(max(0.15, min(0.5, report.max_score * 0.6)), 2)
        msgs.append(
            f"Ti ha sentito ma con un punteggio basso ({report.max_score:.2f}, la soglia è "
            f"{report.threshold:.2f}). Rendo la sensibilità più alta: soglia {suggested:.2f}."
        )
        return Advice(False, msgs, suggested)
    if report.max_rms >= 200:
        msgs.append(
            f"Il microfono funziona ma «Hey Jarvis» non è stato riconosciuto (punteggio massimo "
            f"{report.max_score:.2f}). Il modello è inglese: prova «hèi giàrvis» scandendo bene, "
            "con una breve pausa dopo. Se non basta, usa il pulsante del microfono o la "
            "scorciatoia da tastiera."
        )
    return Advice(False, msgs)


class VoiceTester:
    def __init__(
        self,
        source: AudioSource,
        detector: WakeDetector,
        threshold: float,
        on_update: Update | None = None,
    ) -> None:
        self._source = source
        self._detector = detector
        self._threshold = threshold
        self._on_update = on_update

    def run(self, seconds: float = 15.0, stop: threading.Event | None = None) -> VoiceTestReport:
        frames = FrameQueue()
        report = VoiceTestReport(threshold=self._threshold)
        self._source.start(frames.push)
        started = time.monotonic()
        try:
            while time.monotonic() - started < seconds and not (stop and stop.is_set()):
                frame = frames.get(timeout=0.2)
                if frame is None:
                    continue
                level = rms(frame)
                score = self._detector.predict(frame)
                report.frames += 1
                report.max_rms = max(report.max_rms, level)
                report.max_score = max(report.max_score, score)
                if score >= self._threshold:
                    report.triggers += 1
                if self._on_update:
                    self._on_update(
                        to_unit_level(level), score, to_unit_level(report.max_rms), report.max_score
                    )
        finally:
            self._source.stop()
            report.seconds = time.monotonic() - started
        return report


def format_report(report: VoiceTestReport, advice: Advice) -> str:
    lines = [
        f"Durata: {report.seconds:.0f} s, frame ricevuti: {report.frames}",
        f"Livello massimo del microfono (RMS): {report.max_rms:.0f}",
        f"Punteggio massimo di «Hey Jarvis»: {report.max_score:.3f} "
        f"(soglia {report.threshold:.2f})",
        "",
        *advice.messages,
    ]
    return "\n".join(lines)

# ruff: noqa: E501  (the phrase table below is easier to read one rule per line)
"""Spoken/typed commands with a clear, fixed meaning run directly, with no language model.

"apri Chrome", "alza il volume", "che ore sono" ... take milliseconds instead of several seconds,
cost nothing and cannot be misunderstood. They still go through the ToolExecutor, so the same
policy, approvals and audit apply. Anything unclear falls through to the normal agent.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from gsoi_assistant.connectors.pc.matching import normalize
from gsoi_assistant.llm.base import ToolCall
from gsoi_assistant.tools.executor import ExecContext, ExecResult, ToolExecutor
from gsoi_assistant.tools.registry import ToolRegistry

_FILLERS = re.compile(r"\b(per favore|per piacere|grazie|adesso|subito|ti prego|jarvis)\b")
_OPEN = re.compile(
    r"^(?:apri|aprimi|apri mi|avvia|avviami|lancia|lanciami|esegui|fai partire)\s+"
    r"(?:(?:l |il |lo |la |i |gli |le |un |una )?(?:app |applicazione |programma )?)(?P<what>.+)$"
)
_FOLDER = re.compile(
    r"^(?:la |il )?(?:cartella|directory)\s+(?:dei |delle |di |del |della )?(?P<name>.+)$"
)
FOLDERS = {
    "documenti": "documents",
    "documento": "documents",
    "download": "downloads",
    "scaricati": "downloads",
    "scaricamenti": "downloads",
    "desktop": "desktop",
    "scrivania": "desktop",
    "immagini": "pictures",
    "foto": "pictures",
    "musica": "music",
    "video": "videos",
}
FOLDER_NAMES = {
    "documents": "Documenti",
    "downloads": "Download",
    "desktop": "Scrivania",
    "pictures": "Immagini",
    "music": "Musica",
    "videos": "Video",
}
MEDIA: list[tuple[re.Pattern[str], str, int, str]] = [
    (re.compile(r"^(?:metti in |mettila in |mettilo in )?pausa(?: la musica| il video| la canzone)?$"), "play_pause", 1, "In pausa."),
    (re.compile(r"^(?:ferma|stoppa|stop)(?: la)?(?: musica| canzone| brano| video)?$"), "play_pause", 1, "In pausa."),
    (re.compile(r"^(?:riprendi|riproduci|continua|fai ripartire|play)(?: la musica| il video| la canzone)?$"), "play_pause", 1, "Riprendo."),
    (re.compile(r"^(?:prossim[ao]|success?iv[ao]|avanti|salta)(?: canzone| brano| traccia)?$"), "next", 1, "Brano successivo."),
    (re.compile(r"^(?:canzone|brano|traccia) (?:prossim[ao]|success?iv[ao])$"), "next", 1, "Brano successivo."),
    (re.compile(r"^(?:precedente|indietro|torna indietro)(?: canzone| brano| traccia)?$"), "previous", 1, "Brano precedente."),
    (re.compile(r"^(?:canzone|brano|traccia) precedente$"), "previous", 1, "Brano precedente."),
    (re.compile(r"^(?:alza|aumenta)(?: il)? (?:volume|audio)(?: un po| tanto| molto)?$"), "volume_up", 5, "Alzo il volume."),
    (re.compile(r"^volume (?:su|alto|piu alto|piu forte)$"), "volume_up", 5, "Alzo il volume."),
    (re.compile(r"^(?:abbassa|diminuisci|riduci)(?: il)? (?:volume|audio)(?: un po| tanto| molto)?$"), "volume_down", 5, "Abbasso il volume."),
    (re.compile(r"^volume (?:giu|basso|piu basso|piu piano)$"), "volume_down", 5, "Abbasso il volume."),
    (re.compile(r"^(?:muto|muta|silenzia|togli l audio|disattiva l audio|attiva muto)(?: il computer| l audio)?$"), "mute", 1, "Audio disattivato."),
]  # fmt: skip
_TIME = re.compile(r"^(?:che ora e|che ore sono|dimmi l ora|che ora abbiamo|ora esatta)$")
_DATE = re.compile(r"^(?:che giorno e|che giorno e oggi|che data e|che data e oggi|dimmi la data)$")
_DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_MONTHS = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]  # fmt: skip


Say = Callable[[ExecResult], str]


@dataclass(frozen=True)
class FastPlan:
    tool: str
    arguments: dict[str, object]
    say: Say


@dataclass(frozen=True)
class FastOutcome:
    tool: str
    arguments: dict[str, object]
    call_id: str
    text: str


def _clean(text: str) -> str:
    return _FILLERS.sub(" ", normalize(text)).strip()


def match(text: str, timezone: str = "Europe/Rome") -> FastPlan | None:
    t = " ".join(_clean(text).split())
    if not t or len(t.split()) > 6:
        return None
    for pattern, action, times, phrase in MEDIA:
        if pattern.match(t):
            return FastPlan("pc.media", {"action": action, "times": times}, _fixed(phrase))
    if _TIME.match(t):
        return FastPlan("time.now", {}, _say_time)
    if _DATE.match(t):
        return FastPlan("time.now", {}, _say_date)
    opened = _OPEN.match(t)
    if opened:
        what = opened.group("what").strip()
        folder = _FOLDER.match(what)
        if folder:
            key = FOLDERS.get(folder.group("name").strip())
            if key:
                phrase = f"Apro la cartella {FOLDER_NAMES[key]}."
                return FastPlan("pc.open_folder", {"folder": key}, _fixed(phrase))
            return None
        if len(what.split()) <= 3 and not what.startswith(("sito", "pagina", "link")):
            return FastPlan("pc.open_app", {"name": what}, _say_open)
    return None


def _fixed(phrase: str) -> Say:
    def say(result: ExecResult) -> str:
        return phrase

    return say


def _payload(result: ExecResult) -> dict[str, object]:
    try:
        data = json.loads(result.content)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _say_open(result: ExecResult) -> str:
    name = str(_payload(result).get("launched", "")).strip()
    return f"Apro {name}." if name else "Fatto."


def _say_time(result: ExecResult) -> str:
    iso = str(_payload(result).get("iso", ""))
    try:
        now = datetime.fromisoformat(iso)
    except ValueError:
        return "Non riesco a leggere l'ora."
    if now.hour == 1:
        return f"È l'una e {now.minute:02d}." if now.minute else "È l'una."
    return (
        f"Sono le {now.hour} e {now.minute:02d}." if now.minute else f"Sono le {now.hour} in punto."
    )


def _say_date(result: ExecResult) -> str:
    iso = str(_payload(result).get("iso", ""))
    try:
        now = datetime.fromisoformat(iso)
    except ValueError:
        return "Non riesco a leggere la data."
    return f"Oggi è {_DAYS[now.weekday()]} {now.day} {_MONTHS[now.month - 1]}."


class FastCommands:
    """Runs a matching command through the ToolExecutor. Returns None to defer to the agent."""

    def __init__(self, registry: ToolRegistry, executor: ToolExecutor, timezone: str) -> None:
        self._registry = registry
        self._executor = executor
        self._timezone = timezone

    async def try_run(self, user_id: str, run_id: uuid.UUID, text: str) -> FastOutcome | None:
        plan = match(text, self._timezone)
        if plan is None or self._registry.get(plan.tool) is None:
            return None
        call = ToolCall(
            id=f"fast_{uuid.uuid4().hex[:8]}", name=plan.tool, arguments=dict(plan.arguments)
        )
        result = await self._executor.execute(call, ExecContext(user_id=user_id, run_id=run_id))
        if (
            result.status != "ok"
        ):  # unclear app name, approval needed, denied...: let the agent handle it
            return None
        return FastOutcome(plan.tool, plan.arguments, call.id, plan.say(result))


__all__ = ["FastCommands", "FastOutcome", "FastPlan", "match"]

from __future__ import annotations

import json

import pytest

from gsoi_assistant.agent.fast_commands import match
from gsoi_assistant.tools.executor import ExecResult


def plan(text: str):  # type: ignore[no-untyped-def]
    return match(text)


@pytest.mark.parametrize(
    ("spoken", "tool", "args"),
    [
        ("apri Chrome", "pc.open_app", {"name": "chrome"}),
        ("Apri Chrome.", "pc.open_app", {"name": "chrome"}),
        ("aprimi Spotify", "pc.open_app", {"name": "spotify"}),
        ("apri il Blocco Note per favore", "pc.open_app", {"name": "blocco note"}),
        ("avvia la calcolatrice", "pc.open_app", {"name": "calcolatrice"}),
        ("lancia Visual Studio Code", "pc.open_app", {"name": "visual studio code"}),
        ("Jarvis, apri Chrome", "pc.open_app", {"name": "chrome"}),
        ("apri la cartella download", "pc.open_folder", {"folder": "downloads"}),
        ("apri la cartella dei documenti", "pc.open_folder", {"folder": "documents"}),
        ("apri la cartella scrivania", "pc.open_folder", {"folder": "desktop"}),
        ("metti in pausa", "pc.media", {"action": "play_pause", "times": 1}),
        ("pausa", "pc.media", {"action": "play_pause", "times": 1}),
        ("riprendi la musica", "pc.media", {"action": "play_pause", "times": 1}),
        ("prossima canzone", "pc.media", {"action": "next", "times": 1}),
        ("brano precedente", "pc.media", {"action": "previous", "times": 1}),
        ("alza il volume", "pc.media", {"action": "volume_up", "times": 5}),
        ("abbassa il volume un po'", "pc.media", {"action": "volume_down", "times": 5}),
        ("muto", "pc.media", {"action": "mute", "times": 1}),
        ("che ore sono?", "time.now", {}),
        ("Che ora è", "time.now", {}),
        ("che giorno è oggi", "time.now", {}),
    ],
)
def test_clear_commands_are_recognised(spoken: str, tool: str, args: dict) -> None:  # type: ignore[type-arg]
    p = plan(spoken)
    assert p is not None, spoken
    assert p.tool == tool and p.arguments == args


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ciao",
        "come stai oggi",
        "riepilogo della giornata",
        "apri il sito youtube",
        "apri la pagina di wikipedia",
        "apri la cartella segreta",  # unknown folder: the model decides what to do
        "apri Chrome e cerca la ricetta della carbonara con le uova",  # too long: a real request
        "scrivi una mail a marco",
        "cancella tutte le note",
        "apri",
        "mi puoi aprire Spotify, quello della musica, per favore?",
        "alza",  # ambiguous
    ],
)
def test_everything_else_goes_to_the_model(text: str) -> None:
    assert plan(text) is None


def result(content: object) -> ExecResult:
    return ExecResult(status="ok", content=json.dumps(content))


def test_spoken_answers_in_italian() -> None:
    p = plan("apri Chrome")
    assert p is not None and p.say(result({"launched": "Google Chrome"})) == "Apro Google Chrome."
    t = plan("che ore sono")
    assert t is not None
    assert t.say(result({"iso": "2026-10-08T14:32:00+02:00"})) == "Sono le 14 e 32."
    assert t.say(result({"iso": "2026-10-08T09:00:00+02:00"})) == "Sono le 9 in punto."
    assert t.say(result({"iso": "2026-10-08T01:05:00+02:00"})) == "È l'una e 05."
    assert t.say(result({"iso": "nonsense"})) == "Non riesco a leggere l'ora."
    d = plan("che giorno è")
    assert d is not None
    assert d.say(result({"iso": "2026-10-08T10:00:00+02:00"})) == "Oggi è giovedì 8 ottobre."
    f = plan("apri la cartella download")
    assert f is not None and f.say(result({"opened": "downloads"})) == "Apro la cartella Download."
    m = plan("alza il volume")
    assert m is not None and m.say(result({"done": True})) == "Alzo il volume."

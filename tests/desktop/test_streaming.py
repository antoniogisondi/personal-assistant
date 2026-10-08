from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from gsoi_desktop.client import ApiError
from gsoi_desktop.controller import AssistantController
from gsoi_desktop.sentences import SentenceSplitter, speakable


def test_splitter_emits_sentences_as_soon_as_they_are_complete() -> None:
    s = SentenceSplitter()
    out: list[str] = []
    for chunk in ["Hai tre email ", "non lette. La pri", "ma è di Marco. Poi", " altro"]:
        out += s.feed(chunk)
    assert out == ["Hai tre email non lette.", "La prima è di Marco."]
    assert s.flush() == ["Poi altro"]


def test_splitter_merges_short_pieces_and_keeps_decimals() -> None:
    s = SentenceSplitter()
    assert s.feed("Sì. Sono le 3.5 ore di ritardo previste oggi. ") == [
        "Sì. Sono le 3.5 ore di ritardo previste oggi."
    ]


def test_markup_is_not_spoken() -> None:
    assert speakable("**Ciao** `x` # titolo") == "Ciao x titolo"


class StreamApi:
    def __init__(self, events: list[tuple[str, dict[str, Any]]]) -> None:
        self.events = events

    def chat_stream(
        self, message: str, cid: str | None, channel: str, on_event: Callable[..., None]
    ) -> None:
        for name, data in self.events:
            on_event(name, data)


def test_tokens_become_sentences_and_the_final_turn_is_returned() -> None:
    events = [
        ("run_started", {"conversation_id": "c1", "run_id": "r1"}),
        ("token", {"delta": "Hai due impegni oggi, "}),
        ("token", {"delta": "il primo è alle nove. Poi hai libero."}),
        ("final", {"content": "Hai due impegni oggi, il primo è alle nove. Poi hai libero."}),
    ]
    ctl = AssistantController(StreamApi(events))  # type: ignore[arg-type]
    said: list[str] = []
    turn = ctl.send_streaming("agenda", "voice", said.append)
    assert said == ["Hai due impegni oggi, il primo è alle nove.", "Poi hai libero."]
    assert turn.text.startswith("Hai due") and ctl.conversation_id == "c1"


def test_a_whole_answer_without_tokens_is_still_spoken() -> None:
    events = [("final", {"content": "Ho aperto Chrome per te, signore."})]
    ctl = AssistantController(StreamApi(events))  # type: ignore[arg-type]
    said: list[str] = []
    ctl.send_streaming("apri chrome", "voice", said.append)
    assert said == ["Ho aperto Chrome per te, signore."]


def test_an_approval_request_is_returned_not_spoken() -> None:
    ev = {
        "approval_id": "a1", "tool": "email.send", "risk": "EXTERNAL", "strength": "normal",
        "display": {"summary": "Invia email", "arguments": {}},
    }  # fmt: skip
    ctl = AssistantController(StreamApi([("approval_required", ev)]))  # type: ignore[arg-type]
    said: list[str] = []
    turn = ctl.send_streaming("scrivi", "voice", said.append)
    assert said == [] and turn.approval is not None and turn.approval.approval_id == "a1"


def test_stream_errors_are_raised() -> None:
    ctl = AssistantController(StreamApi([("error", {"message": "boom"})]))  # type: ignore[arg-type]
    with pytest.raises(ApiError, match="boom"):
        ctl.send_streaming("x", "voice", lambda s: None)

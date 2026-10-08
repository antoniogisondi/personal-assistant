"""Shared helpers for the Qt tests."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QApplication

from gsoi_desktop.controller import Approval, Turn


def wait_until(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        QApplication.processEvents()
        if time.monotonic() > end:
            raise AssertionError("condition not reached in time")
        time.sleep(0.005)
    QApplication.processEvents()


class FakeChat:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.sentences: list[str] = []  # sent live before the turn completes
        self.next: Turn | Exception = Turn(text="Ciao!")
        self.decision_result = Turn(text="Email inviata.")

    def send(self, text: str, channel: str = "text") -> Turn:
        self.calls.append(("send", text if channel == "text" else (text, channel)))
        if isinstance(self.next, Exception):
            raise self.next
        return self.next

    def send_streaming(self, text: str, channel: str, on_sentence: Any) -> Turn:
        turn = self.send(text, channel)
        for sentence in self.sentences:
            on_sentence(sentence)
        return turn

    def briefing(self) -> Turn:
        self.calls.append(("briefing", None))
        return Turn(text="Buongiorno signore, hai due impegni.")

    def decide(self, approval: Approval, approve: bool) -> Turn:
        self.calls.append(("decide", approve))
        return self.decision_result

    def new_conversation(self) -> None:
        self.calls.append(("new", None))


class FakeSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.stopped = 0
        self.active = False

    def speak(self, text: str) -> None:
        self.said.append(text)

    def enqueue(self, text: str) -> None:
        self.said.append(text)
        self.active = True

    def stop(self) -> None:
        self.stopped += 1

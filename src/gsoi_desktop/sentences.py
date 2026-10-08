"""Cuts a stream of text into sentences that can be spoken as soon as they are complete."""

from __future__ import annotations

import re

_END = re.compile(r"(?<=[.!?…])\s+|\n+")
_MARKDOWN = re.compile(r"[*_`#>]+")
MIN_CHARS = 16  # shorter pieces ("Sì.", "Dr.") are merged into the next sentence


def speakable(text: str) -> str:
    """Remove markup a voice would read out loud."""
    return " ".join(_MARKDOWN.sub("", text).split())


class SentenceSplitter:
    def __init__(self, min_chars: int = MIN_CHARS) -> None:
        self._min = min_chars
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        out: list[str] = []
        start = 0
        for m in _END.finditer(self._buffer):
            piece = self._buffer[start : m.start()]
            if len(speakable(piece)) >= self._min:
                out.append(speakable(piece))
                start = m.end()
        self._buffer = self._buffer[start:]
        return out

    def flush(self) -> list[str]:
        rest = speakable(self._buffer)
        self._buffer = ""
        return [rest] if rest else []

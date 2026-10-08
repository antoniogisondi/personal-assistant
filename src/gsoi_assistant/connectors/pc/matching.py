"""Pure helpers: sanitising names, fuzzy-matching spoken app names, validating URLs."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import urlsplit

_CONTROL = re.compile(r"[\x00-\x1f\x7f<>\"`]")
_WORDS = re.compile(r"[a-z0-9]+")


def clean_name(name: str, limit: int = 60) -> str:
    """Names come from the operating system (installed programs): never trust them as text."""
    return _CONTROL.sub("", name).strip()[:limit]


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(_WORDS.findall(folded.lower()))


@dataclass(frozen=True)
class Match:
    name: str
    score: float


def score(query: str, name: str) -> float:
    q, n = normalize(query), normalize(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    q_tokens, n_tokens = set(q.split()), set(n.split())
    if q_tokens <= n_tokens:  # "chrome" -> "google chrome"
        return 0.92 - 0.01 * (len(n_tokens) - len(q_tokens))
    if n.startswith(q) or q.startswith(n):
        return 0.85
    return SequenceMatcher(None, q, n).ratio() * 0.8


def rank(query: str, names: list[str]) -> list[Match]:
    return sorted((Match(n, score(query, n)) for n in names), key=lambda m: m.score, reverse=True)


@dataclass(frozen=True)
class Resolution:
    chosen: str | None
    candidates: list[str]


def resolve(
    query: str, names: list[str], *, threshold: float = 0.7, margin: float = 0.06
) -> Resolution:
    """Pick an application only when the match is clear; else return candidates to ask about."""
    ranked = [m for m in rank(query, names) if m.score >= 0.45]
    if not ranked:
        return Resolution(None, [])
    top = ranked[0]
    clear = top.score >= threshold and (len(ranked) == 1 or top.score - ranked[1].score >= margin)
    if clear or (top.score == 1.0):
        return Resolution(top.name, [])
    return Resolution(None, [m.name for m in ranked[:5]])


class UnsafeUrlError(ValueError):
    pass


def check_url(url: str) -> str:
    url = url.strip()
    if len(url) > 2000 or any(c in url for c in "\r\n\t "):
        raise UnsafeUrlError("the address is malformed")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeUrlError("only http and https addresses can be opened")
    if parts.username or parts.password:
        raise UnsafeUrlError("addresses with embedded credentials are not allowed")
    return url

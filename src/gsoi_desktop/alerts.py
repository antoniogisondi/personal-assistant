"""What the desktop shows when the watcher reports something new (no Qt)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Alert:
    kind: str  # "email" | "event"
    key: str
    title: str
    text: str
    spoken: str
    important: bool = False


def parse_alerts(data: dict[str, Any]) -> list[Alert]:
    out: list[Alert] = []
    for raw in data.get("alerts", []):
        try:
            out.append(
                Alert(
                    kind=str(raw["kind"]),
                    key=str(raw["key"]),
                    title=str(raw["title"]),
                    text=str(raw.get("text", "")),
                    spoken=str(raw.get("spoken", raw["title"])),
                    important=bool(raw.get("important", False)),
                )
            )
        except KeyError:
            continue
    return out


def is_quiet(hour: int, start: int, end: int) -> bool:
    """Quiet hours (alerts are not spoken): `start` to `end`, possibly across midnight."""
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


def spoken_summary(alerts: list[Alert], limit: int = 3) -> str:
    """One utterance for a batch: the first few in full, then how many more there are."""
    ordered = sorted(alerts, key=lambda a: (a.kind != "event", not a.important))
    parts = [a.spoken for a in ordered[:limit]]
    if len(ordered) > limit:
        parts.append(f"E altre {len(ordered) - limit} novità.")
    return " ".join(parts)

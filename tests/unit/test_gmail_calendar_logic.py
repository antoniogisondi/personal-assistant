from __future__ import annotations

import base64
from datetime import date, datetime, time, timedelta
from email import message_from_bytes
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from gsoi_assistant.connectors.google.calendar import (
    CreateEventIn,
    FreeSlotsIn,
    compute_free_slots,
    to_aware,
)
from gsoi_assistant.connectors.google.gmail import (
    ComposeIn,
    build_rfc822,
    extract_body,
    html_to_text,
)

ROME = ZoneInfo("Europe/Rome")


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")  # Gmail omits padding


# ---- Gmail parsing ---------------------------------------------------------------------


def test_prefers_plain_text_and_lists_attachments() -> None:
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": b64("Ciao Marco, è tutto ok.")}},
                    {"mimeType": "text/html", "body": {"data": b64("<p>Ciao <b>HTML</b></p>")}},
                ],
            },
            {
                "mimeType": "application/pdf",
                "filename": "offerta.pdf",
                "body": {"attachmentId": "x"},
            },
        ],
    }
    text, files = extract_body(payload)
    assert text == "Ciao Marco, è tutto ok." and files == ["offerta.pdf"]


def test_falls_back_to_html_without_scripts() -> None:
    payload = {
        "mimeType": "text/html",
        "body": {
            "data": b64(
                "<style>p{}</style><p>Riunione alle 10</p><script>evil()</script><p>Sala 2</p>"
            )
        },
    }
    text, _ = extract_body(payload)
    assert "Riunione alle 10" in text and "Sala 2" in text
    assert "evil" not in text and "p{}" not in text


def test_html_to_text_collapses_whitespace() -> None:
    assert html_to_text("<div>a</div>\n\n\n<div>   b   </div>") == "a\n\nb"


# ---- composing ---------------------------------------------------------------------------


def compose(**kw: object) -> ComposeIn:
    base: dict[str, object] = {"to": ["marco@example.com"], "subject": "Ciao", "body": "Testo"}
    base.update(kw)
    return ComposeIn.model_validate(base)


def test_rfc822_has_expected_headers_and_threading() -> None:
    raw = build_rfc822(
        compose(cc=["anna@example.com"]), in_reply_to="<abc@mail.gmail.com>", references="<r1@x>"
    )
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["To"] == "marco@example.com" and msg["Cc"] == "anna@example.com"
    assert msg["Subject"] == "Ciao" and msg["In-Reply-To"] == "<abc@mail.gmail.com>"
    assert msg["References"] == "<r1@x> <abc@mail.gmail.com>"
    assert msg.get_payload(decode=True).decode().strip() == "Testo"  # type: ignore[union-attr]


def test_non_ascii_subject_and_body_survive() -> None:
    raw = build_rfc822(compose(subject="Preventivo città", body="Perché sì ✓"))
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert "Perché sì ✓" in msg.get_payload(decode=True).decode()  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "bad",
    [
        {"to": ["marco@example.com\nBcc: evil@x.com"]},
        {"to": ["Marco <marco@example.com>"]},
        {"to": ["marco@example.com, evil@x.com"]},
        {"to": ["not-an-address"]},
        {"to": []},
        {"to": ["a@b.co"] * 11},
        {"cc": ["x@y"]},
        {"subject": "Hi\nBcc: evil@x.com"},
        {"subject": ""},
        {"body": ""},
        {"reply_to_message_id": "../../etc"},
    ],
)
def test_compose_rejects_header_injection_and_bad_input(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        compose(**bad)


# ---- free slots -------------------------------------------------------------------------


MON = date(2026, 10, 12)  # a Monday
LONG_AGO = datetime(2026, 1, 1, tzinfo=ROME)


def dt(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m), ROME)


def slots(busy: list[tuple[datetime, datetime]], **kw: object) -> list[tuple[datetime, datetime]]:
    args: dict[str, object] = {
        "date_from": MON,
        "date_to": MON,
        "duration": timedelta(minutes=45),
        "day_start": time(9),
        "day_end": time(18),
        "tz": ROME,
        "now": LONG_AGO,
    }
    args.update(kw)
    return compute_free_slots(busy, **args)  # type: ignore[arg-type]


def test_empty_calendar_gives_whole_window() -> None:
    assert slots([]) == [(dt(MON, 9), dt(MON, 18))]


def test_gaps_around_meetings() -> None:
    busy = [(dt(MON, 10), dt(MON, 11)), (dt(MON, 14), dt(MON, 15, 30))]
    assert slots(busy) == [
        (dt(MON, 9), dt(MON, 10)),
        (dt(MON, 11), dt(MON, 14)),
        (dt(MON, 15, 30), dt(MON, 18)),
    ]


def test_overlapping_and_adjacent_busy_blocks_are_merged() -> None:
    busy = [(dt(MON, 10), dt(MON, 12)), (dt(MON, 11), dt(MON, 13)), (dt(MON, 13), dt(MON, 14))]
    assert slots(busy) == [(dt(MON, 9), dt(MON, 10)), (dt(MON, 14), dt(MON, 18))]


def test_gaps_shorter_than_duration_are_dropped() -> None:
    busy = [(dt(MON, 9, 30), dt(MON, 17, 30))]
    assert slots(busy) == []  # 30 min at each end < 45


def test_busy_outside_window_is_ignored_and_all_day_busy_blocks_everything() -> None:
    assert slots([(dt(MON, 7), dt(MON, 8, 30))]) == [(dt(MON, 9), dt(MON, 18))]
    assert slots([(dt(MON, 0), dt(MON, 23, 59))]) == []


def test_meeting_straddling_the_window_edge() -> None:
    busy = [(dt(MON, 8), dt(MON, 9, 30)), (dt(MON, 17), dt(MON, 19))]
    assert slots(busy) == [(dt(MON, 9, 30), dt(MON, 17))]


def test_past_time_is_never_offered() -> None:
    assert slots([], now=dt(MON, 13, 10)) == [(dt(MON, 13, 10), dt(MON, 18))]
    assert slots([], now=dt(MON, 18, 0)) == []


def test_weekends_skipped_unless_asked() -> None:
    sat, sun = date(2026, 10, 17), date(2026, 10, 18)
    assert slots([], date_from=sat, date_to=sun) == []
    assert len(slots([], date_from=sat, date_to=sun, weekdays_only=False)) == 2


def test_multiple_days() -> None:
    out = slots([(dt(MON, 9), dt(MON, 18))], date_to=MON + timedelta(days=1))
    assert out == [(dt(MON + timedelta(days=1), 9), dt(MON + timedelta(days=1), 18))]


# ---- inputs -----------------------------------------------------------------------------


def test_naive_datetimes_are_interpreted_in_user_timezone() -> None:
    assert to_aware(datetime(2026, 10, 12, 9), ROME).utcoffset() == timedelta(hours=2)
    utc = datetime(2026, 10, 12, 9, tzinfo=ZoneInfo("UTC"))
    assert to_aware(utc, ROME).hour == 11


def test_free_slot_range_validation() -> None:
    with pytest.raises(ValidationError):
        FreeSlotsIn(date_from=MON, date_to=MON - timedelta(days=1))
    with pytest.raises(ValidationError):
        FreeSlotsIn(date_from=MON, date_to=MON + timedelta(days=60))


def test_create_event_validation() -> None:
    ok = CreateEventIn(title="x", start=datetime(2026, 10, 12, 9), end=datetime(2026, 10, 12, 10))
    assert ok.attendees == []
    with pytest.raises(ValidationError):
        CreateEventIn(title="x", start=datetime(2026, 10, 12, 10), end=datetime(2026, 10, 12, 9))
    with pytest.raises(ValidationError):
        CreateEventIn(
            title="x",
            start=datetime(2026, 10, 12, 9),
            end=datetime(2026, 10, 12, 10),
            attendees=["bad"],
        )

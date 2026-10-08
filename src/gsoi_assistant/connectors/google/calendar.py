"""Google Calendar: list events, find free slots, create events."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

from gsoi_assistant.connectors.google.api import GoogleApi
from gsoi_assistant.connectors.google.auth import CALENDAR_EVENTS, CALENDAR_READONLY
from gsoi_assistant.connectors.google.gmail import _check_addresses
from gsoi_assistant.core.errors import ToolError
from gsoi_assistant.core.types import Risk
from gsoi_assistant.tools.base import ToolContext, ToolSpec
from gsoi_assistant.tools.registry import AnyTool

BASE = "https://www.googleapis.com/calendar/v3"


def to_aware(dt: datetime, tz: ZoneInfo) -> datetime:
    """Naive date-times from the model are interpreted in the user's time zone."""
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


# ---- models -----------------------------------------------------------------------------


class ListEventsIn(BaseModel):
    start: datetime = Field(
        description="ISO 8601 start of the window (user's time zone if no offset)."
    )
    end: datetime = Field(description="ISO 8601 end of the window.")
    max_results: int = Field(default=25, ge=1, le=50)


class EventOut(BaseModel):
    id: str
    title: str
    start: str
    end: str
    all_day: bool
    location: str
    organizer: str
    attendees_count: int
    description: str


class ListEventsOut(BaseModel):
    events: list[EventOut]


class FreeSlotsIn(BaseModel):
    date_from: date
    date_to: date
    duration_minutes: int = Field(default=30, ge=10, le=480)
    day_start: time = Field(
        default=time(9, 0), description="Earliest start of the day, local time."
    )
    day_end: time = Field(default=time(18, 0), description="Latest end of the day, local time.")
    weekdays_only: bool = True
    limit: int = Field(default=8, ge=1, le=20)

    @field_validator("date_to")
    @classmethod
    def _range(cls, v: date, info: Any) -> date:
        start = info.data.get("date_from")
        if start and (v < start or (v - start).days > 31):
            raise ValueError("date_to must be on/after date_from and within 31 days")
        return v


class Slot(BaseModel):
    start: str
    end: str


class FreeSlotsOut(BaseModel):
    slots: list[Slot]


class CreateEventIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start: datetime
    end: datetime
    attendees: list[str] = Field(default_factory=list, max_length=20)
    location: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator("attendees")
    @classmethod
    def _emails(cls, v: list[str]) -> list[str]:
        return _check_addresses(v)

    @field_validator("end")
    @classmethod
    def _after_start(cls, v: datetime, info: Any) -> datetime:
        start = info.data.get("start")
        if start is not None and (v.tzinfo is None) == (start.tzinfo is None) and v <= start:
            raise ValueError("end must be after start")
        return v


class CreatedEventOut(BaseModel):
    event_id: str
    link: str


# ---- pure logic --------------------------------------------------------------------------


def compute_free_slots(
    busy: list[tuple[datetime, datetime]],
    *,
    date_from: date,
    date_to: date,
    duration: timedelta,
    day_start: time,
    day_end: time,
    tz: ZoneInfo,
    now: datetime,
    weekdays_only: bool = True,
) -> list[tuple[datetime, datetime]]:
    """Maximal free intervals (>= duration) inside each day's working window."""
    merged: list[tuple[datetime, datetime]] = []
    for s, e in sorted(busy):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    slots: list[tuple[datetime, datetime]] = []
    day = date_from
    while day <= date_to:
        if not (weekdays_only and day.weekday() >= 5):
            window_start = max(datetime.combine(day, day_start, tz), now)
            window_end = datetime.combine(day, day_end, tz)
            cursor = window_start
            for b_start, b_end in merged:
                if b_end <= cursor or b_start >= window_end:
                    continue
                if b_start - cursor >= duration:
                    slots.append((cursor, b_start))
                cursor = max(cursor, b_end)
            if window_end - cursor >= duration:
                slots.append((cursor, window_end))
        day += timedelta(days=1)
    return slots


def _parse_event(item: dict[str, Any]) -> EventOut | None:
    if item.get("status") == "cancelled":
        return None
    start, end = item.get("start", {}), item.get("end", {})
    all_day = "date" in start and "dateTime" not in start
    return EventOut(
        id=item.get("id", ""),
        title=item.get("summary", "(no title)"),
        start=start.get("dateTime") or start.get("date", ""),
        end=end.get("dateTime") or end.get("date", ""),
        all_day=all_day,
        location=item.get("location", ""),
        organizer=(item.get("organizer") or {}).get("email", ""),
        attendees_count=len(item.get("attendees") or []),
        description=str(item.get("description", ""))[:300],
    )


# ---- client --------------------------------------------------------------------------------


class CalendarClient:
    def __init__(self, api: GoogleApi, tz: ZoneInfo) -> None:
        self._api = api
        self.tz = tz

    async def list_events(
        self, user_id: str, start: datetime, end: datetime, max_results: int = 25
    ) -> list[EventOut]:
        data = await self._api.request(
            user_id,
            "GET",
            f"{BASE}/calendars/primary/events",
            scopes=(CALENDAR_READONLY,),
            params={
                "timeMin": to_aware(start, self.tz).isoformat(),
                "timeMax": to_aware(end, self.tz).isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": str(max_results),
            },
        )
        events = (_parse_event(i) for i in data.get("items", []))
        return [e for e in events if e is not None]

    async def free_slots(self, user_id: str, args: FreeSlotsIn) -> list[tuple[datetime, datetime]]:
        window_start = datetime.combine(args.date_from, time.min, self.tz)
        window_end = datetime.combine(args.date_to + timedelta(days=1), time.min, self.tz)
        data = await self._api.request(
            user_id,
            "POST",
            f"{BASE}/freeBusy",
            scopes=(CALENDAR_READONLY,),
            json={
                "timeMin": window_start.isoformat(),
                "timeMax": window_end.isoformat(),
                "timeZone": str(self.tz),
                "items": [{"id": "primary"}],
            },
        )
        raw = ((data.get("calendars") or {}).get("primary") or {}).get("busy", [])
        busy = [
            (
                datetime.fromisoformat(b["start"]).astimezone(self.tz),
                datetime.fromisoformat(b["end"]).astimezone(self.tz),
            )
            for b in raw
        ]
        return compute_free_slots(
            busy,
            date_from=args.date_from,
            date_to=args.date_to,
            duration=timedelta(minutes=args.duration_minutes),
            day_start=args.day_start,
            day_end=args.day_end,
            tz=self.tz,
            now=datetime.now(self.tz),
            weekdays_only=args.weekdays_only,
        )

    async def create_event(self, user_id: str, args: CreateEventIn) -> CreatedEventOut:
        start, end = to_aware(args.start, self.tz), to_aware(args.end, self.tz)
        if end <= start:
            raise ToolError("the event must end after it starts")
        body: dict[str, Any] = {
            "summary": args.title,
            "start": {"dateTime": start.isoformat(), "timeZone": str(self.tz)},
            "end": {"dateTime": end.isoformat(), "timeZone": str(self.tz)},
        }
        if args.location:
            body["location"] = args.location
        if args.description:
            body["description"] = args.description
        if args.attendees:
            body["attendees"] = [{"email": a} for a in args.attendees]
        data = await self._api.request(
            user_id,
            "POST",
            f"{BASE}/calendars/primary/events",
            scopes=(CALENDAR_EVENTS,),
            params={"sendUpdates": "all" if args.attendees else "none"},
            json=body,
        )
        return CreatedEventOut(event_id=data["id"], link=data.get("htmlLink", ""))


# ---- tools ---------------------------------------------------------------------------------


def make_calendar_tools(client: CalendarClient) -> list[AnyTool]:
    async def list_events(args: ListEventsIn, ctx: ToolContext) -> ListEventsOut:
        return ListEventsOut(
            events=await client.list_events(ctx.user_id, args.start, args.end, args.max_results)
        )

    async def free_slots(args: FreeSlotsIn, ctx: ToolContext) -> FreeSlotsOut:
        slots = await client.free_slots(ctx.user_id, args)
        return FreeSlotsOut(
            slots=[Slot(start=s.isoformat(), end=e.isoformat()) for s, e in slots[: args.limit]]
        )

    async def create_event(args: CreateEventIn, ctx: ToolContext) -> CreatedEventOut:
        return await client.create_event(ctx.user_id, args)

    return [
        ToolSpec(
            name="calendar.list_events",
            description="List events in the user's primary Google Calendar between two date-times.",
            input_model=ListEventsIn,
            handler=list_events,
            risk=Risk.READ,
            untrusted_output=True,  # titles/descriptions of invitations come from other people
            scopes=(CALENDAR_READONLY,),
        ),
        ToolSpec(
            name="calendar.find_free_slots",
            description=(
                "Find free time slots (computed from the calendar, not guessed) of at least the "
                "given duration within working hours."
            ),
            input_model=FreeSlotsIn,
            handler=free_slots,
            risk=Risk.READ,
            scopes=(CALENDAR_READONLY,),
        ),
        ToolSpec(
            name="calendar.create_event",
            description=(
                "Create an event in the user's calendar. Always requires the user's approval; "
                "attendees, if any, receive invitations."
            ),
            input_model=CreateEventIn,
            handler=create_event,
            risk=Risk.EXTERNAL,
            scopes=(CALENDAR_EVENTS,),
            summarize=lambda a: (
                f"Create event “{a.title}” {a.start.isoformat()} → {a.end.isoformat()}"
                + (f", inviting {', '.join(a.attendees)}" if a.attendees else "")
            ),
        ),
    ]

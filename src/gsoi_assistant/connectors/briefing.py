"""briefing.today: one deterministic snapshot of the user's day.

Counts and lists come from the connected services, never from the model: the model only turns
them into a spoken greeting. A source that is not connected or fails is reported in
`unavailable` instead of failing the whole briefing.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel

from gsoi_assistant.connectors.google.calendar import CalendarClient, EventOut
from gsoi_assistant.connectors.google.gmail import EmailSummary, GmailClient
from gsoi_assistant.core.errors import ToolError
from gsoi_assistant.core.types import Risk
from gsoi_assistant.tools.base import ToolContext, ToolSpec
from gsoi_assistant.tools.registry import AnyTool

log = structlog.get_logger(__name__)
UNREAD_QUERY = "is:unread in:inbox newer_than:1d"
IMPORTANT_QUERY = "is:unread is:important in:inbox newer_than:1d"


class NoArgs(BaseModel):
    pass


class TaskBrief(BaseModel):
    title: str
    due: str | None


class BriefingOut(BaseModel):
    now: str
    weekday: str
    events_today: list[EventOut] | None  # None = source unavailable
    unread_emails_last_24h: int | None
    unread_count_is_lower_bound: bool
    important_emails: list[EmailSummary] | None
    open_tasks: list[TaskBrief]
    open_tasks_count: int
    unavailable: list[str]


def make_briefing_tool(
    gmail: GmailClient | None, calendar: CalendarClient | None, tz: ZoneInfo
) -> AnyTool:
    async def briefing(_: NoArgs, ctx: ToolContext) -> BriefingOut:
        now = datetime.now(tz)
        day_start = datetime.combine(now.date(), time.min, tz)
        unavailable: list[str] = []

        events: list[EventOut] | None = None
        if calendar is not None:
            try:
                events = await calendar.list_events(
                    ctx.user_id, day_start, day_start + timedelta(days=1), 50
                )
            except ToolError as exc:
                log.info("briefing_calendar_unavailable", reason=str(exc)[:120])
                unavailable.append(f"calendar: {exc}")
        else:
            unavailable.append("calendar: not configured")

        unread: int | None = None
        lower_bound = False
        important: list[EmailSummary] | None = None
        if gmail is not None:
            try:
                found = await gmail.search(ctx.user_id, UNREAD_QUERY, 25)
                unread, lower_bound = len(found.emails), found.more_available
                important = (await gmail.search(ctx.user_id, IMPORTANT_QUERY, 5)).emails
            except ToolError as exc:
                log.info("briefing_gmail_unavailable", reason=str(exc)[:120])
                unavailable.append(f"email: {exc}")
        else:
            unavailable.append("email: not configured")

        tasks = await ctx.services.tasks.list(ctx.user_id, "open", 50)
        return BriefingOut(
            now=now.isoformat(timespec="minutes"),
            weekday=now.strftime("%A"),
            events_today=events,
            unread_emails_last_24h=unread,
            unread_count_is_lower_bound=lower_bound,
            important_emails=important,
            open_tasks=[
                TaskBrief(title=t.title, due=t.due_at.isoformat() if t.due_at else None)
                for t in tasks[:10]
            ],
            open_tasks_count=len(tasks),
            unavailable=unavailable,
        )

    return ToolSpec(
        name="briefing.today",
        description=(
            "Snapshot of the user's day: today's calendar events, unread/important emails from the "
            "last 24h, and open tasks. Counts are exact; use them as given."
        ),
        input_model=NoArgs,
        handler=briefing,
        risk=Risk.READ,
        untrusted_output=True,  # email subjects and event titles come from third parties
        timeout_s=60,
    )

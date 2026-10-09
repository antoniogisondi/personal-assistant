"""Background watch: tells the user about NEW email and appointments that are about to start.

The desktop app asks every minute or two. Each item is announced once (remembered in the
database). The first check only records what is already in the inbox, so connecting an account
does not produce a flood of alerts. What this module returns is only for showing or speaking to
the user: mail contents are third-party text and never go to the language model from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import structlog

from gsoi_assistant.connectors.google.calendar import CalendarClient
from gsoi_assistant.connectors.google.gmail import EmailSummary, GmailClient
from gsoi_assistant.connectors.mail.accounts import MailAccounts
from gsoi_assistant.connectors.mail.client import MailAccount, MailSummary
from gsoi_assistant.core.errors import ToolError
from gsoi_assistant.db.stores import SeenStore, utcnow

log = structlog.get_logger(__name__)

# Real mail, not newsletters, promotions or social notifications.
NEW_MAIL_QUERY = (
    "in:inbox is:unread newer_than:1d -category:promotions -category:social -category:forums"
)
MAX_ANNOUNCED_PER_CHECK = 5
BASELINE = "__baseline__"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SENDER = re.compile(r'^\s*"?([^"<]+?)"?\s*<[^>]+>\s*$')


@dataclass(frozen=True)
class Alert:
    kind: Literal["email", "event"]
    key: str
    title: str  # short heading for a notification
    text: str  # one line of details
    spoken: str  # what to say aloud
    important: bool = False


@dataclass(frozen=True)
class CheckResult:
    alerts: list[Alert]
    unavailable: list[str]


def clean(text: str, limit: int = 140) -> str:
    """Third-party text, shortened and stripped of control characters."""
    one_line = " ".join(_CONTROL.sub(" ", text).split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1].rstrip() + "…"


def sender_name(sender: str) -> str:
    m = _SENDER.match(sender)
    return clean(m.group(1) if m else sender, 60) or "un mittente sconosciuto"


def email_alert(mail: EmailSummary) -> Alert:
    who = sender_name(mail.sender)
    subject = clean(mail.subject, 100)
    return Alert(
        kind="email",
        key=mail.id,
        title=f"Nuova email da {who}",
        text=subject,
        spoken=f"Nuova email da {who}: {subject}.",
        important=mail.important,
    )


def event_alert(event_id: str, start: datetime, title: str, now: datetime, location: str) -> Alert:
    minutes = max(0, round((start - now).total_seconds() / 60))
    name = clean(title, 100) or "un appuntamento"
    when = "adesso" if minutes <= 0 else f"tra {minutes} minut{'o' if minutes == 1 else 'i'}"
    where = f", {clean(location, 60)}" if location else ""
    return Alert(
        kind="event",
        key=f"{event_id}@{start.isoformat()}",
        title=f"{name} {when}",
        text=f"{start:%H:%M}{where}",
        spoken=f"Promemoria: {name} {when}{where}.",
        important=True,
    )


class Watcher:
    def __init__(
        self,
        gmail: GmailClient | None,
        calendar: CalendarClient | None,
        seen: SeenStore,
        tz: ZoneInfo,
        mail: MailAccounts | None = None,
    ) -> None:
        self._mail = mail
        self._gmail = gmail
        self._calendar = calendar
        self._seen = seen
        self._tz = tz

    async def check(
        self, user_id: str, lead_minutes: int = 10, now: datetime | None = None
    ) -> CheckResult:
        now = now or datetime.now(self._tz)
        alerts: list[Alert] = []
        unavailable: list[str] = []
        try:
            alerts += await self._new_mail(user_id)
        except ToolError as exc:
            unavailable.append(f"email: {exc}")
        try:
            alerts += await self._other_mailboxes(user_id, unavailable)
        except ToolError as exc:
            unavailable.append(f"mail: {exc}")
        try:
            alerts += await self._upcoming(user_id, lead_minutes, now)
        except ToolError as exc:
            unavailable.append(f"calendar: {exc}")
        await self._seen.prune(utcnow() - timedelta(days=14))
        return CheckResult(alerts, unavailable)

    async def _new_mail(self, user_id: str) -> list[Alert]:
        if self._gmail is None:
            return []
        found = await self._gmail.search(user_id, NEW_MAIL_QUERY, 15)
        ids = [m.id for m in found.emails]
        if not await self._seen.has_any(user_id, "email"):
            # First run: remember what is already there instead of announcing all of it.
            await self._seen.add(user_id, "email", [*ids, BASELINE])
            return []
        fresh = set(await self._seen.new_keys(user_id, "email", ids))
        new = [m for m in found.emails if m.id in fresh]
        await self._seen.add(user_id, "email", [m.id for m in new])
        # An important message first; never an avalanche.
        new.sort(key=lambda m: not m.important)
        if len(new) > MAX_ANNOUNCED_PER_CHECK:
            log.info("watch_many_new_emails", count=len(new))
        return [email_alert(m) for m in new[:MAX_ANNOUNCED_PER_CHECK]]

    async def _other_mailboxes(self, user_id: str, unavailable: list[str]) -> list[Alert]:
        if self._mail is None:
            return []
        alerts: list[Alert] = []
        for account in await self._mail.list(user_id):
            try:
                alerts += await self._new_in_account(user_id, account)
            except ToolError as exc:  # one broken account must not silence the others
                unavailable.append(f"{account.label}: {exc}")
        return alerts

    async def _new_in_account(self, user_id: str, account: MailAccount) -> list[Alert]:
        assert self._mail is not None  # nosec B101
        kind = f"m{account.id}"
        found = await self._mail.client.search(account, unread_only=True, since_days=2, limit=15)
        keys = [m.uid for m in found]
        if not await self._seen.has_any(user_id, kind):
            await self._seen.add(user_id, kind, [*keys, BASELINE])
            return []
        fresh = set(await self._seen.new_keys(user_id, kind, keys))
        new = [m for m in found if m.uid in fresh]
        await self._seen.add(user_id, kind, [m.uid for m in new])
        return [self._account_alert(account, m) for m in new[:MAX_ANNOUNCED_PER_CHECK]]

    @staticmethod
    def _account_alert(account: MailAccount, mail: MailSummary) -> Alert:
        who = sender_name(mail.sender)
        subject = clean(mail.subject, 100)
        return Alert(
            kind="email",
            key=f"{account.id}:{mail.uid}",
            title=f"Nuova email da {who} ({account.label})",
            text=subject,
            spoken=f"Nuova email su {account.label} da {who}: {subject}.",
        )

    async def _upcoming(self, user_id: str, lead_minutes: int, now: datetime) -> list[Alert]:
        if self._calendar is None:
            return []
        events = await self._calendar.list_events(
            user_id, now, now + timedelta(minutes=lead_minutes), 10
        )
        candidates: list[tuple[str, str, datetime, str, str]] = []
        for e in events:
            if e.all_day:
                continue
            try:
                start = datetime.fromisoformat(e.start)
            except ValueError:
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=self._tz)
            if now - timedelta(minutes=1) <= start <= now + timedelta(minutes=lead_minutes):
                key = f"{e.id}@{start.isoformat()}"
                candidates.append((key, e.id, start, e.title, e.location))
        fresh = set(await self._seen.new_keys(user_id, "event", [c[0] for c in candidates]))
        chosen = [c for c in candidates if c[0] in fresh]
        await self._seen.add(user_id, "event", [c[0] for c in chosen])
        return [event_alert(eid, start, title, now, loc) for _, eid, start, title, loc in chosen]

"""Gmail: search, read, draft, send.

Everything read from a mailbox is third-party content: tool outputs are marked untrusted.
Sending is EXTERNAL (always needs the user's approval, bound to the exact recipients, subject
and body that are shown to them)."""

from __future__ import annotations

import asyncio
import base64
import re
from email.message import EmailMessage
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel, Field, field_validator

from gsoi_assistant.connectors.google.api import GoogleApi
from gsoi_assistant.connectors.google.auth import GMAIL_COMPOSE, GMAIL_READONLY
from gsoi_assistant.core.errors import ToolError
from gsoi_assistant.core.types import DataClass, Risk
from gsoi_assistant.tools.base import ToolContext, ToolSpec
from gsoi_assistant.tools.registry import AnyTool

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_ADDR = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"']+$")
_ID = r"^[A-Za-z0-9_-]{6,64}$"
MAX_BODY_CHARS = 6000


# ---- models ---------------------------------------------------------------------------


class SearchIn(BaseModel):
    query: str = Field(
        default="in:inbox newer_than:2d",
        max_length=200,
        description="Gmail search syntax, e.g. 'from:marco is:unread newer_than:7d'.",
    )
    max_results: int = Field(default=10, ge=1, le=25)


class EmailSummary(BaseModel):
    id: str
    thread_id: str
    sender: str
    subject: str
    date: str
    snippet: str
    unread: bool
    important: bool


class SearchOut(BaseModel):
    emails: list[EmailSummary]
    more_available: bool = False


class ReadIn(BaseModel):
    message_id: str = Field(pattern=_ID)


class ReadOut(BaseModel):
    id: str
    thread_id: str
    sender: str
    to: str
    cc: str
    subject: str
    date: str
    body: str
    attachments: list[str]
    truncated: bool


def _check_addresses(values: list[str]) -> list[str]:
    for v in values:
        if not _ADDR.match(v.strip()):
            raise ValueError(f"'{v}' is not a plain email address")
    return [v.strip() for v in values]


class ComposeIn(BaseModel):
    to: list[str] = Field(min_length=1, max_length=10)
    cc: list[str] = Field(default_factory=list, max_length=10)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)
    reply_to_message_id: str | None = Field(
        default=None,
        pattern=_ID,
        description="Id of the message being answered (keeps the thread).",
    )

    @field_validator("to", "cc")
    @classmethod
    def _addresses(cls, v: list[str]) -> list[str]:
        return _check_addresses(v)

    @field_validator("subject")
    @classmethod
    def _no_newlines(cls, v: str) -> str:
        if "\n" in v or "\r" in v:
            raise ValueError("subject must be a single line")
        return v.strip()


class DraftOut(BaseModel):
    draft_id: str


class SentOut(BaseModel):
    message_id: str
    thread_id: str


# ---- pure helpers ----------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    text = re.sub(r" ?\n ?", "\n", text)  # no stray spaces around line breaks
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def headers_of(payload: dict[str, Any]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", []) if "name" in h}


def extract_body(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Return (text, attachment filenames). Prefers text/plain; falls back to stripped HTML."""
    plain: list[str] = []
    html: list[str] = []
    files: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        if part.get("filename"):
            files.append(str(part["filename"]))
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and not part.get("filename"):
            if mime == "text/plain":
                plain.append(_b64(data))
            elif mime == "text/html":
                html.append(_b64(data))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    text = "\n".join(plain) if plain else html_to_text("\n".join(html))
    return text.strip(), files


def build_rfc822(
    compose: ComposeIn, *, in_reply_to: str | None = None, references: str | None = None
) -> str:
    """Build the base64url RFC 822 message Gmail expects. Header injection is impossible:
    EmailMessage rejects newlines in header values, and addresses/subject were validated."""
    msg = EmailMessage()
    msg["To"] = ", ".join(compose.to)
    if compose.cc:
        msg["Cc"] = ", ".join(compose.cc)
    msg["Subject"] = compose.subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = f"{references} {in_reply_to}".strip() if references else in_reply_to
    msg.set_content(compose.body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


# ---- client -----------------------------------------------------------------------------


class GmailClient:
    def __init__(self, api: GoogleApi) -> None:
        self._api = api

    async def search(self, user_id: str, query: str, max_results: int) -> SearchOut:
        listing = await self._api.request(
            user_id,
            "GET",
            f"{BASE}/messages",
            scopes=(GMAIL_READONLY,),
            params={"q": query, "maxResults": str(max_results)},
        )
        ids = [m["id"] for m in listing.get("messages", [])]
        sem = asyncio.Semaphore(5)

        async def one(mid: str) -> EmailSummary:
            async with sem:
                data = await self._api.request(
                    user_id,
                    "GET",
                    f"{BASE}/messages/{mid}",
                    scopes=(GMAIL_READONLY,),
                    params=[
                        ("format", "metadata"),
                        ("metadataHeaders", "From"),
                        ("metadataHeaders", "Subject"),
                        ("metadataHeaders", "Date"),
                    ],
                )
            h = headers_of(data.get("payload", {}))
            labels = set(data.get("labelIds", []))
            return EmailSummary(
                id=data["id"],
                thread_id=data.get("threadId", ""),
                sender=h.get("from", ""),
                subject=h.get("subject", "(no subject)"),
                date=h.get("date", ""),
                snippet=str(data.get("snippet", ""))[:300],
                unread="UNREAD" in labels,
                important="IMPORTANT" in labels,
            )

        emails = list(await asyncio.gather(*(one(i) for i in ids)))
        return SearchOut(emails=emails, more_available="nextPageToken" in listing)

    async def read(self, user_id: str, message_id: str) -> ReadOut:
        data = await self._api.request(
            user_id,
            "GET",
            f"{BASE}/messages/{message_id}",
            scopes=(GMAIL_READONLY,),
            params={"format": "full"},
        )
        payload = data.get("payload", {})
        h = headers_of(payload)
        body, files = extract_body(payload)
        return ReadOut(
            id=data["id"],
            thread_id=data.get("threadId", ""),
            sender=h.get("from", ""),
            to=h.get("to", ""),
            cc=h.get("cc", ""),
            subject=h.get("subject", "(no subject)"),
            date=h.get("date", ""),
            body=body[:MAX_BODY_CHARS],
            attachments=files,
            truncated=len(body) > MAX_BODY_CHARS,
        )

    async def _message_payload(self, user_id: str, c: ComposeIn) -> dict[str, Any]:
        in_reply_to = references = thread_id = None
        if c.reply_to_message_id:
            orig = await self._api.request(
                user_id,
                "GET",
                f"{BASE}/messages/{c.reply_to_message_id}",
                scopes=(GMAIL_READONLY,),
                params=[
                    ("format", "metadata"),
                    ("metadataHeaders", "Message-ID"),
                    ("metadataHeaders", "References"),
                ],
            )
            h = headers_of(orig.get("payload", {}))
            in_reply_to, references = h.get("message-id"), h.get("references")
            thread_id = orig.get("threadId")
        payload: dict[str, Any] = {
            "raw": build_rfc822(c, in_reply_to=in_reply_to, references=references)
        }
        if thread_id:
            payload["threadId"] = thread_id
        return payload

    async def create_draft(self, user_id: str, c: ComposeIn) -> DraftOut:
        message = await self._message_payload(user_id, c)
        data = await self._api.request(
            user_id, "POST", f"{BASE}/drafts", scopes=(GMAIL_COMPOSE,), json={"message": message}
        )
        return DraftOut(draft_id=data["id"])

    async def send(self, user_id: str, c: ComposeIn) -> SentOut:
        message = await self._message_payload(user_id, c)
        data = await self._api.request(
            user_id, "POST", f"{BASE}/messages/send", scopes=(GMAIL_COMPOSE,), json=message
        )
        return SentOut(message_id=data["id"], thread_id=data.get("threadId", ""))


# ---- tools --------------------------------------------------------------------------------


def make_gmail_tools(client: GmailClient) -> list[AnyTool]:
    async def search(args: SearchIn, ctx: ToolContext) -> SearchOut:
        return await client.search(ctx.user_id, args.query, args.max_results)

    async def read(args: ReadIn, ctx: ToolContext) -> ReadOut:
        return await client.read(ctx.user_id, args.message_id)

    async def draft(args: ComposeIn, ctx: ToolContext) -> DraftOut:
        return await client.create_draft(ctx.user_id, args)

    async def send(args: ComposeIn, ctx: ToolContext) -> SentOut:
        return await client.send(ctx.user_id, args)

    def preview(a: ComposeIn) -> str:
        return f"to {', '.join(a.to)}: “{a.subject}”"

    return [
        ToolSpec(
            name="email.search",
            description=(
                "Search the user's Gmail. Returns sender, subject, date, snippet, unread/important "
                "flags. Use Gmail query syntax."
            ),
            input_model=SearchIn,
            handler=search,
            risk=Risk.READ,
            untrusted_output=True,
            scopes=(GMAIL_READONLY,),
            timeout_s=45,
        ),
        ToolSpec(
            name="email.read",
            description="Read one email in full (text body, recipients, attachment names) by id.",
            input_model=ReadIn,
            handler=read,
            risk=Risk.READ,
            untrusted_output=True,
            scopes=(GMAIL_READONLY,),
        ),
        ToolSpec(
            name="email.create_draft",
            description="Save an email as a draft in Gmail without sending it.",
            input_model=ComposeIn,
            handler=draft,
            risk=Risk.WRITE_LOCAL,
            scopes=(GMAIL_COMPOSE,),
            summarize=lambda a: f"Save a draft {preview(a)}",
        ),
        ToolSpec(
            name="email.send",
            description=(
                "Send an email. ALWAYS requires the user's approval; the user sees the exact "
                "recipients, subject and text. Pass reply_to_message_id to reply in-thread."
            ),
            input_model=ComposeIn,
            handler=send,
            risk=Risk.EXTERNAL,
            output_data_class=DataClass.PRIVATE,
            scopes=(GMAIL_COMPOSE,),
            summarize=lambda a: f"Send an email {preview(a)}",
        ),
    ]


__all__ = ["GmailClient", "ToolError", "make_gmail_tools"]

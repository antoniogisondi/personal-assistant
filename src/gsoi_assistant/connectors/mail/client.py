"""IMAP (read) and SMTP (send) for mailboxes other than Gmail: Tiscali, Libero, Aruba...

Reading never changes the mailbox (read-only select, BODY.PEEK: messages stay "unread").
Everything read is third-party content; sending always goes through the approval flow.
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
import re
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any

from pydantic import BaseModel

from gsoi_assistant.connectors.google.gmail import MAX_BODY_CHARS, html_to_text
from gsoi_assistant.core.errors import ToolError

TIMEOUT = 20
MAX_FETCH_BYTES = 300_000
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_SAFE_TERM = re.compile(r"^[A-Za-z0-9@._ -]{1,60}$")  # what may go into an IMAP SEARCH
_FLAGS = re.compile(rb"FLAGS \(([^)]*)\)")
_UID = re.compile(rb"UID (\d+)")


@dataclass(frozen=True)
class MailAccount:
    id: str
    label: str
    address: str
    username: str
    password: str = ""
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: str = "ssl"

    def __repr__(self) -> str:  # never print the password
        return f"MailAccount({self.label!r}, {self.address!r})"


class MailSummary(BaseModel):
    uid: str
    sender: str
    subject: str
    date: str
    unread: bool


class MailBody(BaseModel):
    uid: str
    sender: str
    to: str
    cc: str
    subject: str
    date: str
    body: str
    attachments: list[str]
    truncated: bool


ConnectImap = Callable[[MailAccount], Any]


def _tls() -> ssl.SSLContext:
    return ssl.create_default_context()


def connect_imap(account: MailAccount) -> Any:
    try:
        conn = imaplib.IMAP4_SSL(
            account.imap_host, account.imap_port, ssl_context=_tls(), timeout=TIMEOUT
        )
    except (OSError, imaplib.IMAP4.error) as exc:
        raise ToolError(
            f"Non riesco a collegarmi al server di posta {account.imap_host}: {exc}"
        ) from exc
    try:
        conn.login(account.username, account.password)
    except imaplib.IMAP4.error as exc:
        _quiet_logout(conn)
        raise ToolError(
            "Accesso rifiutato dal server di posta: controlla indirizzo e password "
            "(se l'account ha la verifica in due passaggi serve una password per app)."
        ) from exc
    return conn


def _quiet_logout(conn: Any) -> None:
    with contextlib.suppress(Exception):  # the connection may be gone already
        conn.logout()


def _header_text(msg: Any, name: str) -> str:
    value = msg.get(name)
    return " ".join(str(value).split()) if value else ""


def _imap_date(days_ago: int) -> str:
    d = datetime.now(UTC) - timedelta(days=days_ago)
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


def search_criteria(
    unread_only: bool, from_contains: str | None, subject_contains: str | None, since_days: int
) -> list[str]:
    criteria = ["SINCE", _imap_date(since_days)]
    if unread_only:
        criteria.append("UNSEEN")
    for key, term in (("FROM", from_contains), ("SUBJECT", subject_contains)):
        if term:
            if not _SAFE_TERM.match(term):
                raise ToolError("Use only letters, digits, spaces and . _ - @ in the search words.")
            criteria += [key, f'"{term}"']
    return criteria


def _search(conn: Any, criteria: list[str], limit: int) -> list[MailSummary]:
    typ, _ = conn.select("INBOX", readonly=True)
    if typ != "OK":
        raise ToolError("Non riesco ad aprire la casella di posta in arrivo.")
    typ, data = conn.uid("SEARCH", *criteria)
    if typ != "OK" or not data or not data[0]:
        return []
    uids = data[0].split()[-limit:]
    typ, rows = conn.uid(
        "FETCH", b",".join(uids), "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
    )
    if typ != "OK":
        return []
    found: list[MailSummary] = []
    for row in rows:
        if not isinstance(row, tuple):
            continue
        meta, raw = row[0], row[1]
        uid = _UID.search(meta)
        if uid is None:
            continue
        flags = _FLAGS.search(meta)
        msg = message_from_bytes(raw, policy=policy.default)
        found.append(
            MailSummary(
                uid=uid.group(1).decode(),
                sender=_header_text(msg, "From"),
                subject=_header_text(msg, "Subject") or "(senza oggetto)",
                date=_header_text(msg, "Date"),
                unread=b"\\Seen" not in (flags.group(1) if flags else b""),
            )
        )
    found.sort(key=lambda m: int(m.uid), reverse=True)  # newest first
    return found


def _read(conn: Any, uid: str) -> MailBody:
    typ, _ = conn.select("INBOX", readonly=True)
    if typ != "OK":
        raise ToolError("Non riesco ad aprire la casella di posta in arrivo.")
    typ, rows = conn.uid("FETCH", uid, f"(BODY.PEEK[]<0.{MAX_FETCH_BYTES}>)")
    raw = next((r[1] for r in rows or [] if isinstance(r, tuple)), None)
    if typ != "OK" or raw is None:
        raise ToolError("Messaggio non trovato.")
    msg = message_from_bytes(raw, policy=policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if part is not None:
        try:
            content = str(part.get_content())
        except (LookupError, UnicodeDecodeError):
            content = ""
        text = html_to_text(content) if part.get_content_type() == "text/html" else content.strip()
    files = [a.get_filename() or "(senza nome)" for a in msg.iter_attachments()]
    return MailBody(
        uid=uid,
        sender=_header_text(msg, "From"),
        to=_header_text(msg, "To"),
        cc=_header_text(msg, "Cc"),
        subject=_header_text(msg, "Subject") or "(senza oggetto)",
        date=_header_text(msg, "Date"),
        body=text[:MAX_BODY_CHARS],
        attachments=files,
        truncated=len(text) > MAX_BODY_CHARS,
    )


def build_message(
    account: MailAccount, to: list[str], cc: list[str], subject: str, body: str
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((account.label, account.address))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg["Message-ID"] = make_msgid(domain=account.address.rsplit("@", 1)[-1])
    msg.set_content(body)
    return msg


def _smtp_connect(host: str, port: int, security: str) -> smtplib.SMTP:
    if security == "ssl":
        return smtplib.SMTP_SSL(host, port, context=_tls(), timeout=TIMEOUT)
    server = smtplib.SMTP(host, port, timeout=TIMEOUT)
    try:
        server.starttls(context=_tls())
    except Exception:
        server.close()
        raise
    return server


def _smtp_candidates(account: MailAccount) -> list[tuple[int, str]]:
    """The configured way first, then the two standard ones (providers differ and change)."""
    first = (account.smtp_port, account.smtp_security)
    return [first, *[c for c in ((465, "ssl"), (587, "starttls")) if c != first]]


def smtp_open(account: MailAccount) -> tuple[smtplib.SMTP, int, str]:
    """Connect and log in to the outgoing server. Returns the connection and the port and
    security that worked. A rejected password stops at once: other ports would reject it too."""
    problems: list[str] = []
    for port, security in _smtp_candidates(account):
        label = f"{port} ({'SSL' if security == 'ssl' else 'STARTTLS'})"
        try:
            server = _smtp_connect(account.smtp_host, port, security)
        except (OSError, smtplib.SMTPException) as exc:  # includes ssl.SSLError
            problems.append(f"{label}: {_short(exc)}")
            continue
        try:
            server.login(account.username, account.password)
        except smtplib.SMTPAuthenticationError as exc:
            server.close()
            raise ToolError(
                "Il server di invio ha rifiutato l'accesso: controlla indirizzo e password."
            ) from exc
        except (OSError, smtplib.SMTPException) as exc:
            server.close()
            problems.append(f"{label}: {_short(exc)}")
            continue
        return server, port, security
    hint = ""
    if any(
        m in p for p in problems for m in ("HANDSHAKE_FAILURE", "UNSUPPORTED_PROTOCOL", "VERSION")
    ):
        hint = (
            " Il server sembra usare un protocollo di sicurezza datato (TLS 1.0/1.1) che l'app "
            "non accetta per la tua sicurezza: controlla sul sito del provider i server di invio."
        )
    raise ToolError(
        f"Non riesco a collegarmi al server di invio {account.smtp_host}. "
        "Provato: " + "; ".join(problems) + "." + hint
    )


def _short(exc: Exception) -> str:
    return " ".join(str(exc).split())[:120]


def smtp_login(account: MailAccount) -> smtplib.SMTP:
    return smtp_open(account)[0]


class MailClient:
    """All blocking network calls run in a worker thread."""

    def __init__(self, connect: ConnectImap = connect_imap) -> None:
        self._connect = connect

    async def search(
        self,
        account: MailAccount,
        *,
        unread_only: bool = True,
        from_contains: str | None = None,
        subject_contains: str | None = None,
        since_days: int = 7,
        limit: int = 10,
    ) -> list[MailSummary]:
        criteria = search_criteria(unread_only, from_contains, subject_contains, since_days)

        def work() -> list[MailSummary]:
            conn = self._connect(account)
            try:
                return _search(conn, criteria, limit)
            except imaplib.IMAP4.error as exc:
                raise ToolError(f"Errore del server di posta: {exc}") from exc
            finally:
                _quiet_logout(conn)

        return await asyncio.to_thread(work)

    async def read(self, account: MailAccount, uid: str) -> MailBody:
        def work() -> MailBody:
            conn = self._connect(account)
            try:
                return _read(conn, uid)
            except imaplib.IMAP4.error as exc:
                raise ToolError(f"Errore del server di posta: {exc}") from exc
            finally:
                _quiet_logout(conn)

        return await asyncio.to_thread(work)

    async def send(
        self, account: MailAccount, to: list[str], cc: list[str], subject: str, body: str
    ) -> str:
        message = build_message(account, to, cc, subject, body)

        def work() -> str:
            server = smtp_login(account)
            try:
                server.send_message(message, to_addrs=[*to, *cc])
            except smtplib.SMTPException as exc:
                raise ToolError(f"Il server ha rifiutato il messaggio: {exc}") from exc
            finally:
                try:
                    server.quit()
                except smtplib.SMTPException:
                    server.close()
            return str(message["Message-ID"])

        return await asyncio.to_thread(work)

    async def test(self, account: MailAccount) -> tuple[int, str]:
        """Log in to both servers (raises ToolError with a readable reason). Returns the port and
        security of the outgoing server that actually worked."""

        def work() -> tuple[int, str]:
            _quiet_logout(self._connect(account))
            server, port, security = smtp_open(account)
            server.close()
            return port, security

        return await asyncio.to_thread(work)

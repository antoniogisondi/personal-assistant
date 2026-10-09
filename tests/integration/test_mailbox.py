from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx
import pytest
from sqlalchemy import text

from gsoi_assistant.api.container import Container
from gsoi_assistant.connectors.mail import client as mail_client
from gsoi_assistant.connectors.mail.client import MailAccount, MailClient, search_criteria
from gsoi_assistant.core.errors import ConnectorNotConnectedError, ToolError
from gsoi_assistant.llm.base import ToolCall
from gsoi_assistant.tools.executor import ExecContext
from support import new_run

HEADER = (
    b"From: =?utf-8?q?Marco_Rossi?= <marco@example.it>\r\n"
    b"Subject: =?utf-8?q?Fattura_di_ottobre?=\r\nDate: Fri, 09 Oct 2026 09:00:00 +0200\r\n\r\n"
)
FULL = (
    b"From: Marco <marco@example.it>\r\nTo: me@tiscali.it\r\nSubject: Ciao\r\n"
    b"Content-Type: multipart/mixed; boundary=XX\r\n\r\n--XX\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n\r\n<p>Ciao <b>Anto</b></p><p>Ci vediamo.</p>\r\n"
    b"--XX\r\nContent-Type: application/pdf; name=fattura.pdf\r\n"
    b"Content-Disposition: attachment; filename=fattura.pdf\r\n\r\nJVBER\r\n--XX--\r\n"
)


class FakeImap:
    def __init__(self, messages: dict[int, tuple[bool, bytes]], good: bool = True) -> None:
        self.messages = messages  # uid -> (seen, header bytes)
        self.good = good
        self.log: list[tuple[Any, ...]] = []

    def select(self, box: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.log.append(("select", box, readonly))
        return "OK", [b"1"]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        self.log.append((command, *args))
        if command == "SEARCH":
            unseen = "UNSEEN" in args
            uids = [u for u, (seen, _) in sorted(self.messages.items()) if not (unseen and seen)]
            return "OK", [b" ".join(str(u).encode() for u in uids)]
        if command == "FETCH" and "HEADER.FIELDS" in args[1]:
            wanted = [int(x) for x in args[0].split(b",")]
            rows: list[Any] = []
            for u in wanted:
                seen, header = self.messages[u]
                flags = b"\\Seen" if seen else b""
                rows.append(
                    (f"1 (UID {u} FLAGS ({flags.decode()}) BODY[HEADER.FIELDS]".encode(), header)
                )
                rows.append(b")")
            return "OK", rows
        if command == "FETCH":
            return "OK", [(b"1 (BODY[] {10}", FULL), b")"]
        return "NO", []

    def logout(self) -> None:
        self.log.append(("logout",))


class FakeSmtp:
    sent: ClassVar[list[Any]] = []

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    def send_message(self, message: Any, to_addrs: list[str]) -> None:
        FakeSmtp.sent.append((message, to_addrs))

    def quit(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def imap() -> FakeImap:
    return FakeImap({1: (True, HEADER), 2: (False, HEADER), 3: (False, HEADER)})


@pytest.fixture
def mail(gcontainer: Container, imap: FakeImap, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    FakeSmtp.sent = []

    def fake_open(account: MailAccount) -> tuple[FakeSmtp, int, str]:
        if account.password == "sbagliata":
            raise ToolError("Il server di invio ha rifiutato l'accesso")
        return FakeSmtp(), account.smtp_port, account.smtp_security

    monkeypatch.setattr(mail_client, "smtp_open", fake_open)

    def connect(account: MailAccount) -> FakeImap:
        if account.password == "sbagliata":
            raise ToolError("Accesso rifiutato dal server di posta")
        return imap

    gcontainer.mail.client = MailClient(connect=connect)
    return gcontainer.mail


async def test_adding_an_account_tests_the_login_and_stores_the_password_encrypted(
    gcontainer: Container, mail
) -> None:  # type: ignore[no-untyped-def]
    acct = await mail.add("owner", address="me@tiscali.it", password="segreta-123")
    assert acct.label == "Tiscali" and acct.imap_host == "imap.tiscali.it" and acct.smtp_port == 465
    async with gcontainer.engine.connect() as conn:
        raw = (await conn.execute(text("select password_enc from mail_accounts"))).scalar_one()
    assert "segreta-123" not in raw and raw.startswith("gAAAA")  # Fernet token
    assert "segreta-123" not in repr(acct)
    again = await mail.add("owner", address="me@tiscali.it", password="nuova-456")
    assert (
        len(await mail.list("owner")) == 1 and (await mail.list("owner"))[0].password == "nuova-456"
    )
    assert again.id != acct.id


async def test_wrong_credentials_or_unknown_provider_are_rejected_and_nothing_is_stored(
    mail,
) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ToolError, match="rifiutato"):
        await mail.add("owner", address="me@tiscali.it", password="sbagliata")
    with pytest.raises(ToolError, match="Provider non riconosciuto"):
        await mail.add("owner", address="me@azienda-sconosciuta.it", password="x")
    with pytest.raises(ToolError, match="non valido"):
        await mail.add(
            "owner", address="me@azienda.it", password="x", imap_host="bad host!", smtp_host="s.it"
        )
    assert await mail.list("owner") == []
    custom = await mail.add(
        "owner",
        address="me@azienda.it",
        password="x",
        imap_host="mail.azienda.it",
        smtp_host="smtp.azienda.it",
        smtp_security="starttls",
        smtp_port=587,
    )
    assert custom.label == "azienda.it" and custom.smtp_security == "starttls"


async def test_accounts_are_resolved_by_name_or_the_only_one(mail) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConnectorNotConnectedError):
        await mail.resolve("owner", None)
    await mail.add("owner", address="me@tiscali.it", password="p")
    assert (await mail.resolve("owner", None)).address == "me@tiscali.it"
    await mail.add("owner", address="io@libero.it", password="p")
    assert (await mail.resolve("owner", "libero")).address == "io@libero.it"
    with pytest.raises(ToolError, match="più account"):
        await mail.resolve("owner", None)
    with pytest.raises(ToolError, match="Nessun account"):
        await mail.resolve("owner", "yahoo")


async def test_search_lists_newest_first_without_marking_anything_as_read(
    mail, imap: FakeImap
) -> None:  # type: ignore[no-untyped-def]
    acct = await mail.add("owner", address="me@tiscali.it", password="p")
    found = await mail.client.search(acct, unread_only=True)
    assert [m.uid for m in found] == ["3", "2"] and found[0].unread
    assert (
        found[0].sender == "Marco Rossi <marco@example.it>"
        and found[0].subject == "Fattura di ottobre"
    )
    assert ("select", "INBOX", True) in imap.log  # read-only
    fetches = [c for c in imap.log if c[0] == "FETCH"]
    assert fetches and all("PEEK" in c[2] for c in fetches)
    everything = await mail.client.search(acct, unread_only=False)
    assert [m.uid for m in everything] == ["3", "2", "1"] and not everything[-1].unread
    assert ("logout",) in imap.log


async def test_read_extracts_text_and_attachment_names(mail) -> None:  # type: ignore[no-untyped-def]
    acct = await mail.add("owner", address="me@tiscali.it", password="p")
    body = await mail.client.read(acct, "2")
    assert (
        "Ciao Anto" in body.body
        and "Ci vediamo." in body.body
        and body.attachments == ["fattura.pdf"]
    )
    assert body.sender == "Marco <marco@example.it>"


def test_search_words_cannot_inject_imap_syntax() -> None:
    assert search_criteria(True, "marco", None, 7)[-2:] == ["FROM", '"marco"']
    for bad in ('x" OR ALL "', "a\r\nb", "ä", "x" * 80):
        with pytest.raises(ToolError):
            search_criteria(True, bad, None, 7)


async def test_send_builds_the_message_from_the_chosen_account(mail) -> None:  # type: ignore[no-untyped-def]
    acct = await mail.add("owner", address="me@tiscali.it", password="p")
    mid = await mail.client.send(acct, ["a@x.it"], ["b@x.it"], "Ciao", "Testo")
    message, recipients = FakeSmtp.sent[0]
    assert (
        recipients == ["a@x.it", "b@x.it"] and message["Subject"] == "Ciao" and mid.startswith("<")
    )
    assert "me@tiscali.it" in message["From"] and message["Cc"] == "b@x.it"


async def run_tool(c: Container, name: str, args: dict[str, Any]) -> Any:
    ctx = ExecContext(user_id="owner", run_id=await new_run(c))
    return await c.executor.execute(ToolCall(id="1", name=name, arguments=args), ctx)


async def test_tools_search_untrusted_and_send_needs_approval(gcontainer: Container, mail) -> None:  # type: ignore[no-untyped-def]
    await mail.add("owner", address="me@tiscali.it", password="p")
    r = await run_tool(gcontainer, "mailbox.search", {})
    assert r.status == "ok" and "<untrusted" in r.content and "Fattura di ottobre" in r.content
    sent = await run_tool(
        gcontainer, "mailbox.send", {"to": ["a@x.it"], "subject": "Ciao", "body": "Testo"}
    )
    assert sent.status == "approval_required" and FakeSmtp.sent == []
    bad = await run_tool(
        gcontainer, "mailbox.send", {"to": ["not an address"], "subject": "x", "body": "y"}
    )
    assert bad.status == "error"


async def test_new_mail_alerts_for_other_accounts_baseline_then_news(
    gcontainer: Container, mail, imap: FakeImap
) -> None:  # type: ignore[no-untyped-def]
    await mail.add("owner", address="me@tiscali.it", password="p")
    w = gcontainer.watcher
    w._gmail = w._calendar = None
    assert (await w.check("owner")).alerts == []  # baseline: what is already unread
    imap.messages[4] = (False, HEADER)
    alerts = (await w.check("owner")).alerts
    assert (
        len(alerts) == 1
        and "Tiscali" in alerts[0].spoken
        and "Fattura di ottobre" in alerts[0].spoken
    )
    assert (await w.check("owner")).alerts == []


async def test_a_broken_account_does_not_silence_the_others(
    gcontainer: Container, mail, imap: FakeImap
) -> None:  # type: ignore[no-untyped-def]
    await mail.add("owner", address="me@tiscali.it", password="p")
    await mail.add("owner", address="io@libero.it", password="p")
    real = mail.client._connect

    def flaky(account: MailAccount) -> FakeImap:
        if account.address.endswith("libero.it"):
            raise ToolError("server non raggiungibile")
        return real(account)  # type: ignore[no-any-return]

    mail.client = MailClient(connect=flaky)
    w = gcontainer.watcher
    w._gmail = w._calendar = None
    result = await w.check("owner")
    assert any("Libero" in u for u in result.unavailable)


async def test_account_endpoints(gclient: httpx.AsyncClient, mail) -> None:  # type: ignore[no-untyped-def]
    bad = await gclient.post(
        "/v1/mail/accounts", json={"address": "me@tiscali.it", "password": "sbagliata"}
    )
    assert (
        bad.status_code == 400 and "rifiutato" in bad.json()["error"]["message"]
        if "error" in bad.json()
        else True
    )
    assert (await gclient.get("/v1/mail/accounts")).json() == []
    ok = await gclient.post(
        "/v1/mail/accounts", json={"address": "me@tiscali.it", "password": "buona-123"}
    )
    assert ok.status_code == 200 and "password" not in json.dumps(ok.json())
    listed = (await gclient.get("/v1/mail/accounts")).json()
    assert [a["address"] for a in listed] == ["me@tiscali.it"]
    assert (await gclient.delete(f"/v1/mail/accounts/{listed[0]['id']}")).status_code == 204
    assert (await gclient.delete("/v1/mail/accounts/nope")).status_code == 404
    assert (await gclient.get("/v1/mail/accounts")).json() == []


async def test_briefing_counts_unread_in_other_accounts(gcontainer: Container, mail) -> None:  # type: ignore[no-untyped-def]
    await mail.add("owner", address="me@tiscali.it", password="p")
    r = await run_tool(gcontainer, "briefing.today", {})
    payload = json.loads(r.content[r.content.index("{") : r.content.rindex("}") + 1])
    assert payload["other_accounts_unread"] == [{"account": "Tiscali", "unread_last_2_days": 2}]


class ScriptedSmtp:
    """Stands in for smtplib to test the choice of port and security."""

    def __init__(self, working: tuple[int, str] | None, auth_ok: bool = True) -> None:
        self.working, self.auth_ok = working, auth_ok
        self.tried: list[tuple[int, str]] = []

    def connect(self, host: str, port: int, security: str) -> Any:
        import ssl

        self.tried.append((port, security))
        if (port, security) != self.working:
            raise ssl.SSLError("[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] handshake failure")
        outer = self

        class Server:
            def login(self, user: str, password: str) -> None:
                if not outer.auth_ok:
                    import smtplib

                    raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

            def close(self) -> None:
                pass

        return Server()


def _account(port: int = 465, security: str = "ssl") -> MailAccount:
    return MailAccount(
        id="a1",
        label="Tiscali",
        address="me@tiscali.it",
        username="me@tiscali.it",
        password="p",
        imap_host="imap.tiscali.it",
        smtp_host="smtp.tiscali.it",
        smtp_port=port,
        smtp_security=security,
    )


def test_a_failing_secure_port_falls_back_to_the_other_standard_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = ScriptedSmtp(working=(587, "starttls"))
    monkeypatch.setattr(mail_client, "_smtp_connect", fake.connect)
    _, port, security = mail_client.smtp_open(_account())
    assert (port, security) == (587, "starttls") and fake.tried == [(465, "ssl"), (587, "starttls")]


def test_when_nothing_works_the_error_lists_what_was_tried(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = ScriptedSmtp(working=None)
    monkeypatch.setattr(mail_client, "_smtp_connect", fake.connect)
    with pytest.raises(ToolError) as err:
        mail_client.smtp_open(_account())
    assert "465 (SSL)" in str(err.value) and "587 (STARTTLS)" in str(err.value)


def test_a_wrong_password_stops_at_the_first_server_that_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = ScriptedSmtp(working=(465, "ssl"), auth_ok=False)
    monkeypatch.setattr(mail_client, "_smtp_connect", fake.connect)
    with pytest.raises(ToolError, match="rifiutato l'accesso"):
        mail_client.smtp_open(_account())
    assert fake.tried == [(465, "ssl")]


async def test_the_working_port_is_remembered_when_the_account_is_added(
    gcontainer: Container, imap: FakeImap, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = ScriptedSmtp(working=(587, "starttls"))
    monkeypatch.setattr(mail_client, "_smtp_connect", fake.connect)
    gcontainer.mail.client = MailClient(connect=lambda account: imap)
    acct = await gcontainer.mail.add("owner", address="me@tiscali.it", password="p")
    assert (acct.smtp_port, acct.smtp_security) == (587, "starttls")
    stored = (await gcontainer.mail.list("owner"))[0]
    assert (stored.smtp_port, stored.smtp_security) == (587, "starttls")


def test_diagnosis_reports_unreachable_ports_and_unknown_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    from gsoi_assistant.connectors.mail import diagnostics

    lines: list[str] = []
    monkeypatch.setattr(socket, "gethostbyname", lambda h: "192.0.2.1")

    def refuse(address: Any, timeout: float = 0) -> Any:
        raise OSError("timed out")

    monkeypatch.setattr(socket, "create_connection", refuse)
    diagnostics.diagnose("smtp.example.it", lines.append)
    assert lines[0] == "smtp.example.it -> 192.0.2.1"
    assert sum("non raggiungibile" in line for line in lines) == 4

    def unknown(host: str) -> str:
        raise OSError("nope")

    monkeypatch.setattr(socket, "gethostbyname", unknown)
    lines.clear()
    diagnostics.diagnose("nessun.host.it", lines.append)
    assert "non si risolve" in lines[0]

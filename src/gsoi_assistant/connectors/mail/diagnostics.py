"""`gsoi-desktop --mail-diag HOST`: what does an outgoing mail server accept?

Used when adding a mailbox fails with a connection or TLS error. Nothing is sent and no password
is used: it only opens connections and reports which ports answer and which TLS versions the
server negotiates, so the cause (closed port, outdated TLS...) is known instead of guessed.
"""

from __future__ import annotations

import smtplib
import socket
import ssl
import warnings
from collections.abc import Callable

PORTS = ((465, "ssl"), (587, "starttls"), (25, "starttls"), (2525, "starttls"))
Say = Callable[[str], None]


def _context(minimum: ssl.TLSVersion, maximum: ssl.TLSVersion, relaxed: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version, ctx.maximum_version = minimum, maximum
    if relaxed:  # diagnosis only: lets an outdated server show what it really speaks
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    return ctx


def _tries() -> list[tuple[str, ssl.SSLContext]]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # TLS 1.0/1.1 are probed on purpose
        return [
            ("TLS 1.3/1.2", _context(ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3, False)),
            (
                "TLS 1.1/1.0",
                _context(ssl.TLSVersion.MINIMUM_SUPPORTED, ssl.TLSVersion.TLSv1_1, True),
            ),
        ]


def _try(host: str, port: int, security: str, ctx: ssl.SSLContext) -> str:
    try:
        if security == "ssl":
            with (
                socket.create_connection((host, port), timeout=6) as raw,
                ctx.wrap_socket(raw, server_hostname=host) as tls,
            ):
                cipher = tls.cipher()
                return f"ok, {tls.version()} {cipher[0] if cipher else ''}"
        with smtplib.SMTP(host, port, timeout=6) as server:
            server.starttls(context=ctx)
            sock = server.sock
            version = sock.version() if isinstance(sock, ssl.SSLSocket) else "?"
            return f"ok, {version}"
    except (OSError, smtplib.SMTPException) as exc:
        return " ".join(str(exc).split())[:110]


def diagnose(host: str, say: Say = print) -> None:
    try:
        say(f"{host} -> {socket.gethostbyname(host)}")
    except OSError as exc:
        say(f"Il nome {host} non si risolve: {exc}")
        return
    for port, security in PORTS:
        try:
            socket.create_connection((host, port), timeout=6).close()
        except OSError as exc:
            say(f"porta {port}: non raggiungibile ({exc})")
            continue
        say(f"porta {port} ({security}): raggiungibile")
        for name, ctx in _tries():
            say(f"    {name}: {_try(host, port, security, ctx)}")

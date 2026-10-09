"""Server settings of common providers, so a user only types an address and a password."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    name: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    smtp_security: str  # "ssl" (port 465) or "starttls" (port 587)
    note: str = ""


_YAHOO = "Serve una «password per app» creata dalle impostazioni di sicurezza Yahoo."
PRESETS: dict[str, Preset] = {
    "tiscali.it": Preset("Tiscali", "imap.tiscali.it", 993, "smtp.tiscali.it", 465, "ssl"),
    "libero.it": Preset("Libero", "imapmail.libero.it", 993, "smtp.libero.it", 465, "ssl"),
    "virgilio.it": Preset("Virgilio", "in.virgilio.it", 993, "out.virgilio.it", 465, "ssl"),
    "aruba.it": Preset("Aruba", "imaps.aruba.it", 993, "smtps.aruba.it", 465, "ssl"),
    "yahoo.com": Preset("Yahoo", "imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465, "ssl",
                        _YAHOO),
    "yahoo.it": Preset("Yahoo", "imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465, "ssl",
                       _YAHOO),
    "icloud.com": Preset("iCloud", "imap.mail.me.com", 993, "smtp.mail.me.com", 587, "starttls",
                         "Serve una «password specifica per app» (appleid.apple.com)."),
}  # fmt: skip


def preset_for(address: str) -> Preset | None:
    domain = address.rsplit("@", 1)[-1].strip().lower()
    return PRESETS.get(domain)

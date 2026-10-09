"""The user's IMAP/SMTP accounts: stored encrypted, resolved by name for the tools."""

from __future__ import annotations

import re
import uuid
from dataclasses import replace

from gsoi_assistant.connectors.mail.client import MailAccount, MailClient
from gsoi_assistant.connectors.mail.presets import preset_for
from gsoi_assistant.core.errors import ConnectorNotConnectedError, ToolError
from gsoi_assistant.db import models
from gsoi_assistant.db.stores import MailAccountStore
from gsoi_assistant.security.crypto import TokenCipher

_ADDRESS = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"']+$")
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


class MailAccounts:
    def __init__(self, store: MailAccountStore, cipher: TokenCipher, client: MailClient) -> None:
        self._store = store
        self._cipher = cipher
        self.client = client

    def _account(self, row: models.MailAccount) -> MailAccount:
        password = str(self._cipher.decrypt_json(row.password_enc).get("password", ""))
        return MailAccount(
            id=row.id,
            label=row.label,
            address=row.address,
            username=row.username,
            password=password,
            imap_host=row.imap_host,
            imap_port=row.imap_port,
            smtp_host=row.smtp_host,
            smtp_port=row.smtp_port,
            smtp_security=row.smtp_security,
        )

    async def list(self, user_id: str) -> list[MailAccount]:
        return [self._account(r) for r in await self._store.list(user_id)]

    async def resolve(self, user_id: str, ref: str | None) -> MailAccount:
        """The account the user means: by label or address, or the only one there is."""
        accounts = await self.list(user_id)
        if not accounts:
            raise ConnectorNotConnectedError(
                "Nessun account email aggiunto: l'utente può aggiungerlo da Servizi."
            )
        if ref:
            wanted = ref.strip().lower()
            for a in accounts:
                if wanted in (a.label.lower(), a.address.lower(), a.id):
                    return a
            matches = [a for a in accounts if wanted in a.label.lower() or wanted in a.address]
            if len(matches) == 1:
                return matches[0]
            names = ", ".join(a.label for a in accounts)
            raise ToolError(f"Nessun account '{ref}'. Account disponibili: {names}.")
        if len(accounts) == 1:
            return accounts[0]
        names = ", ".join(f"{a.label} ({a.address})" for a in accounts)
        raise ToolError(f"Ci sono più account: {names}. Chiedi all'utente quale usare.")

    async def add(
        self,
        user_id: str,
        *,
        address: str,
        password: str,
        label: str | None = None,
        imap_host: str | None = None,
        smtp_host: str | None = None,
        imap_port: int | None = None,
        smtp_port: int | None = None,
        smtp_security: str | None = None,
    ) -> MailAccount:
        address = address.strip()
        if not _ADDRESS.match(address):
            raise ToolError("Indirizzo email non valido.")
        preset = preset_for(address)
        imap_host = imap_host or (preset.imap_host if preset else None)
        smtp_host = smtp_host or (preset.smtp_host if preset else None)
        if not imap_host or not smtp_host:
            raise ToolError(
                "Provider non riconosciuto: indica i server IMAP e SMTP del tuo provider."
            )
        for host in (imap_host, smtp_host):
            if not _HOST.match(host):
                raise ToolError(f"Nome del server non valido: {host}")
        security = smtp_security or (preset.smtp_security if preset else "ssl")
        if security not in ("ssl", "starttls"):
            raise ToolError("Sicurezza dell'invio non valida (ssl o starttls).")
        account = MailAccount(
            id=uuid.uuid4().hex[:10],
            label=(label or (preset.name if preset else address.split("@")[1])).strip()[:60],
            address=address,
            username=address,
            password=password,
            imap_host=imap_host,
            imap_port=imap_port or (preset.imap_port if preset else 993),
            smtp_host=smtp_host,
            smtp_port=smtp_port or (preset.smtp_port if preset else 465),
            smtp_security=security,
        )
        port, security_used = await self.client.test(account)  # never store what does not work
        account = replace(account, smtp_port=port, smtp_security=security_used)
        await self._store.save(
            models.MailAccount(
                id=account.id,
                user_id=user_id,
                label=account.label,
                address=account.address,
                username=account.username,
                imap_host=account.imap_host,
                imap_port=account.imap_port,
                smtp_host=account.smtp_host,
                smtp_port=account.smtp_port,
                smtp_security=account.smtp_security,
                password_enc=self._cipher.encrypt_json({"password": password}),
            )
        )
        return account

    async def remove(self, user_id: str, account_id: str) -> bool:
        return await self._store.delete(user_id, account_id)

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from gsoi_assistant.api.deps import ContainerDep, UserDep
from gsoi_assistant.api.schemas import MailAccountBody, MailAccountOut
from gsoi_assistant.connectors.mail.client import MailAccount
from gsoi_assistant.core.errors import ToolError

router = APIRouter(prefix="/v1/mail", tags=["mail"])


def _out(a: MailAccount) -> MailAccountOut:
    return MailAccountOut(
        id=a.id, label=a.label, address=a.address, imap_host=a.imap_host, smtp_host=a.smtp_host
    )


@router.get("/accounts", response_model=list[MailAccountOut])
async def list_accounts(container: ContainerDep, user_id: UserDep) -> list[MailAccountOut]:
    return [_out(a) for a in await container.mail.list(user_id)]


@router.post("/accounts", response_model=MailAccountOut)
async def add_account(
    body: MailAccountBody, container: ContainerDep, user_id: UserDep
) -> MailAccountOut:
    """Add an account. The login is tested first: wrong credentials are never stored."""
    try:
        account = await container.mail.add(
            user_id,
            address=body.address,
            password=body.password.get_secret_value(),
            label=body.label,
            imap_host=body.imap_host,
            smtp_host=body.smtp_host,
            imap_port=body.imap_port,
            smtp_port=body.smtp_port,
            smtp_security=body.smtp_security,
        )
    except ToolError as exc:
        raise HTTPException(400, str(exc)) from exc
    await container.audit.record(
        user_id=user_id, actor="user", action="mail.account_added", subject=account.address
    )
    return _out(account)


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(account_id: str, container: ContainerDep, user_id: UserDep) -> None:
    if not await container.mail.remove(user_id, account_id):
        raise HTTPException(404, "account not found")
    await container.audit.record(
        user_id=user_id, actor="user", action="mail.account_removed", subject=account_id
    )

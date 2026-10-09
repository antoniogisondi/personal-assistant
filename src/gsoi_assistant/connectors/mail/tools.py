"""mailbox.*: other mailboxes (Tiscali, Libero...). Gmail has its own email.* tools."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from gsoi_assistant.connectors.google.gmail import _check_addresses
from gsoi_assistant.connectors.mail.accounts import MailAccounts
from gsoi_assistant.connectors.mail.client import MailBody, MailSummary
from gsoi_assistant.core.types import DataClass, Risk
from gsoi_assistant.tools.base import ToolContext, ToolSpec
from gsoi_assistant.tools.registry import AnyTool

_ACCOUNT = Field(
    default=None,
    max_length=254,
    description="Account name or address. Optional when there is only one account.",
)


class NoArgs(BaseModel):
    pass


class AccountInfo(BaseModel):
    label: str
    address: str


class AccountsOut(BaseModel):
    accounts: list[AccountInfo]


class SearchIn(BaseModel):
    account: str | None = _ACCOUNT
    unread_only: bool = True
    from_contains: str | None = Field(default=None, max_length=60)
    subject_contains: str | None = Field(default=None, max_length=60)
    since_days: int = Field(default=7, ge=1, le=60)
    max_results: int = Field(default=10, ge=1, le=25)


class SearchOut(BaseModel):
    account: str
    emails: list[MailSummary]


class ReadIn(BaseModel):
    account: str | None = _ACCOUNT
    uid: str = Field(pattern=r"^\d{1,12}$", description="The uid returned by mailbox.search.")


class SendIn(BaseModel):
    account: str | None = _ACCOUNT
    to: list[str] = Field(min_length=1, max_length=10)
    cc: list[str] = Field(default_factory=list, max_length=10)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)

    @field_validator("to", "cc")
    @classmethod
    def _addresses(cls, v: list[str]) -> list[str]:
        return _check_addresses(v)

    @field_validator("subject")
    @classmethod
    def _one_line(cls, v: str) -> str:
        if "\n" in v or "\r" in v:
            raise ValueError("subject must be a single line")
        return v.strip()


class SentOut(BaseModel):
    account: str
    message_id: str


def make_mail_tools(accounts: MailAccounts) -> list[AnyTool]:
    async def list_accounts(_: NoArgs, ctx: ToolContext) -> AccountsOut:
        found = await accounts.list(ctx.user_id)
        return AccountsOut(accounts=[AccountInfo(label=a.label, address=a.address) for a in found])

    async def search(args: SearchIn, ctx: ToolContext) -> SearchOut:
        acct = await accounts.resolve(ctx.user_id, args.account)
        emails = await accounts.client.search(
            acct,
            unread_only=args.unread_only,
            from_contains=args.from_contains,
            subject_contains=args.subject_contains,
            since_days=args.since_days,
            limit=args.max_results,
        )
        return SearchOut(account=acct.label, emails=emails)

    async def read(args: ReadIn, ctx: ToolContext) -> MailBody:
        acct = await accounts.resolve(ctx.user_id, args.account)
        return await accounts.client.read(acct, args.uid)

    async def send(args: SendIn, ctx: ToolContext) -> SentOut:
        acct = await accounts.resolve(ctx.user_id, args.account)
        message_id = await accounts.client.send(acct, args.to, args.cc, args.subject, args.body)
        return SentOut(account=acct.label, message_id=message_id)

    return [
        ToolSpec(
            name="mailbox.accounts",
            description="List the user's other email accounts (not Gmail), e.g. Tiscali.",
            input_model=NoArgs,
            handler=list_accounts,
            risk=Risk.READ,
        ),
        ToolSpec(
            name="mailbox.search",
            description=(
                "Search the inbox of one of the user's other email accounts (Tiscali, Libero...). "
                "Gmail is searched with email.search instead. Reading does not mark mail as read."
            ),
            input_model=SearchIn,
            handler=search,
            risk=Risk.READ,
            untrusted_output=True,
            timeout_s=45,
        ),
        ToolSpec(
            name="mailbox.read",
            description="Read one email in full from another email account, by uid.",
            input_model=ReadIn,
            handler=read,
            risk=Risk.READ,
            untrusted_output=True,
            timeout_s=45,
        ),
        ToolSpec(
            name="mailbox.send",
            description=(
                "Send an email from one of the user's other accounts (Tiscali, Libero...). ALWAYS "
                "requires the user's approval; the user sees the account, recipients, subject and "
                "text. For Gmail use email.send."
            ),
            input_model=SendIn,
            handler=send,
            risk=Risk.EXTERNAL,
            output_data_class=DataClass.PRIVATE,
            timeout_s=60,
            summarize=lambda a: (
                f"Send an email from {a.account or 'the account'} to {', '.join(a.to)}: "
                f"“{a.subject}”"
            ),
        ),
    ]

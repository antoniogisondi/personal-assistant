from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr

from gsoi_assistant.core.events import UsageInfo
from gsoi_assistant.core.types import DataClass


class ChatRequestBody(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: uuid.UUID | None = None
    profile: str | None = Field(
        default=None, description="Model profile; defaults to the configured one."
    )
    data_class: DataClass = Field(
        default=DataClass.PRIVATE,
        description="Sensitivity of this message. SECRET is only ever processed by local models.",
    )
    channel: Literal["text", "voice"] = Field(
        default="text", description="'voice' makes the answer suitable to be read aloud."
    )


class BriefingRequestBody(BaseModel):
    channel: Literal["text", "voice"] = "voice"
    profile: str | None = None


class ApprovalOut(BaseModel):
    approval_id: uuid.UUID
    run_id: uuid.UUID
    tool: str
    risk: str
    strength: str
    display: dict[str, Any]
    expires_at: datetime


class ChatResponseBody(BaseModel):
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    status: str = Field(description="'done' or 'awaiting_approval'")
    content: str
    model: str
    usage: UsageInfo
    cost_usd: float
    approval: ApprovalOut | None = None


class DecisionBody(BaseModel):
    approve: bool
    confirm_tool: str | None = Field(
        default=None, description="For destructive actions: repeat the tool name to confirm."
    )


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str | None


class ToolCallOut(BaseModel):
    tool: str
    status: str
    decision: str
    risk: int | None
    latency_ms: int


class RunOut(BaseModel):
    run_id: uuid.UUID
    status: str
    tainted: bool
    cost_usd: float
    tool_calls: list[ToolCallOut]


class AuditVerifyOut(BaseModel):
    ok: bool
    entries: int
    first_bad_seq: int | None


class HealthBody(BaseModel):
    status: str


class ConnectionOut(BaseModel):
    provider: str
    configured: bool = Field(description="The application credentials (Client ID/Secret) are set.")
    config_source: str | None = Field(
        default=None, description="'app' (saved from /setup) or 'env'."
    )
    connected: bool = Field(description="The user has granted access.")
    status: str
    scopes: list[str]
    redirect_uri: str = Field(description="Authorized redirect URI to register in Google Cloud.")


class GoogleConfigBody(BaseModel):
    client_id: str = Field(min_length=10, max_length=256)
    client_secret: str = Field(min_length=8, max_length=256)


class ConnectStartOut(BaseModel):
    auth_url: str = Field(description="Open this URL in your browser to grant access.")


class AlertsCheckBody(BaseModel):
    lead_minutes: int = Field(default=10, ge=1, le=120, description="Warn this long before events.")


class AlertOut(BaseModel):
    kind: str
    key: str
    title: str
    text: str
    spoken: str
    important: bool


class AlertsOut(BaseModel):
    alerts: list[AlertOut]
    unavailable: list[str]


class MailAccountBody(BaseModel):
    address: str = Field(min_length=5, max_length=254)
    password: SecretStr = Field(description="Account password (or an app password).")
    label: str | None = Field(default=None, max_length=60)
    imap_host: str | None = Field(default=None, max_length=253)
    smtp_host: str | None = Field(default=None, max_length=253)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: str | None = Field(default=None, pattern="^(ssl|starttls)$")


class MailAccountOut(BaseModel):
    id: str
    label: str
    address: str
    imap_host: str
    smtp_host: str

"""initial schema: conversations, runs, messages, model_calls

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _ts() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")  # used from the memory phase on

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200)),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        *_ts(),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id")),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("user_request", sa.Text(), nullable=False),
        sa.Column("route", JSONType),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("state", JSONType),
        sa.Column("tainted", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("result", sa.Text()),
        *_ts(),
    )
    op.create_index("ix_runs_user_id", "runs", ["user_id"])
    op.create_index("ix_runs_conversation_id", "runs", ["conversation_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id")),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("tool_calls", JSONType),
        sa.Column("data_class", sa.Integer(), nullable=False),
        *_ts(),
    )
    op.create_index(
        "ix_messages_conversation_created", "messages", ["conversation_id", "created_at"]
    )

    op.create_table(
        "model_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id")),
        sa.Column("profile", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("data_class_max", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text()),
        *_ts(),
    )
    op.create_index("ix_model_calls_run_id", "model_calls", ["run_id"])


def downgrade() -> None:
    op.drop_table("model_calls")
    op.drop_table("messages")
    op.drop_table("runs")
    op.drop_table("conversations")

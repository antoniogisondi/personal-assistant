"""items already announced (new mail, upcoming events), so each alert fires once

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seen_items",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("seen_items")

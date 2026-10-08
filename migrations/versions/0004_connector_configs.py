"""connector application settings (e.g. Google OAuth client), secret encrypted

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_configs",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("client_id", sa.String(256), nullable=False),
        sa.Column("client_secret_enc", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("connector_configs")

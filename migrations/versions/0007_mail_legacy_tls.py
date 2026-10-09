"""per-account consent to an outdated TLS for the outgoing server (providers like Tiscali)

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mail_accounts",
        sa.Column("smtp_legacy_tls", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("mail_accounts", "smtp_legacy_tls")

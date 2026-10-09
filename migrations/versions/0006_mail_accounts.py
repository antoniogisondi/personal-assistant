"""email accounts reached over IMAP/SMTP (Tiscali, Libero, ...), password encrypted

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_accounts",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("label", sa.String(60), nullable=False),
        sa.Column("address", sa.String(254), nullable=False),
        sa.Column("username", sa.String(254), nullable=False),
        sa.Column("imap_host", sa.String(253), nullable=False),
        sa.Column("imap_port", sa.Integer(), nullable=False),
        sa.Column("smtp_host", sa.String(253), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("smtp_security", sa.String(8), nullable=False),
        sa.Column("password_enc", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "address", name="uq_mail_user_address"),
    )


def downgrade() -> None:
    op.drop_table("mail_accounts")

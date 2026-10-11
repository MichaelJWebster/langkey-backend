"""Structured lines on a passage

Revision ID: d1e4b82c6f30
Revises: c5a81f3e74b2
Create Date: 2026-10-11

Nullable, and no backfill: a passage without lines renders as the paragraph
it already was.
"""

import sqlalchemy as sa
from alembic import op

revision = "d1e4b82c6f30"
down_revision = "c5a81f3e74b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("passages", sa.Column("lines", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("passages", "lines")

"""English title for reading texts

Revision ID: c9d4e2b73f18
Revises: b7c3f1a90d42
Create Date: 2026-10-05 11:40:00.000000

Nullable: a text whose title translation was rejected by the quality gates
keeps the Norwegian title alone rather than storing something wrong.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c9d4e2b73f18'
down_revision = 'b7c3f1a90d42'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('texts', sa.Column('title_en', sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column('texts', 'title_en')

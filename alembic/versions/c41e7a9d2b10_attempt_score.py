"""attempt score

Revision ID: c41e7a9d2b10
Revises: ab8eb99f4057
Create Date: 2026-10-02 10:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c41e7a9d2b10'
down_revision = 'ab8eb99f4057'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('exercise_attempts', sa.Column('score', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('exercise_attempts', 'score')

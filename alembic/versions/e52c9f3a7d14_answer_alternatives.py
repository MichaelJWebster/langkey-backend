"""user answer alternatives

Revision ID: e52c9f3a7d14
Revises: c41e7a9d2b10
Create Date: 2026-10-02 18:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e52c9f3a7d14'
down_revision = 'c41e7a9d2b10'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_answer_alternatives',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('direction', sa.String(length=16), nullable=False),
        sa.Column('answer', sa.String(length=256), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['items.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'item_id', 'direction', 'answer'),
    )
    op.create_index(
        op.f('ix_user_answer_alternatives_user_id'),
        'user_answer_alternatives',
        ['user_id'],
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_user_answer_alternatives_user_id'), table_name='user_answer_alternatives'
    )
    op.drop_table('user_answer_alternatives')

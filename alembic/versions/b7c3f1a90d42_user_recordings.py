"""user recordings

Revision ID: b7c3f1a90d42
Revises: e52c9f3a7d14
Create Date: 2026-10-04

"""
import sqlalchemy as sa
from alembic import op

revision = 'b7c3f1a90d42'
down_revision = 'e52c9f3a7d14'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_recordings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('example_id', sa.Integer(), nullable=True),
        sa.Column('object_key', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['example_id'], ['item_examples.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('object_key'),
    )
    op.create_index(op.f('ix_user_recordings_user_id'), 'user_recordings', ['user_id'])
    op.create_index(op.f('ix_user_recordings_item_id'), 'user_recordings', ['item_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_user_recordings_item_id'), table_name='user_recordings')
    op.drop_index(op.f('ix_user_recordings_user_id'), table_name='user_recordings')
    op.drop_table('user_recordings')

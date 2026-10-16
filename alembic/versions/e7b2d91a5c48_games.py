"""Game sets, pairs and rounds

Revision ID: e7b2d91a5c48
Revises: d1e4b82c6f30
Create Date: 2026-10-17

Authored content plus the learner's completed rounds. Nothing is backfilled:
the sets arrive through the seed sync on the next startup.
"""

import sqlalchemy as sa
from alembic import op

revision = "e7b2d91a5c48"
down_revision = "d1e4b82c6f30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_sets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("language_code", sa.String(length=8), nullable=False),
        sa.Column("game", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["language_code"], ["languages.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_game_sets_language_code", "game_sets", ["language_code"])

    op.create_table(
        "game_pairs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("en", sa.String(length=64), nullable=False),
        sa.Column("no", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["set_id"], ["game_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("set_id", "position"),
    )
    op.create_index("ix_game_pairs_set_id", "game_pairs", ["set_id"])

    op.create_table(
        "game_rounds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=True),
        sa.Column("pairs", sa.Integer(), nullable=False),
        sa.Column("mistakes", sa.Integer(), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["set_id"], ["game_sets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_game_rounds_user_id", "game_rounds", ["user_id"])
    op.create_index("ix_game_rounds_set_id", "game_rounds", ["set_id"])
    op.create_index("ix_game_rounds_created_at", "game_rounds", ["created_at"])


def downgrade() -> None:
    op.drop_table("game_rounds")
    op.drop_index("ix_game_pairs_set_id", table_name="game_pairs")
    op.drop_table("game_pairs")
    op.drop_index("ix_game_sets_language_code", table_name="game_sets")
    op.drop_table("game_sets")

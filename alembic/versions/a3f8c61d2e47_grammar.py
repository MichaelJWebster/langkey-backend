"""Grammar units, exercises and attempts

Revision ID: a3f8c61d2e47
Revises: f3c1a0d9e2b7
Create Date: 2026-10-22

Authored content plus the learner's answers. Nothing is backfilled: the
units and exercises arrive through the seed sync on the next startup.
"""

import sqlalchemy as sa
from alembic import op

revision = "a3f8c61d2e47"
down_revision = "f3c1a0d9e2b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grammar_units",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("language_code", sa.String(length=8), nullable=False),
        sa.Column("topic", sa.String(length=32), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("topic_title", sa.String(length=128), nullable=False),
        sa.Column("topic_blurb", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("blurb", sa.Text(), nullable=False),
        sa.Column("review", sa.Boolean(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["language_code"], ["languages.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("language_code", "topic", "unit"),
    )
    op.create_index("ix_grammar_units_language_code", "grammar_units", ["language_code"])

    op.create_table(
        "grammar_exercises",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["unit_id"], ["grammar_units.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_grammar_exercises_unit_id", "grammar_exercises", ["unit_id"])

    op.create_table(
        "grammar_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("exercise_id", sa.Integer(), nullable=True),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["exercise_id"], ["grammar_exercises.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_grammar_attempts_user_id", "grammar_attempts", ["user_id"])
    op.create_index("ix_grammar_attempts_exercise_id", "grammar_attempts", ["exercise_id"])
    op.create_index("ix_grammar_attempts_created_at", "grammar_attempts", ["created_at"])


def downgrade() -> None:
    op.drop_table("grammar_attempts")
    op.drop_index("ix_grammar_exercises_unit_id", table_name="grammar_exercises")
    op.drop_table("grammar_exercises")
    op.drop_index("ix_grammar_units_language_code", table_name="grammar_units")
    op.drop_table("grammar_units")

"""A1 translation passages

Revision ID: c5a81f3e74b2
Revises: b3c7d50e9a14
Create Date: 2026-10-11

Authored content (passages, glosses, topics) plus the learner's self-marked
attempts. Nothing is backfilled: the passages arrive through the seed sync on
the next startup, and there is no earlier data to migrate.
"""

import sqlalchemy as sa
from alembic import op

revision = "c5a81f3e74b2"
down_revision = "b3c7d50e9a14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "passages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("language_code", sa.String(length=8), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False, server_default="A1"),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("no_text", sa.Text(), nullable=False),
        sa.Column("en_text", sa.Text(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
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
    op.create_index("ix_passages_language_code", "passages", ["language_code"])

    op.create_table(
        "passage_glosses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("passage_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("form", sa.String(length=64), nullable=False),
        sa.Column("lemma", sa.String(length=64), nullable=False),
        sa.Column("en", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["passage_id"], ["passages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("passage_id", "position"),
    )
    op.create_index("ix_passage_glosses_passage_id", "passage_glosses", ["passage_id"])

    op.create_table(
        "passage_topics",
        sa.Column("passage_id", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["passage_id"], ["passages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("passage_id", "topic"),
    )

    op.create_table(
        "passage_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("passage_id", sa.Integer(), nullable=True),
        sa.Column("mark", sa.String(length=8), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("revealed", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["passage_id"], ["passages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_passage_attempts_user_id", "passage_attempts", ["user_id"])
    op.create_index("ix_passage_attempts_passage_id", "passage_attempts", ["passage_id"])
    op.create_index("ix_passage_attempts_created_at", "passage_attempts", ["created_at"])


def downgrade() -> None:
    op.drop_table("passage_attempts")
    op.drop_table("passage_topics")
    op.drop_table("passage_glosses")
    op.drop_index("ix_passages_language_code", table_name="passages")
    op.drop_table("passages")

"""Conversation: dialogues, runs, spoken turns; attempts may come from a turn

Revision ID: b5d2e8f41c09
Revises: a3f8c61d2e47
Create Date: 2026-10-24

Dialogues arrive through the seed sync on the next startup. Every existing
pronunciation attempt has a recording, so the one-source check holds for the
rows already there.
"""

import sqlalchemy as sa
from alembic import op

revision = "b5d2e8f41c09"
down_revision = "a3f8c61d2e47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dialogues",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("language_code", sa.String(length=8), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("title_en", sa.String(length=128), nullable=False),
        sa.Column("blurb", sa.Text(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("character", sa.JSON(), nullable=False),
        sa.Column("start", sa.String(length=64), nullable=False),
        sa.Column("nodes", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["language_code"], ["languages.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("language_code", "slug"),
    )
    op.create_index("ix_dialogues_language_code", "dialogues", ["language_code"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("dialogue_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dialogue_id"], ["dialogues.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_dialogue_id", "conversations", ["dialogue_id"])

    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("transcript_model", sa.String(length=128), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=True),
        sa.Column("scorer_model", sa.String(length=128), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("decision_reason", sa.String(length=64), nullable=False),
        sa.Column("picked_id", sa.String(length=64), nullable=True),
        sa.Column("confirmed_id", sa.String(length=64), nullable=True),
        sa.Column("confirmed_text", sa.Text(), nullable=True),
        sa.Column("confirmed_how", sa.String(length=16), nullable=True),
        sa.Column("transcribe_ms", sa.Integer(), nullable=True),
        sa.Column("score_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(
        "ix_conversation_turns_conversation_id", "conversation_turns", ["conversation_id"]
    )
    op.create_index("ix_conversation_turns_user_id", "conversation_turns", ["user_id"])

    with op.batch_alter_table("pronunciation_attempts") as batch:
        batch.alter_column("recording_id", existing_type=sa.Uuid(), nullable=True)
        batch.add_column(sa.Column("turn_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_pronunciation_attempts_turn_id",
            "conversation_turns",
            ["turn_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_pronunciation_attempts_turn_id", ["turn_id"])
        batch.create_check_constraint(
            "pronunciation_attempts_one_source", "(recording_id IS NULL) <> (turn_id IS NULL)"
        )


def downgrade() -> None:
    op.execute("DELETE FROM pronunciation_attempts WHERE turn_id IS NOT NULL")
    with op.batch_alter_table("pronunciation_attempts") as batch:
        batch.drop_constraint("pronunciation_attempts_one_source", type_="check")
        batch.drop_index("ix_pronunciation_attempts_turn_id")
        batch.drop_constraint("fk_pronunciation_attempts_turn_id", type_="foreignkey")
        batch.drop_column("turn_id")
        batch.alter_column("recording_id", existing_type=sa.Uuid(), nullable=False)

    op.drop_index("ix_conversation_turns_user_id", table_name="conversation_turns")
    op.drop_index("ix_conversation_turns_conversation_id", table_name="conversation_turns")
    op.drop_table("conversation_turns")
    op.drop_index("ix_conversations_dialogue_id", table_name="conversations")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_dialogues_language_code", table_name="dialogues")
    op.drop_table("dialogues")

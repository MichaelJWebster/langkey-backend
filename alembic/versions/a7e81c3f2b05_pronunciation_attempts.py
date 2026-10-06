"""Word-level pronunciation attempts, and transcripts on recordings

Revision ID: a7e81c3f2b05
Revises: c9d4e2b73f18
Create Date: 2026-10-06

"""

import sqlalchemy as sa
from alembic import op

revision = "a7e81c3f2b05"
down_revision = "c9d4e2b73f18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_recordings", sa.Column("transcript", sa.Text(), nullable=True))
    op.add_column(
        "user_recordings", sa.Column("transcript_model", sa.String(length=128), nullable=True)
    )
    op.create_table(
        "pronunciation_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("recording_id", sa.Uuid(), nullable=False),
        sa.Column("language_code", sa.String(length=8), nullable=False),
        sa.Column("word", sa.String(length=64), nullable=False),
        sa.Column("said", sa.Boolean(), nullable=False),
        sa.Column("heard", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recording_id"], ["user_recordings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pronunciation_attempts_user_id", "pronunciation_attempts", ["user_id"]
    )
    op.create_index(
        "ix_pronunciation_attempts_recording_id", "pronunciation_attempts", ["recording_id"]
    )
    op.create_index("ix_pronunciation_attempts_word", "pronunciation_attempts", ["word"])
    # The tally query: this learner's record for these words.
    op.create_index(
        "ix_pronunciation_attempts_user_word",
        "pronunciation_attempts",
        ["user_id", "language_code", "word"],
    )


def downgrade() -> None:
    op.drop_index("ix_pronunciation_attempts_user_word", table_name="pronunciation_attempts")
    op.drop_index("ix_pronunciation_attempts_word", table_name="pronunciation_attempts")
    op.drop_index("ix_pronunciation_attempts_recording_id", table_name="pronunciation_attempts")
    op.drop_index("ix_pronunciation_attempts_user_id", table_name="pronunciation_attempts")
    op.drop_table("pronunciation_attempts")
    op.drop_column("user_recordings", "transcript_model")
    op.drop_column("user_recordings", "transcript")

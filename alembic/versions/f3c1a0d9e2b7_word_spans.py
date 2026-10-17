"""Word spans: where each word sits in a take, and in a reference clip

Revision ID: f3c1a0d9e2b7
Revises: e7b2d91a5c48
Create Date: 2026-10-17

For playing one word of the reference against the same word of the learner's
take (ADR-043). Nothing is backfilled: a take's words are placed the next time
it is checked, and a clip's the first time anyone asks for them.
"""

import sqlalchemy as sa
from alembic import op

revision = "f3c1a0d9e2b7"
down_revision = "e7b2d91a5c48"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_recordings", sa.Column("transcript_words", sa.JSON(), nullable=True))
    op.add_column(
        "user_recordings", sa.Column("alignment_model", sa.String(length=128), nullable=True)
    )

    op.create_table(
        "clip_alignments",
        sa.Column("object_key", sa.String(length=255), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("words", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("object_key"),
    )


def downgrade() -> None:
    op.drop_table("clip_alignments")
    op.drop_column("user_recordings", "alignment_model")
    op.drop_column("user_recordings", "transcript_words")

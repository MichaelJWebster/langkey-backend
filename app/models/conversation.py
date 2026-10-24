"""Conversations: scripted dialogues, the learner's runs through them, and
every spoken turn -- which is the corpus (docs/plans/conversation-corpus.md).

A dialogue is a shallow tree of character lines, each with the answers the
learner is expected to give. It is content, synced from
`data/dialogues_<lang>.json` by slug like the grammar units, and stored whole
in one JSON column: nothing queries inside the tree, and the app reads a
dialogue only to walk it.

Each learner turn keeps its audio, what Whisper heard, every candidate's CTC
score, the decision taken, and the answer the learner confirmed. The
confirmation is the label: with it, `scripts/export_conversations.py` turns
real replies into a benchmark corpus that `scripts/bench_context.py` re-scores,
so the decision rule can be tuned on conversation rather than on sentences
read after a reference.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import utcnow


class Dialogue(Base):
    __tablename__ = "dialogues"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    language_code: Mapped[str] = mapped_column(ForeignKey("languages.code"), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(128))
    title_en: Mapped[str] = mapped_column(String(128))
    blurb: Mapped[str] = mapped_column(Text)
    #: Difficulty, rising with `seq`: the list runs easiest first, and a
    #: dialogue opens once the one before it has been finished.
    level: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer)
    character: Mapped[dict] = mapped_column(JSON)
    start: Mapped[str] = mapped_column(String(64))
    nodes: Mapped[dict] = mapped_column(JSON)

    __table_args__ = (UniqueConstraint("language_code", "slug"),)


class Conversation(Base):
    """One run through a dialogue."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dialogue_id: Mapped[int] = mapped_column(ForeignKey("dialogues.id", ondelete="CASCADE"), index=True)
    #: The line the character is waiting on an answer to; null once finished.
    node_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationTurn(Base):
    """One spoken answer, and everything needed to re-judge it offline."""

    __tablename__ = "conversation_turns"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)

    object_key: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    #: What Whisper heard; null when the transcriber was unavailable.
    transcript: Mapped[str | None] = mapped_column(Text)
    transcript_model: Mapped[str | None] = mapped_column(String(128))
    #: [{"id": answer id or null for the transcript, "text", "ctc", "letters"}]
    candidates: Mapped[list | None] = mapped_column(JSON)
    scorer_model: Mapped[str | None] = mapped_column(String(128))
    #: accepted | choice | unclear (app/conversation.py), and why.
    decision: Mapped[str] = mapped_column(String(16))
    decision_reason: Mapped[str] = mapped_column(String(64), default="")
    picked_id: Mapped[str | None] = mapped_column(String(64))
    #: The ground truth: what the learner says they said. Null until they
    #: confirm, and for a turn abandoned by trying again.
    confirmed_id: Mapped[str | None] = mapped_column(String(64))
    #: "None of these", typed by the learner when no answer fitted.
    confirmed_text: Mapped[str | None] = mapped_column(Text)
    #: echo (accepted, not corrected) | choice | list | typed | retry | corrected
    confirmed_how: Mapped[str | None] = mapped_column(String(16))

    transcribe_ms: Mapped[int | None] = mapped_column(Integer)
    score_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

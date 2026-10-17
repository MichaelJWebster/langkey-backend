"""Where each word sits in a reference clip (ADR-043).

Content, not user data: a clip is content-addressed, so its alignment is a
pure function of the bytes and the text, the same for every learner and every
visit. Computed the first time anyone asks for a clip's words and kept, so
the speech service sees each clip once rather than once per play.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import utcnow


class ClipAlignment(Base):
    __tablename__ = "clip_alignments"

    #: The clip's object key -- audio/<lang>/<digest>.wav -- which already
    #: encodes the voice, the speed and the text (app/audio.py).
    object_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    #: One entry per word of `words(text)`, in order: {word, start, end} in
    #: seconds, or null where the aligner could not place the word.
    words: Mapped[list[dict | None]] = mapped_column(JSON)
    #: Which aligner placed them. ADR-031 applies here as much as to the
    #: transcriber, and is only checkable if recorded with the result.
    model: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

"""A1 translation passages: serve them, and record the learner's own marking.

Selection is deliberately not FSRS. A passage is not a card: there is no
recall interval to model, and the useful order is simply "what you have not
done, then what you got wrong". Sorting happens in Python because the whole
set is a few hundred rows and the rule is easier to read than the SQL for it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.glossary import form_index, matched_forms
from app.models import (
    MARKS,
    Item,
    Passage,
    PassageAttempt,
    User,
    UserGlossaryEntry,
)

router = APIRouter(prefix="/api/v1/passages", tags=["passages"])

SESSION_SIZE = 10

#: How many passages that went badly may open a session. The point of a cap is
#: that a bad day does not become a wall of its own failures next time: the
#: session leads with a few of them and then moves on to new material. Same
#: idea as REPEAT_LAPSE_CAP in the repetition deck.
REVIEW_SLOTS = 3

#: Lower sorts earlier among the rest. Unseen passages are the forward
#: progress; one already marked "got" is last but never retired, so the
#: section does not run dry.
MARK_PRIORITY = {None: 0, "missed": 1, "partly": 2, "got": 3}


class GlossOut(BaseModel):
    form: str
    lemma: str
    en: str
    #: "new" -- not in the course at all, glossed by the author.
    #: "unseen" -- a deck word, but one this learner has not met yet.
    source: str


class LineOut(BaseModel):
    role: str  # one of ROLES
    no: str
    en: str


class PassageOut(BaseModel):
    slug: str
    kind: str
    level: str
    no: str
    en: str  # the reference translation, revealed by the UI on request
    #: Present when the passage is an exchange or otherwise wants its lines
    #: kept apart; null when it is a plain paragraph.
    lines: list[LineOut] | None
    word_count: int
    topics: list[str]
    glossary: list[GlossOut]
    last_mark: str | None
    attempts: int


def _glossary(
    passage: Passage,
    index,  # FormIndex
    known: set[int],
    words: dict[int, tuple[str, str]],  # item id -> (source text, translation)
) -> list[GlossOut]:
    """The authored glossary plus every deck word this learner has not met.

    Michael: *"there are words that I haven't seen appearing in the sentences,
    though they might be in the A1 vocabulary"*. Being in the course is no help
    while reading if you have not been taught it yet, so the two kinds of
    unfamiliar word are glossed the same way and marked so the UI can say
    which is which.

    Two things keep the list readable:

    **Longer glosses swallow shorter ones.** `rett over gata` is one deck entry
    and so are `rett` and `over`; glossing all three turns a six-word sentence
    into a wall of notes. A gloss whose span sits inside a longer one is
    dropped, which also removes the duplicate when an authored note covers a
    phrase the deck happens to contain.

    **An ambiguous form shows every meaning.** `dusjer` is both `en dusj` plus
    a suffix and the present tense of `å dusje`; `FormIndex.forms` answers with
    whichever came first in the deck, which is how a verb ends up glossed as a
    noun. Here all the candidates are joined, so the learner sees the choice
    instead of a confident wrong answer.
    """
    text = passage.no_text
    lowered = text.lower()
    # (start, end, authored?, gloss)
    candidates: list[tuple[int, int, bool, GlossOut]] = []

    for g in passage.glosses:
        at = lowered.find(g.form.lower())
        if at < 0:
            at = len(text)
        candidates.append(
            (
                at,
                at + len(g.form),
                True,
                GlossOut(form=g.form, lemma=g.lemma, en=g.en, source="new"),
            )
        )

    for form, item_id, at in matched_forms(index, text):
        # Every item this surface form could mean, not just the first claimant.
        rows = [
            words[i]
            for i in index.form_items.get(form.lower(), [item_id])
            if i in words and i not in known
        ]
        if not rows:
            continue
        candidates.append(
            (
                at,
                at + len(form),
                False,
                GlossOut(
                    form=form,
                    lemma=" · ".join(dict.fromkeys(lemma for lemma, _ in rows)),
                    en=" · ".join(dict.fromkeys(en for _, en in rows)),
                    source="unseen",
                ),
            )
        )

    # Longest first, so a phrase is settled before the words inside it.
    kept: list[tuple[int, int, bool, GlossOut]] = []
    for cand in sorted(candidates, key=lambda c: (c[0] - c[1], not c[2])):
        if any(
            k[0] <= cand[0] and cand[1] <= k[1] and (k[0], k[1]) != (cand[0], cand[1])
            for k in kept
        ):
            continue
        if any(k[3].form.lower() == cand[3].form.lower() for k in kept):
            continue
        kept.append(cand)

    return [g for _at, _end, _authored, g in sorted(kept, key=lambda c: c[0])]


class PassageSessionOut(BaseModel):
    total: int
    attempted: int  # distinct passages marked at least once
    got: int  # distinct passages whose latest mark is "got"
    passages: list[PassageOut]


@router.get("", response_model=PassageSessionOut)
async def passage_session(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    language: str = "nb",
    topic: str | None = None,
    limit: int = SESSION_SIZE,
) -> PassageSessionOut:
    rows = list(
        await session.scalars(
            select(Passage)
            .where(Passage.language_code == language)
            .order_by(Passage.seq)
        )
    )
    if topic:
        rows = [p for p in rows if any(t.topic == topic for t in p.topics)]

    # Latest mark and attempt count per passage, for this user only.
    latest = (
        select(
            PassageAttempt.passage_id,
            func.count().label("n"),
            func.max(PassageAttempt.id).label("last_id"),
        )
        .where(PassageAttempt.user_id == user.id)
        .group_by(PassageAttempt.passage_id)
        .subquery()
    )
    history = {
        pid: (mark, n)
        for pid, n, mark in await session.execute(
            select(latest.c.passage_id, latest.c.n, PassageAttempt.mark).join(
                PassageAttempt, PassageAttempt.id == latest.c.last_id
            )
        )
    }

    # The glossary is built for every passage, not just the ones served: how
    # many words a passage needs explained is what decides its place in the
    # queue, so it has to be known before the sort. It is pure CPU -- a regex
    # and some dict lookups per passage -- and the two cannot drift apart,
    # because the number the sort uses is the number of hints he will see.
    index = await form_index(session, language)
    known = set(
        await session.scalars(
            select(UserGlossaryEntry.item_id).where(
                UserGlossaryEntry.user_id == user.id
            )
        )
    )
    words = {
        item_id: (source_text, translation)
        for item_id, source_text, translation in await session.execute(
            select(Item.id, Item.source_text, Item.translation).where(
                Item.language_code == language
            )
        )
    }
    glossaries = {p.id: _glossary(p, index, known, words) for p in rows}

    def mark_of(p: Passage) -> str | None:
        return history.get(p.id, (None, 0))[0]

    def sort_key(p: Passage) -> tuple[int, int, int]:
        # Michael: *"favouring sentences where I've seen more words"*. The
        # count of words needing a hint is the difficulty that matters -- a
        # long passage with one unfamiliar word reads easily, a short one with
        # four does not -- so it orders ahead of the authored sequence, which
        # survives as the tie-break and still puts the simpler grammar first.
        return (MARK_PRIORITY.get(mark_of(p), 0), len(glossaries[p.id]), p.seq)

    limit = max(1, limit)
    # Lead with a few that went badly, then work forward through the rest.
    review = [p for p in rows if mark_of(p) in ("missed", "partly")][:REVIEW_SLOTS]
    rest = [p for p in sorted(rows, key=sort_key) if p not in review]
    ordered = (review + rest)[:limit]

    # Which deck words this learner has already met, and the items behind the
    # ones they have not. Only the passages actually being served are looked
    # at, so the cost does not grow with the size of the content file.
    return PassageSessionOut(
        total=len(rows),
        attempted=sum(1 for p in rows if p.id in history),
        got=sum(1 for p in rows if history.get(p.id, (None, 0))[0] == "got"),
        passages=[
            PassageOut(
                slug=p.slug,
                kind=p.kind,
                level=p.level,
                no=p.no_text,
                en=p.en_text,
                lines=[LineOut(**ln) for ln in p.lines] if p.lines else None,
                word_count=p.word_count,
                topics=sorted(t.topic for t in p.topics),
                glossary=glossaries[p.id],
                last_mark=history.get(p.id, (None, 0))[0],
                attempts=history.get(p.id, (None, 0))[1],
            )
            for p in ordered
        ],
    )


class AttemptIn(BaseModel):
    mark: str
    answer: str | None = Field(default=None, max_length=4000)
    revealed: bool = True


@router.post("/{slug}/attempt")
async def record_attempt(
    slug: str,
    body: AttemptIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str | int]:
    if body.mark not in MARKS:
        raise HTTPException(422, f"mark must be one of {', '.join(MARKS)}")
    passage = await session.scalar(select(Passage).where(Passage.slug == slug))
    if passage is None:
        raise HTTPException(404, "No such passage")
    session.add(
        PassageAttempt(
            user_id=user.id,
            passage_id=passage.id,
            mark=body.mark,
            answer=body.answer,
            revealed=body.revealed,
        )
    )
    await session.commit()
    return {"slug": slug, "mark": body.mark, "score": MARKS.index(body.mark)}

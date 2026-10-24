"""Conversation: talk turn by turn with a character (ROADMAP M3 step 5).

The plan is docs/plans/conversation-corpus.md. A dialogue is scripted
content: each character line carries the answers the learner is expected to
give. A spoken turn is saved first, then transcribed by NB-Whisper and scored
against those answers by the CTC model (speech_service/scorer.py), and
app/conversation.decide takes it from there -- accepted (the character
replies), a choice between two close answers, or unclear (try again, or tap
the answer you gave). No LLM runs here (ADR-026): every line the character
says is authored and voiced by Piper in advance.

Every path ends in the learner confirming what they said, and every turn is
kept with its scores. That is the corpus the decision rule gets tuned on.

Dialogues run easiest first, and one opens when the one before it has been
finished once.
"""

import logging
import time
import uuid
from typing import Annotated

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio import audio_url
from app.auth import get_current_user
from app.config import get_settings
from app.content import _s3_client, load_dialogues
from app.conversation import (
    ACCEPTED,
    UNIVERSAL,
    UNIVERSAL_IDS,
    Answer,
    decide,
    paths,
)
from app.db import get_session
from app.models import (
    Conversation,
    ConversationTurn,
    Dialogue,
    PronunciationAttempt,
    User,
)
from app.models.common import utcnow
from app.pronunciation import SAID, compare, normalise
from app.routes.recordings import EXTENSIONS, MAX_BYTES, _base_type
from app.routes.speaking import SpeedOut, _speeds
from app.speech import (
    CandidateScorer,
    SpeechToText,
    TranscriptionError,
    get_scorer,
    get_transcriber,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/conversation", tags=["conversation"])

LANGUAGE = "nb"


# --- response shapes --------------------------------------------------------------


class LineOut(BaseModel):
    """Something the character says, with its clip on every speed rung."""

    text: str
    en: str
    audio_url: str
    speeds: list[SpeedOut]


class AnswerOut(BaseModel):
    id: str
    text: str
    en: str
    #: "Jeg forstår ikke." and "Kan du gjenta?", accepted on every line.
    universal: bool = False


class NodeOut(BaseModel):
    id: str
    line: LineOut
    answers: list[AnswerOut]


class CharacterOut(BaseModel):
    name: str
    role: str


class DialogueOut(BaseModel):
    slug: str
    title: str
    title_en: str
    blurb: str
    level: int
    character: CharacterOut
    #: Learner turns, shortest and longest route.
    turns_min: int
    turns_max: int
    finished: int
    unlocked: bool
    #: The dialogue to finish to open this one, when it is locked.
    unlock_after: str | None = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    dialogue: DialogueOut
    node: NodeOut
    #: Said when the turn was unclear, and when offering a choice.
    unclear: LineOut
    choice: LineOut


class StepOut(BaseModel):
    """What happens once an answer is confirmed."""

    #: The character's reply to the answer; null for a universal answer.
    reply: LineOut | None = None
    #: The next line to answer; for a universal answer, the same line again.
    node: NodeOut | None = None
    #: True for "Jeg forstår ikke." / "Kan du gjenta?": play the line slower.
    repeat: bool = False
    finished: bool = False


class TurnOut(BaseModel):
    turn_id: uuid.UUID
    decision: str
    reason: str
    #: What Whisper heard; null when no transcriber was reachable.
    transcript: str | None
    #: The answer taken (accepted) or thought likeliest (choice).
    picked: AnswerOut | None = None
    #: For a choice, the two answers to pick between, best first.
    offered: list[AnswerOut] = []
    #: Present when the turn was accepted: the conversation has moved on.
    step: StepOut | None = None


class ConfirmIn(BaseModel):
    """Exactly one of answer_id, text or retry."""

    answer_id: str | None = None
    #: "None of these": what the learner says they said, typed.
    text: str | None = None
    retry: bool = False
    #: How the learner chose: "choice" (from two offered) or "list".
    how: str = "list"


class StartIn(BaseModel):
    slug: str


# --- helpers ----------------------------------------------------------------------


def _line(text: str, en: str) -> LineOut:
    return LineOut(
        text=text,
        en=en,
        audio_url=audio_url(LANGUAGE, text, primary=True),
        speeds=_speeds(LANGUAGE, text),
    )


def _universal_answers() -> list[AnswerOut]:
    return [AnswerOut(id=i, text=t, en=e, universal=True) for i, t, e in UNIVERSAL]


def _node_out(dialogue: Dialogue, node_id: str) -> NodeOut:
    node = dialogue.nodes[node_id]
    return NodeOut(
        id=node_id,
        line=_line(node["say"], node["say_en"]),
        answers=[AnswerOut(id=a["id"], text=a["text"], en=a["en"]) for a in node["answers"]]
        + _universal_answers(),
    )


def _answer(dialogue: Dialogue, node_id: str, answer_id: str) -> dict | None:
    """The authored answer, or a universal one as {id, text, en}."""
    for a in dialogue.nodes[node_id]["answers"]:
        if a["id"] == answer_id:
            return a
    for i, t, e in UNIVERSAL:
        if i == answer_id:
            return {"id": i, "text": t, "en": e}
    return None


def _answer_out(a: dict) -> AnswerOut:
    return AnswerOut(id=a["id"], text=a["text"], en=a["en"], universal=a["id"] in UNIVERSAL_IDS)


async def _finished_counts(session: AsyncSession, user: User) -> dict[int, int]:
    rows = await session.execute(
        select(Conversation.dialogue_id, func.count())
        .where(Conversation.user_id == user.id, Conversation.finished_at.is_not(None))
        .group_by(Conversation.dialogue_id)
    )
    return dict(rows.all())


async def _catalogue(session: AsyncSession, user: User) -> list[tuple[Dialogue, DialogueOut]]:
    dialogues = list(
        await session.scalars(
            select(Dialogue)
            .where(Dialogue.language_code == LANGUAGE, Dialogue.seq > 0)
            .order_by(Dialogue.seq)
        )
    )
    finished = await _finished_counts(session, user)
    out = []
    previous: Dialogue | None = None
    for d in dialogues:
        routes = paths(d.nodes, d.start)
        unlocked = previous is None or finished.get(previous.id, 0) > 0
        out.append(
            (
                d,
                DialogueOut(
                    slug=d.slug,
                    title=d.title,
                    title_en=d.title_en,
                    blurb=d.blurb,
                    level=d.level,
                    character=CharacterOut(**d.character),
                    turns_min=min(len(r) for r in routes),
                    turns_max=max(len(r) for r in routes),
                    finished=finished.get(d.id, 0),
                    unlocked=unlocked,
                    unlock_after=None if unlocked else previous.title,
                ),
            )
        )
        previous = d
    return out


def _fixed() -> tuple[LineOut, LineOut]:
    _, fixed = load_dialogues(LANGUAGE)
    return (
        _line(fixed["unclear"], fixed["unclear_en"]),
        _line(fixed["choice"], fixed["choice_en"]),
    )


async def _owned(session: AsyncSession, user: User, conversation_id: uuid.UUID) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(404, "Conversation not found")
    return conversation


async def _record_marks(
    session: AsyncSession, user: User, turn: ConversationTurn, said_text: str
) -> None:
    """Feed the pronunciation tally from a confirmed turn (ADR-036): each
    word of what the learner says they said, against what Whisper heard.
    Replaces any marks from an earlier confirmation of the same turn."""
    await session.execute(delete(PronunciationAttempt).where(PronunciationAttempt.turn_id == turn.id))
    if not turn.transcript:
        return
    session.add_all(
        PronunciationAttempt(
            user_id=user.id,
            turn_id=turn.id,
            language_code=LANGUAGE,
            word=normalise(m.word),
            said=m.verdict == SAID,
            heard=(m.heard or None),
        )
        for m in compare(said_text, turn.transcript)
        if normalise(m.word)
    )


async def _apply(
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    dialogue: Dialogue,
    turn: ConversationTurn,
    answer: dict,
    how: str,
) -> StepOut:
    """Confirm `answer` for `turn` and move the conversation on."""
    turn.confirmed_id = answer["id"]
    turn.confirmed_text = None
    turn.confirmed_how = how
    await _record_marks(session, user, turn, answer["text"])

    if answer["id"] in UNIVERSAL_IDS:
        # "I don't understand" / "can you repeat": the same line again.
        return StepOut(node=_node_out(dialogue, turn.node_id), repeat=True)

    conversation.node_id = answer["next"]
    reply = _line(answer["reply"], answer["reply_en"])
    if answer["next"] is None:
        conversation.finished_at = utcnow()
        return StepOut(reply=reply, finished=True)
    return StepOut(reply=reply, node=_node_out(dialogue, answer["next"]))


# --- routes -------------------------------------------------------------------------


@router.get("/dialogues", response_model=list[DialogueOut])
async def list_dialogues(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[DialogueOut]:
    return [out for _, out in await _catalogue(session, user)]


@router.post("/start", response_model=ConversationOut, status_code=201)
async def start(
    body: StartIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationOut:
    found = next(((d, out) for d, out in await _catalogue(session, user) if d.slug == body.slug), None)
    if found is None:
        raise HTTPException(404, "Unknown dialogue")
    dialogue, summary = found
    if not summary.unlocked:
        raise HTTPException(403, f"Finish “{summary.unlock_after}” first")

    conversation = Conversation(user_id=user.id, dialogue_id=dialogue.id, node_id=dialogue.start)
    session.add(conversation)
    await session.commit()
    unclear, choice = _fixed()
    return ConversationOut(
        id=conversation.id,
        dialogue=summary,
        node=_node_out(dialogue, dialogue.start),
        unclear=unclear,
        choice=choice,
    )


@router.post("/{conversation_id}/turn", response_model=TurnOut, status_code=201)
async def take_turn(
    conversation_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    transcriber: Annotated[SpeechToText, Depends(get_transcriber)],
    scorer: Annotated[CandidateScorer, Depends(get_scorer)],
    audio: Annotated[UploadFile, File()],
    duration_ms: Annotated[int | None, Form()] = None,
) -> TurnOut:
    conversation = await _owned(session, user, conversation_id)
    if conversation.node_id is None:
        raise HTTPException(409, "This conversation has finished")
    dialogue = await session.get(Dialogue, conversation.dialogue_id)
    if dialogue is None or conversation.node_id not in dialogue.nodes:
        raise HTTPException(409, "This conversation's dialogue has changed; start it again")

    base_type = _base_type(audio.content_type or "")
    if base_type not in EXTENSIONS:
        raise HTTPException(415, f"Unsupported audio type: {audio.content_type!r}")
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Empty recording")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Recording too large")

    seq = (
        await session.scalar(
            select(func.count()).where(ConversationTurn.conversation_id == conversation.id)
        )
        or 0
    ) + 1
    turn_id = uuid.uuid4()
    object_key = f"conversations/{user.id}/{turn_id}.{EXTENSIONS[base_type]}"
    # The audio is saved before any model runs: a turn whose audio is kept is
    # corpus even when the speech service is down.
    try:
        _s3_client().put_object(
            Bucket=get_settings().s3_bucket, Key=object_key, Body=data, ContentType=base_type
        )
    except (ClientError, BotoCoreError) as exc:
        logger.exception("Storing conversation turn %s failed", object_key)
        raise HTTPException(
            502, f"Audio storage unavailable — turn not saved ({type(exc).__name__})"
        )

    node = dialogue.nodes[conversation.node_id]
    answers = [
        Answer(a["id"], a["text"], meaning=f"{a['next']}|{a['reply']}") for a in node["answers"]
    ] + [Answer(i, t) for i, t, _ in UNIVERSAL]

    transcript = transcript_model = None
    transcribe_ms = score_ms = None
    started = time.monotonic()
    try:
        heard = await transcriber.transcribe(data, content_type=base_type)
        transcript, transcript_model = heard.text, heard.model
    except TranscriptionError as exc:  # TranscriptionUnavailable included
        logger.info("turn %s not transcribed: %s", turn_id, exc)
    transcribe_ms = int((time.monotonic() - started) * 1000)

    texts = [a.text for a in answers] + ([transcript] if transcript else [])
    scores = transcript_score = None
    scorer_model = None
    stored_candidates = None
    if transcript:
        started = time.monotonic()
        try:
            result = await scorer.score(data, content_type=base_type, candidates=texts)
        except TranscriptionError as exc:
            logger.info("turn %s not scored: %s", turn_id, exc)
        else:
            scorer_model = result.model
            scores = {a.id: c for a, c in zip(answers, result.candidates)}
            transcript_score = result.candidates[len(answers)]
            stored_candidates = [
                {"id": a.id, "text": c.text, "ctc": c.ctc, "letters": c.letters}
                for a, c in zip(answers, result.candidates)
            ] + [
                {"id": None, "text": transcript_score.text, "ctc": transcript_score.ctc,
                 "letters": transcript_score.letters}
            ]
        score_ms = int((time.monotonic() - started) * 1000)

    decision = decide(answers, scores, transcript, transcript_score)
    turn = ConversationTurn(
        id=turn_id,
        conversation_id=conversation.id,
        user_id=user.id,
        node_id=conversation.node_id,
        seq=seq,
        object_key=object_key,
        content_type=base_type,
        size_bytes=len(data),
        duration_ms=duration_ms,
        transcript=transcript,
        transcript_model=transcript_model,
        candidates=stored_candidates,
        scorer_model=scorer_model,
        decision=decision.kind,
        decision_reason=decision.reason,
        picked_id=decision.picked,
        transcribe_ms=transcribe_ms,
        score_ms=score_ms,
    )
    session.add(turn)
    await session.flush()

    picked = _answer(dialogue, turn.node_id, decision.picked) if decision.picked else None
    step = None
    if decision.kind == ACCEPTED and picked is not None:
        step = await _apply(session, user, conversation, dialogue, turn, picked, "echo")
    await session.commit()

    return TurnOut(
        turn_id=turn.id,
        decision=decision.kind,
        reason=decision.reason,
        transcript=transcript,
        picked=_answer_out(picked) if picked else None,
        offered=[_answer_out(_answer(dialogue, turn.node_id, i)) for i in decision.offered],
        step=step,
    )


@router.post("/{conversation_id}/turn/{turn_id}/confirm", response_model=StepOut)
async def confirm(
    conversation_id: uuid.UUID,
    turn_id: uuid.UUID,
    body: ConfirmIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StepOut:
    """Say what was actually said.

    For a turn still waiting (choice, unclear) this moves the conversation
    on. For one already accepted it is a correction: the label changes, the
    conversation does not rewind.
    """
    conversation = await _owned(session, user, conversation_id)
    turn = await session.get(ConversationTurn, turn_id)
    if turn is None or turn.conversation_id != conversation.id:
        raise HTTPException(404, "Turn not found")
    dialogue = await session.get(Dialogue, conversation.dialogue_id)
    if dialogue is None or turn.node_id not in dialogue.nodes:
        raise HTTPException(409, "This conversation's dialogue has changed")
    if sum(x is not None and x is not False for x in (body.answer_id, body.text, body.retry or None)) != 1:
        raise HTTPException(400, "Give exactly one of answer_id, text or retry")

    if body.retry:
        if turn.confirmed_id is not None:
            raise HTTPException(409, "This turn has already been confirmed")
        turn.confirmed_how = "retry"
        await session.commit()
        return StepOut()

    if body.text is not None:
        turn.confirmed_id = None
        turn.confirmed_text = body.text.strip()[:500]
        turn.confirmed_how = "typed"
        await _record_marks(session, user, turn, turn.confirmed_text)
        await session.commit()
        return StepOut()

    answer = _answer(dialogue, turn.node_id, body.answer_id)
    if answer is None:
        raise HTTPException(400, "Not an answer to this line")

    if turn.confirmed_id is not None or conversation.node_id != turn.node_id:
        # Already acted on: relabel only.
        turn.confirmed_id = answer["id"]
        turn.confirmed_how = "corrected"
        await _record_marks(session, user, turn, answer["text"])
        await session.commit()
        return StepOut()

    how = body.how if body.how in ("choice", "list") else "list"
    step = await _apply(session, user, conversation, dialogue, turn, answer, how)
    await session.commit()
    return step

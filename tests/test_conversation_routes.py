"""Conversation routes: the catalogue, starting, spoken turns, confirmation.

The speech service is faked: it "hears" whatever the test says and scores the
candidates so the one the test prefers wins, which is enough to steer the
decision rule into each of its three outcomes. S3 is the in-memory bucket.
"""

import json
import uuid

import pytest
from sqlalchemy import select

import app.routes.conversation as conversation_routes
from app import db
from app.conversation import CLEAR, MARGIN
from app.models import ConversationTurn, PronunciationAttempt
from app.speech import (
    CandidateScore,
    CandidateScores,
    Transcript,
    TranscriptionUnavailable,
)
from scripts.check_dialogues import DEFAULT
from tests.conftest import login
from tests.test_recordings import _FakeS3

AUDIO = b"\x1aE\xdf\xa3fake-webm-bytes" * 8
DIALOGUES = json.loads(DEFAULT.read_text())["dialogues"]
HEI = DIALOGUES[0]


@pytest.fixture
def bucket(monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(conversation_routes, "_s3_client", lambda **kw: fake)
    return fake


class FakeSpeech:
    """Hears `heard`; scores `prefer` best, `close` just behind it, and the
    transcript at `transcript_fit` per letter."""

    name = "fake"

    def __init__(self, heard=None, prefer=None, close=None, transcript_fit=-1.0, down=False):
        self.heard, self.prefer, self.close = heard, prefer, close
        self.transcript_fit, self.down = transcript_fit, down
        self.scored: list[list[str]] = []

    async def transcribe(self, audio, *, content_type, language="no"):
        if self.down:
            raise TranscriptionUnavailable("down")
        return Transcript(text=self.heard or "", language="no", model="NbAiLab/nb-whisper-medium")

    async def score(self, audio, *, content_type, candidates):
        if self.down:
            raise TranscriptionUnavailable("down")
        self.scored.append(candidates)
        out = []
        for i, text in enumerate(candidates):
            letters = max(1, len(text.replace(" ", "")))
            if i == len(candidates) - 1 and text == self.heard:
                per = self.transcript_fit
            elif text == self.prefer:
                per = -1.0
            elif text == self.close:
                per = -1.0 - CLEAR / 2
            else:
                per = -4.0
            out.append(CandidateScore(text=text, ctc=per * letters, letters=letters))
        return CandidateScores(candidates=tuple(out), model="NbAiLab/nb-wav2vec2-300m-bokmaal")


@pytest.fixture
def speech():
    from app.main import app as fastapi_app
    from app.speech import get_transcriber

    holder = {"fake": FakeSpeech()}
    fastapi_app.dependency_overrides[get_transcriber] = lambda: holder["fake"]
    yield holder
    fastapi_app.dependency_overrides.pop(get_transcriber, None)


async def _start(client, headers, slug="hei"):
    resp = await client.post("/api/v1/conversation/start", headers=headers, json={"slug": slug})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _turn(client, headers, conversation_id):
    resp = await client.post(
        f"/api/v1/conversation/{conversation_id}/turn",
        headers=headers,
        data={"duration_ms": "1800"},
        files={"audio": ("take.webm", AUDIO, "audio/webm;codecs=opus")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _confirm(client, headers, conversation_id, turn_id, **body):
    resp = await client.post(
        f"/api/v1/conversation/{conversation_id}/turn/{turn_id}/confirm", headers=headers, json=body
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _first_answer(node):
    return next(a for a in node["answers"] if not a["universal"])


async def test_the_catalogue_runs_easiest_first_and_only_the_first_is_open(client):
    headers = await login(client)
    listing = (await client.get("/api/v1/conversation/dialogues", headers=headers)).json()

    assert [d["slug"] for d in listing] == [d["slug"] for d in DIALOGUES]
    assert [d["level"] for d in listing] == sorted(d["level"] for d in listing)
    assert listing[0]["unlocked"] and not listing[1]["unlocked"]
    assert listing[1]["unlock_after"] == listing[0]["title"]
    assert all(5 <= d["turns_min"] <= d["turns_max"] <= 10 for d in listing)


async def test_a_locked_dialogue_cannot_be_started(client):
    headers = await login(client)
    resp = await client.post("/api/v1/conversation/start", headers=headers, json={"slug": "kafe"})
    assert resp.status_code == 403


async def test_start_gives_the_first_line_with_audio_and_its_answers(client):
    headers = await login(client)
    conv = await _start(client, headers)

    line = conv["node"]["line"]
    assert line["text"] == HEI["nodes"][HEI["start"]]["say"]
    assert line["audio_url"].startswith("/api/v1/audio/nb/")
    assert len(line["speeds"]) == 5
    universal = [a["text"] for a in conv["node"]["answers"] if a["universal"]]
    assert universal == ["Jeg forstår ikke.", "Kan du gjenta?"]
    assert conv["unclear"]["text"] and conv["choice"]["text"]


async def test_a_whole_dialogue_heard_cleanly_finishes_and_opens_the_next(client, bucket, speech):
    headers = await login(client)
    conv = await _start(client, headers)
    node, turns = conv["node"], 0
    while node is not None:
        answer = _first_answer(node)
        speech["fake"] = FakeSpeech(heard=answer["text"])  # Whisper heard it exactly
        result = await _turn(client, headers, conv["id"])
        assert result["decision"] == "accepted", result
        assert result["picked"]["id"] == answer["id"]
        assert result["step"]["reply"]["text"]
        node = result["step"]["node"]
        turns += 1
    assert result["step"]["finished"]
    assert turns == 5
    assert len(bucket.objects) == 5  # every turn's audio is kept

    listing = (await client.get("/api/v1/conversation/dialogues", headers=headers)).json()
    assert listing[0]["finished"] == 1
    assert listing[1]["unlocked"]


async def test_a_good_score_is_accepted_even_when_whisper_misheard(client, bucket, speech):
    headers = await login(client)
    conv = await _start(client, headers)
    answer = _first_answer(conv["node"])
    speech["fake"] = FakeSpeech(heard="Hei Ingrid hyggelig møte", prefer=answer["text"], transcript_fit=-1.0)

    result = await _turn(client, headers, conv["id"])

    assert result["decision"] == "accepted"
    assert result["transcript"] == "Hei Ingrid hyggelig møte"
    # The transcript is scored with the answers, last.
    assert speech["fake"].scored[0][-1] == "Hei Ingrid hyggelig møte"


async def test_two_close_answers_are_offered_and_the_pick_moves_on(client, bucket, speech):
    headers = await login(client)
    conv = await _start(client, headers)
    first, second = [a for a in conv["node"]["answers"] if not a["universal"]][:2]
    speech["fake"] = FakeSpeech(heard="hei hei du", prefer=first["text"], close=second["text"])

    result = await _turn(client, headers, conv["id"])
    assert result["decision"] == "choice"
    assert [a["id"] for a in result["offered"]] == [first["id"], second["id"]]
    assert result["step"] is None

    step = await _confirm(client, headers, conv["id"], result["turn_id"], answer_id=second["id"], how="choice")
    assert step["node"]["id"] != conv["node"]["id"]
    assert step["reply"]["text"]


async def test_off_script_is_unclear_and_retry_stays_put(client, bucket, speech):
    headers = await login(client)
    conv = await _start(client, headers)
    speech["fake"] = FakeSpeech(heard="Jeg har en stor hund", transcript_fit=-4.0 + MARGIN + 1.0)

    result = await _turn(client, headers, conv["id"])
    assert result["decision"] == "unclear" and result["picked"] is None

    assert await _confirm(client, headers, conv["id"], result["turn_id"], retry=True) == {
        "reply": None, "node": None, "repeat": False, "finished": False,
    }
    # Still on the first line: the next turn answers it.
    answer = _first_answer(conv["node"])
    speech["fake"] = FakeSpeech(heard=answer["text"])
    again = await _turn(client, headers, conv["id"])
    assert again["decision"] == "accepted" and again["picked"]["id"] == answer["id"]


async def test_i_dont_understand_repeats_the_line(client, bucket, speech):
    headers = await login(client)
    conv = await _start(client, headers)
    speech["fake"] = FakeSpeech(heard="Jeg forstår ikke")

    result = await _turn(client, headers, conv["id"])

    assert result["decision"] == "accepted" and result["picked"]["universal"]
    assert result["step"]["repeat"] and result["step"]["node"]["id"] == conv["node"]["id"]


async def test_with_the_speech_service_down_the_turn_is_kept_and_tapping_works(client, bucket, speech):
    headers = await login(client)
    conv = await _start(client, headers)
    speech["fake"] = FakeSpeech(down=True)

    result = await _turn(client, headers, conv["id"])
    assert result["decision"] == "unclear" and result["transcript"] is None
    assert len(bucket.objects) == 1

    answer = _first_answer(conv["node"])
    step = await _confirm(client, headers, conv["id"], result["turn_id"], answer_id=answer["id"])
    assert step["node"] is not None


async def test_a_confirmed_turn_feeds_the_pronunciation_tally_and_a_correction_replaces_it(
    client, bucket, speech
):
    headers = await login(client)
    conv = await _start(client, headers)
    answers = [a for a in conv["node"]["answers"] if not a["universal"]]
    speech["fake"] = FakeSpeech(heard="Hei Ingrid hyggelig å møte deg", prefer=answers[0]["text"])
    result = await _turn(client, headers, conv["id"])
    assert result["decision"] == "accepted"

    async with db.session_factory() as session:
        marks = list(await session.scalars(select(PronunciationAttempt)))
    assert marks and all(m.turn_id is not None and m.recording_id is None for m in marks)
    assert {m.word for m in marks} == {"hei", "ingrid", "hyggelig", "å", "møte", "deg"}

    # The learner says it was the other answer: the label and marks follow,
    # the conversation does not rewind.
    await _confirm(client, headers, conv["id"], result["turn_id"], answer_id=answers[1]["id"])
    async with db.session_factory() as session:
        turn = await session.get(ConversationTurn, uuid.UUID(result["turn_id"]))
        marks = list(await session.scalars(select(PronunciationAttempt)))
    assert (turn.confirmed_id, turn.confirmed_how) == (answers[1]["id"], "corrected")
    assert {m.word for m in marks} == {"hei", "jeg", "er", "ny", "her"}


async def test_the_turn_log_keeps_every_candidate_score(client, bucket, speech):
    headers = await login(client)
    conv = await _start(client, headers)
    answer = _first_answer(conv["node"])
    speech["fake"] = FakeSpeech(heard="hei ingrid", prefer=answer["text"])
    result = await _turn(client, headers, conv["id"])

    async with db.session_factory() as session:
        turn = await session.get(ConversationTurn, uuid.UUID(result["turn_id"]))
    ids = [c["id"] for c in turn.candidates]
    assert answer["id"] in ids and ids[-1] is None  # the transcript, last
    assert turn.scorer_model and turn.transcript_model
    assert turn.decision == "accepted" and turn.confirmed_how == "echo"


async def test_a_finished_conversation_takes_no_more_turns(client, bucket, speech):
    headers = await login(client)
    conv = await _start(client, headers)
    node = conv["node"]
    while node is not None:
        speech["fake"] = FakeSpeech(heard=_first_answer(node)["text"])
        node = (await _turn(client, headers, conv["id"]))["step"]["node"]
    resp = await client.post(
        f"/api/v1/conversation/{conv['id']}/turn",
        headers=headers,
        files={"audio": ("take.webm", AUDIO, "audio/webm")},
    )
    assert resp.status_code == 409


async def test_confirm_needs_exactly_one_kind_of_answer(client, bucket, speech):
    headers = await login(client)
    conv = await _start(client, headers)
    speech["fake"] = FakeSpeech(down=True)
    result = await _turn(client, headers, conv["id"])
    resp = await client.post(
        f"/api/v1/conversation/{conv['id']}/turn/{result['turn_id']}/confirm",
        headers=headers,
        json={"answer_id": "hyggelig", "retry": True},
    )
    assert resp.status_code == 400


async def test_saying_your_name_to_ingrid_is_accepted_and_shown_as_said(client, bucket, speech):
    headers = await login(client)
    conv = await _start(client, headers)
    # Whisper heard it cleanly; no fixed answer fits.
    speech["fake"] = FakeSpeech(heard="Hei, jeg heter Michael.", transcript_fit=-1.0)

    result = await _turn(client, headers, conv["id"])

    assert (result["decision"], result["reason"]) == ("accepted", "slot")
    assert result["picked"]["text"] == "Hei, jeg heter Michael."
    assert result["step"]["reply"]["text"] == "Hyggelig å møte deg!"
    # The slot answer is never sent for scoring: it has no fixed text.
    assert not any("…" in t for t in speech["fake"].scored[0])
    async with db.session_factory() as session:
        marks = list(await session.scalars(select(PronunciationAttempt)))
    assert marks == []  # nothing fixed to mark the take against


async def test_start_carries_the_off_script_line(client):
    headers = await login(client)
    conv = await _start(client, headers)
    assert "annen måte" in conv["offscript"]["text"]

"""Which expected answer did the learner give? The conversation turn's decision.

ROADMAP M3 step 5, docs/plans/conversation-corpus.md. A turn knows its
expected answers, so the question is not "what was said" -- NB-Whisper answers
that with fluent Norwegian the learner may not have produced -- but which of
the answers the audio supports. Each candidate is scored by the CTC model as
log P(text | audio) per letter (speech_service/scorer.py), and so is Whisper's
own transcript, which stands in for "something else".

Three outcomes, chosen on the 2026-10-24 benchmark (ROADMAP step 5):

* **accepted** -- the best answer holds its own against the transcript and
  clearly beats the runner-up. The character replies and echoes it.
* **choice** -- it holds against the transcript but the top two are close:
  "Sa du ... eller ...?", the learner taps one.
* **unclear** -- the transcript fits the audio much better than any answer
  (the learner went off-script, or mumbled), or nothing could be scored.
  The learner tries again or taps their answer.

Every path ends in a confirmed answer, which is the label the corpus needs.
Pure functions, so the rule is tested without a model and re-run offline on
logged turns.
"""

from dataclasses import dataclass

from app.pronunciation import normalise
from app.speech import CandidateScore

#: How far, in nats per letter, the best expected answer may trail Whisper's
#: transcript and still be taken. Between the benchmark's 0.3 (81% exact, 11%
#: wrong answers accepted off-script) and 0.5 (88%, 12%).
MARGIN = 0.4
#: How far the best answer must lead the runner-up to be taken without
#: asking. Below this the two are offered as a choice.
CLEAR = 0.15

ACCEPTED = "accepted"
CHOICE = "choice"
UNCLEAR = "unclear"

#: Answers every turn accepts, whatever the dialogue. Both have the character
#: say its line again, slower; neither moves the conversation on.
UNIVERSAL = (
    ("forstar-ikke", "Jeg forstår ikke.", "I don't understand."),
    ("gjenta", "Kan du gjenta?", "Can you repeat that?"),
)
UNIVERSAL_IDS = frozenset(u[0] for u in UNIVERSAL)


@dataclass(frozen=True)
class Answer:
    id: str
    text: str
    #: Answers with the same meaning lead the same way (same reply, same next
    #: line); a close call between two of them needs no question.
    meaning: str | None = None


@dataclass(frozen=True)
class Decision:
    kind: str
    #: The answer taken (accepted), or the best one (choice); None if unclear.
    picked: str | None
    #: For a choice: the answers offered, best first.
    offered: tuple[str, ...] = ()
    #: Why, for the turn log: which rule decided.
    reason: str = ""


def decide(
    answers: list[Answer],
    scores: dict[str, CandidateScore] | None,
    transcript: str | None,
    transcript_score: CandidateScore | None,
    *,
    margin: float = MARGIN,
    clear: float = CLEAR,
) -> Decision:
    """Decide which of `answers` was given.

    `scores` maps answer id to its CTC score, or is None when the scorer was
    unavailable. `transcript` is what Whisper heard (None when it was
    unavailable), `transcript_score` its CTC score.
    """
    heard = normalise(transcript or "")
    # Whisper heard one of the answers word for word: that is evidence enough,
    # and it keeps the turn working when the scorer is down.
    for a in answers:
        if heard and normalise(a.text) == heard:
            return Decision(ACCEPTED, a.id, reason="transcript matches")

    if not heard:
        # Nothing heard is silence or a failed capture, not an answer -- and
        # without a transcript there is nothing to measure "off-script" by.
        return Decision(UNCLEAR, None, reason="nothing heard")
    if not scores:
        return Decision(UNCLEAR, None, reason="no scores")

    ranked = sorted(
        (
            (scores[a.id].per_letter, a.id)
            for a in answers
            if a.id in scores and scores[a.id].per_letter is not None
        ),
        reverse=True,
    )
    if not ranked:
        return Decision(UNCLEAR, None, reason="nothing scorable")

    best_value, best_id = ranked[0]
    t = transcript_score.per_letter if transcript_score is not None else None
    if t is not None and best_value < t - margin:
        return Decision(UNCLEAR, None, reason="transcript fits better")

    if len(ranked) > 1 and best_value - ranked[1][0] < clear:
        meanings = {a.id: a.meaning for a in answers}
        runner_up = ranked[1][1]
        if meanings.get(best_id) is not None and meanings.get(best_id) == meanings.get(runner_up):
            return Decision(ACCEPTED, best_id, reason="close call, same meaning")
        return Decision(CHOICE, best_id, offered=(best_id, runner_up), reason="close call")
    return Decision(ACCEPTED, best_id, reason="clear win")


def paths(nodes: dict, start: str) -> list[list[str]]:
    """Every route through a dialogue from `start` to an end, as the ids of
    the nodes the learner answers. Raises ValueError on a cycle."""
    found: list[list[str]] = []

    def walk(node_id: str, trail: list[str]) -> None:
        if node_id in trail:
            raise ValueError(f"cycle through {node_id!r}")
        trail = [*trail, node_id]
        nexts = {a["next"] for a in nodes[node_id]["answers"]}
        for nxt in sorted(nexts, key=lambda n: (n is None, n or "")):
            if nxt is None:
                found.append(trail)
            else:
                walk(nxt, trail)

    walk(start, [])
    return found

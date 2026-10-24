"""The conversation turn's decision rule, without a model.

Scores are given per letter by setting letters=1, so the numbers read as the
per-letter values the rule compares.
"""

from app.conversation import (
    ACCEPTED,
    CHOICE,
    CLEAR,
    MARGIN,
    UNCLEAR,
    Answer,
    decide,
)
from app.pronunciation import words as app_words
from app.speech import CandidateScore

ANSWERS = [
    Answer("oslo", "Jeg bor i Oslo."),
    Answer("bergen", "Jeg bor i Bergen."),
    Answer("vet-ikke", "Det vet jeg ikke."),
]


def s(value):
    return CandidateScore(text="", ctc=value, letters=1)


def test_a_clear_winner_that_holds_against_the_transcript_is_accepted():
    d = decide(
        ANSWERS,
        {"oslo": s(-1.0), "bergen": s(-2.0), "vet-ikke": s(-3.0)},
        "Jeg bor i Åslo.",
        s(-0.9),
    )
    assert d.kind == ACCEPTED and d.picked == "oslo"


def test_two_close_answers_become_a_choice_best_first():
    d = decide(
        ANSWERS,
        {"oslo": s(-1.0), "bergen": s(-1.0 - CLEAR / 2), "vet-ikke": s(-3.0)},
        "Jeg bor i Bærum.",
        s(-1.0),
    )
    assert d.kind == CHOICE
    assert d.offered == ("oslo", "bergen")


def test_a_transcript_that_fits_far_better_is_off_script():
    d = decide(
        ANSWERS,
        {"oslo": s(-2.0), "bergen": s(-3.0), "vet-ikke": s(-4.0)},
        "Jeg har en katt.",
        s(-2.0 + MARGIN + 0.01),
    )
    assert d.kind == UNCLEAR and d.picked is None


def test_trailing_the_transcript_within_the_margin_is_still_accepted():
    d = decide(
        ANSWERS,
        {"oslo": s(-2.0), "bergen": s(-3.0), "vet-ikke": s(-4.0)},
        "Jeg bor i Åslo.",
        s(-2.0 + MARGIN - 0.01),
    )
    assert d.kind == ACCEPTED and d.picked == "oslo"


def test_whisper_hearing_an_answer_exactly_is_enough_even_without_scores():
    d = decide(ANSWERS, None, "jeg bor i bergen", None)
    assert d.kind == ACCEPTED and d.picked == "bergen"


def test_no_scorer_and_no_exact_match_asks_the_learner():
    d = decide(ANSWERS, None, "Jeg bor i Åslo.", None)
    assert d.kind == UNCLEAR


def test_nothing_heard_is_unclear_whatever_the_scores_say():
    d = decide(ANSWERS, {"oslo": s(-0.1)}, "", s(None))
    assert d.kind == UNCLEAR
    d = decide(ANSWERS, {"oslo": s(-0.1)}, None, None)
    assert d.kind == UNCLEAR


def test_unscorable_answers_are_skipped_not_fatal():
    d = decide(
        ANSWERS,
        {"oslo": s(None), "bergen": s(-1.0), "vet-ikke": s(-3.0)},
        "Jeg bor i Bergen, ja.",
        s(-1.1),
    )
    assert d.kind == ACCEPTED and d.picked == "bergen"


def test_an_unscorable_transcript_leaves_only_the_answers_to_compare():
    d = decide(ANSWERS, {"oslo": s(-1.0), "bergen": s(-2.0)}, "8", s(None))
    assert d.kind == ACCEPTED and d.picked == "oslo"


def test_the_speech_service_splits_words_the_way_the_app_does():
    """The scorer copies app.pronunciation's tokenisation rather than
    importing it (the speech image carries speech_service/ only); the two
    must not drift."""
    from speech_service.scorer import words as service_words

    for text in ["Hei, hvordan går det?", "Jeg bor i Oslo.", "Klokka er halv åtte!", "Ja - takk."]:
        assert service_words(text) == app_words(text)


def test_a_close_call_between_answers_that_mean_the_same_is_accepted():
    same = [
        Answer("bra", "Bra, takk.", meaning="m1"),
        Answer("fint", "Jeg har det fint.", meaning="m1"),
        Answer("trott", "Jeg er trøtt.", meaning="m2"),
    ]
    d = decide(same, {"bra": s(-1.0), "fint": s(-1.05), "trott": s(-3.0)}, "Bra tak", s(-1.0))
    assert (d.kind, d.picked, d.reason) == (ACCEPTED, "bra", "close call, same meaning")

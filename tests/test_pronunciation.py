"""Word-level comparison: a hint per take, a tally across them (ADR-036)."""

import pytest

from app.pronunciation import (
    MISSED,
    PATTERN_MIN_ATTEMPTS,
    SAID,
    SLIPPED,
    compare,
    is_pattern,
    normalise,
    words,
)


def verdicts(target: str, heard: str) -> dict[str, str]:
    return {m.word: m.verdict for m in compare(target, heard)}


def test_a_perfect_transcript_marks_everything_said():
    marks = compare("Toget går om ti minutter.", "Toget går om ti minutter.")
    assert {m.verdict for m in marks} == {SAID}


def test_punctuation_and_case_are_not_mistakes():
    marks = compare("Ha det bra! Vi snakkes.", "ha det bra vi snakkes")
    assert {m.verdict for m in marks} == {SAID}


def test_a_drifting_ending_is_a_slip_and_names_what_was_heard():
    # The real failure, measured 2026-10-06: klokka came back as klokke in 15
    # of 22 attempts. Knowing *what* it became is the whole value.
    marks = {m.word: m for m in compare("Møtet slutter klokka åtte.", "Møte slutter klokke åtte.")}
    assert marks["klokka"].verdict == SLIPPED
    assert marks["klokka"].heard == "klokke"
    assert marks["åtte."].verdict == SAID


def test_a_word_replaced_by_something_unrelated_is_missed():
    marks = {m.word: m for m in compare("Katten har ni liv.", "Cotten har ni liv.")}
    assert marks["Katten"].verdict == MISSED
    assert marks["Katten"].heard == "cotten"


def test_a_dropped_word_is_missed_with_nothing_heard():
    marks = {m.word: m for m in compare("Bananen er gul.", "Bananen.")}
    assert marks["er"].verdict == MISSED
    assert marks["gul."].verdict == MISSED
    assert marks["er"].heard is None


def test_an_empty_transcript_misses_everything_rather_than_crashing():
    marks = compare("Toget går om ti minutter.", "")
    assert [m.verdict for m in marks] == [MISSED] * 5


def test_an_empty_target_has_nothing_to_say():
    assert compare("", "noe som helst") == []


def test_extra_words_in_the_transcript_do_not_fail_the_target():
    marks = compare("Takk for maten!", "Å ja, takk for maten, sier han.")
    assert {m.verdict for m in marks} == {SAID}


def test_every_target_word_gets_exactly_one_mark():
    target = "Jeg lærer ti nye ord hver dag."
    assert len(compare(target, "Jeg klarer ti ny år hver dag.")) == len(words(target))


@pytest.mark.parametrize(
    "text,expected",
    [("Klokka er tre.", "klokka er tre"), ("  HA  DET!  ", "ha det"), ("", "")],
)
def test_normalise(text, expected):
    assert normalise(text) == expected


class TestIsPattern:
    def test_one_bad_attempt_is_not_a_pattern(self):
        assert not is_pattern(0, 1)

    def test_nor_are_a_few(self):
        assert not is_pattern(0, PATTERN_MIN_ATTEMPTS - 1)

    def test_failing_most_of_enough_attempts_is(self):
        # klokka: 7 said out of 22.
        assert is_pattern(7, 22)

    def test_a_word_that_usually_lands_is_not_flagged(self):
        assert not is_pattern(20, 22)

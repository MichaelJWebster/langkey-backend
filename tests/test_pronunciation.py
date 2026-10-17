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
    take_spans,
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


# --- where each word is in the take (ADR-043) ---------------------------------


def test_a_mark_remembers_which_heard_word_it_came_from():
    """Playing a word of the take back needs the take's word, not the
    target's: for klokka heard as klokke, the stretch that *was* klokke."""
    marks = compare("Møtet slutter klokka åtte.", "Møte slutter klokke åtte.")
    assert [m.heard_index for m in marks] == [0, 1, 2, 3]
    assert marks[2].verdict == SLIPPED


def test_a_word_the_model_found_nothing_for_points_at_no_heard_word():
    marks = compare("Jeg bor i Oslo.", "Jeg bor Oslo.")
    by_word = {m.word: m for m in marks}
    assert by_word["i"].verdict == MISSED
    assert by_word["i"].heard_index is None
    # The words either side still know where they are in the transcript.
    assert by_word["bor"].heard_index == 1
    assert by_word["Oslo."].heard_index == 2


def test_a_word_replaced_by_something_unrelated_still_points_at_it():
    # Wrong, but the model did put *something* there, and that something is
    # exactly what the learner should hear back.
    marks = {m.word: m for m in compare("Vi kjøper brød.", "Vi kjøper tog.")}
    assert marks["brød."].verdict == MISSED
    assert marks["brød."].heard == "tog"
    assert marks["brød."].heard_index == 2


def test_take_spans_follow_the_heard_words():
    marks = compare("Møtet slutter klokka åtte.", "Møte slutter klokke åtte.")
    heard = [(0.2, 0.6), (0.6, 1.1), (1.1, 1.6), (1.7, 2.0)]
    assert take_spans(marks, heard) == heard


def test_a_missed_word_gets_the_stretch_between_its_neighbours():
    # "i" was not heard. Whatever he said between "bor" and "Oslo" is where
    # it should have been, and that is the thing worth hearing back.
    marks = compare("Jeg bor i Oslo.", "Jeg bor Oslo.")
    heard = [(0.1, 0.4), (0.5, 0.8), (1.2, 1.7)]
    spans = take_spans(marks, heard)
    assert spans == [(0.1, 0.4), (0.5, 0.8), (0.8, 1.2), (1.2, 1.7)]


def test_a_missed_word_at_the_edge_has_nothing_to_point_at():
    marks = compare("Jeg bor i Oslo.", "bor i Oslo.")
    heard = [(0.5, 0.8), (0.8, 0.9), (1.2, 1.7)]
    spans = take_spans(marks, heard)
    assert spans[0] is None  # nothing before "bor" to bracket it
    assert spans[1:] == heard


def test_an_unplaced_heard_word_leaves_its_mark_without_a_span():
    marks = compare("Jeg bor i Oslo.", "Jeg bor i Oslo.")
    heard = [(0.1, 0.4), None, (0.8, 0.9), (1.2, 1.7)]
    spans = take_spans(marks, heard)
    # The aligner could not place "bor"; it is bracketed by its neighbours,
    # the same as a word that was never heard.
    assert spans[1] == (0.4, 0.8)

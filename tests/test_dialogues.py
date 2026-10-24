"""The conversation dialogues: the shipped file passes its gates, and the
gates catch what they exist to catch."""

import json

from scripts.check_dialogues import (
    DEFAULT,
    MAX_TURNS,
    MIN_TURNS,
    check,
    near_pair,
    paths,
)


def _doc():
    return json.loads(DEFAULT.read_text())


def test_the_shipped_dialogues_pass_every_gate():
    assert check(_doc()) == []


def test_the_dialogues_run_from_easiest_to_hardest():
    dialogues = _doc()["dialogues"]
    shortest = [min(len(p) for p in paths(d["nodes"], d["start"])) for d in dialogues]
    assert [d["level"] for d in dialogues] == sorted(d["level"] for d in dialogues)
    # The first is the shortest conversation and the last the longest.
    assert shortest[0] == min(shortest) and shortest[-1] == max(shortest)


def test_near_spellings_are_caught():
    assert near_pair("Kan du hjelpe meg?", "Kan du hjalp meg?")
    assert near_pair("Skolen begynner klokka ni.", "Skolen begynner klokke ni.")
    assert near_pair("Jeg forstår ikke.", "jeg forstår ikke")  # the same words


def test_distinct_answers_pass():
    assert near_pair("Jeg kommer fra Australia.", "Jeg kommer fra England.") is None
    assert near_pair("Inne, takk.", "Jeg vil sitte ute.") is None


def _tiny(turns: int, *, answers=None):
    nodes = {}
    for i in range(turns):
        nxt = f"n{i + 1}" if i + 1 < turns else None
        nodes[f"n{i}"] = {
            "say": "Hei?", "say_en": "Hi?",
            "answers": answers or [
                {"id": "a", "text": "Ja, takk.", "en": "Yes.", "reply": "Fint.", "reply_en": "Fine.", "next": nxt},
                {"id": "b", "text": "Nei, det går bra.", "en": "No.", "reply": "Ok.", "reply_en": "Ok.", "next": nxt},
            ],
        }
    return {
        "fixed": {"unclear": "x", "unclear_en": "x", "choice": "x", "choice_en": "x", "offscript": "x", "offscript_en": "x"},
        "dialogues": [{
            "slug": "t", "title": "T", "title_en": "T", "blurb": "b", "level": 1,
            "character": {"name": "A", "role": "r"}, "start": "n0", "nodes": nodes,
        }],
    }


def test_too_short_and_too_long_conversations_fail():
    assert check(_tiny(MIN_TURNS)) == []
    assert check(_tiny(MAX_TURNS)) == []
    assert any("turns" in p for p in check(_tiny(MIN_TURNS - 1)))
    assert any("turns" in p for p in check(_tiny(MAX_TURNS + 1)))


def test_an_answer_too_like_a_universal_one_fails():
    clash = [
        {"id": "a", "text": "Jeg forstår ikke.", "en": "x", "reply": "x", "reply_en": "x", "next": None},
    ]
    doc = _tiny(1, answers=clash)
    assert any("forstar-ikke" in p for p in check(doc))


def test_a_dangling_next_fails():
    doc = _tiny(MIN_TURNS)
    doc["dialogues"][0]["nodes"]["n0"]["answers"][0]["next"] = "nowhere"
    assert any("nowhere" in p for p in check(doc))


def test_a_slot_answer_must_match_its_own_example():
    slot = {"id": "navn", "text": "Jeg heter …", "en": "x", "reply": "x", "reply_en": "x", "next": None,
            "match": r"jeg heter \w+", "example": "Jeg heter Michael."}
    other = {"id": "a", "text": "Ja, takk.", "en": "x", "reply": "x", "reply_en": "x", "next": None}
    doc = _tiny(1, answers=[other, slot])
    assert not any("slot" in p or "match" in p for p in check(doc))
    slot["example"] = "Jeg bor i Oslo."
    assert any("does not fit its example" in p for p in check(doc))

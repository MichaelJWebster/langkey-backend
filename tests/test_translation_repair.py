"""The repair loop's acceptance rule.

The loop sends a flagged sentence back to the translator with an instruction
naming the fault. The danger is not that a repair fails -- it is that a repair
is accepted when it made things worse, because then a bad rewrite replaces a
translation nobody will look at again. So the rule is: the gate that fired
must now pass, and no gate that was passing may start failing.

The model is stubbed here. Whether gemma3 can actually fix a given sentence is
measured against the corpus (see Gate.measured), not asserted in a unit test.
"""

import pytest

from scripts import check_translation
from scripts.translation_gates import check_sentence

ARCHAIC = {"seq": 1, "no": "«Hun tager Dem ikke paa Ordet.", "en": "She doth not take you at your word."}


@pytest.fixture
def replies(monkeypatch):
    """Queue the model's replies, and record the prompts it was sent."""
    sent: list[str] = []

    def install(*answers: str):
        queue = list(answers)
        def fake_ollama(model: str, prompt: str) -> str:
            sent.append(prompt)
            return queue.pop(0) if queue else ""
        monkeypatch.setattr(check_translation, "ollama", fake_ollama)
        return sent

    return install


def finding_for(sentence: dict, gate: str):
    hits = [f for f in check_sentence(sentence["seq"], sentence["no"], sentence["en"]) if f.gate == gate]
    assert hits, f"expected {gate} to fire on the fixture"
    return hits[0]


def test_accepts_a_clean_rewrite_and_keeps_the_original(replies):
    replies("She does not take you at your word.")
    s = dict(ARCHAIC)

    assert check_translation.repair(s, finding_for(s, "archaic_english"), "m") is True
    assert s["en"] == "She does not take you at your word."
    # The first attempt is still on the record, so a review can see what the
    # translator originally produced.
    assert s["repairs"] == [
        {
            "gate": "archaic_english",
            "before": "She doth not take you at your word.",
            "after": "She does not take you at your word.",
        }
    ]


def test_the_prompt_names_the_specific_fault(replies):
    sent = replies("She does not take you at your word.")
    s = dict(ARCHAIC)
    check_translation.repair(s, finding_for(s, "archaic_english"), "m")

    prompt = sent[0]
    assert "archaic English" in prompt
    assert s["no"] in prompt  # the Norwegian, not the bad English, is the input


def test_rejects_a_rewrite_that_still_has_the_fault(replies):
    replies("She hath not taken you at thy word.", "Thou dost not take her at her word.")
    s = dict(ARCHAIC)

    assert check_translation.repair(s, finding_for(s, "archaic_english"), "m") is False
    assert s["en"] == ARCHAIC["en"]  # untouched
    assert "repairs" not in s


def test_rejects_a_rewrite_that_trades_one_fault_for_another(replies):
    # No archaic English left, but now the model is talking about its task.
    replies("Here is the translation: she does not take you at your word.")
    s = dict(ARCHAIC)

    assert check_translation.repair(s, finding_for(s, "archaic_english"), "m") is False
    assert s["en"] == ARCHAIC["en"]


def test_takes_a_second_attempt_before_giving_up(replies):
    replies("She hath not taken you at your word.", "She does not take you at your word.")
    s = dict(ARCHAIC)

    assert check_translation.repair(s, finding_for(s, "archaic_english"), "m") is True
    assert s["en"] == "She does not take you at your word."


def test_stops_after_two_attempts(replies):
    sent = replies(*["She hath not."] * 5)
    s = dict(ARCHAIC)
    check_translation.repair(s, finding_for(s, "archaic_english"), "m")

    assert len(sent) == check_translation.MAX_ATTEMPTS == 2


def test_an_empty_reply_is_not_a_repair(replies):
    replies("", "")
    s = dict(ARCHAIC)

    assert check_translation.repair(s, finding_for(s, "archaic_english"), "m") is False
    assert s["en"] == ARCHAIC["en"]


def test_a_fault_in_the_norwegian_does_not_block_an_otherwise_good_repair(replies):
    # The source has a page number fused into it, so digit_mismatch fires no
    # matter what the translator returns. Counting that against the candidate
    # would make this sentence unrepairable.
    s = {"seq": 1, "no": "Nej, jeg36var heldig, doth han sige.", "en": "No, I doth was lucky, he says."}
    replies("No, I was lucky, he says.")

    assert check_translation.repair(s, finding_for(s, "archaic_english"), "m") is True
    assert s["en"] == "No, I was lucky, he says."


def test_run_repairs_leaves_source_side_findings_alone(replies, capsys):
    sent = replies(*["She does not take you at your word."] * 4)
    sentences = [
        dict(ARCHAIC),
        {"seq": 2, "no": "Nej, jeg36var heldig!", "en": "No, I was lucky!"},
    ]
    from scripts.translation_gates import check_all

    fixed = check_translation.run_repairs(sentences, check_all(sentences), "m")

    assert fixed == {"archaic_english": 1}
    # One prompt only: the source-side finding was never sent to the model.
    assert len(sent) == 1
    assert "1 source-side" in capsys.readouterr().out

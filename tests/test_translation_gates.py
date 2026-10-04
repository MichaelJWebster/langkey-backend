"""The deterministic translation gates, pinned to real corpus cases.

Every string here is either something the gates found in Mysterier (7228
sentences) or something they wrongly flagged there. The must-not-flag cases
matter more than the must-flag ones: a gate with repair enabled rewrites what
it flags, so a false positive does not merely waste a review, it replaces a
correct translation with a worse one. `ten øre` became `ten pence` that way.
"""

import pytest

from scripts.translation_gates import (
    BY_NAME,
    GATES,
    SOURCE,
    TRANSLATION,
    check_all,
    check_sentence,
)


def gates_hit(no: str, en: str) -> set[str]:
    return {f.gate for f in check_sentence(1, no, en)}


# --- the gate set itself ---------------------------------------------------


def test_only_translation_side_gates_can_be_repaired():
    # A SOURCE gate fires on a fault in the Norwegian. Asking the translator to
    # try again cannot clear it, and the loop would burn two attempts per
    # sentence discovering that.
    for gate in GATES:
        if gate.side == SOURCE:
            assert gate.instruction is None, gate.name
            assert not gate.repairable, gate.name


def test_every_gate_records_what_it_scored():
    # The rule is that repair is only wired to a gate whose precision has been
    # measured. If this is empty the rule has been skipped.
    for gate in GATES:
        assert gate.measured != "not yet measured", gate.name
        assert gate.side in (TRANSLATION, SOURCE), gate.name


def test_a_clean_sentence_trips_nothing():
    assert gates_hit("Jeg bor i Oslo.", "I live in Oslo.") == set()


# --- must flag: cases the gates actually caught ----------------------------


@pytest.mark.parametrize(
    "gate,no,en",
    [
        # #4101 and #6696: the model matched the 19th-century register.
        ("archaic_english", "«Hun tager Dem ikke paa Ordet.", "She doth not take you at your word."),
        ("archaic_english", "«De synes naturligvis, at jeg er en grov Bandit",
         "«Do ye think it natural, that I am a rough Bandit"),
        # #620: print page number 40 carried into the English.
        ("leading_number", "Pause.", "40. Pause."),
        # #2204: the Norwegian echoed back, then translated.
        ("too_long", "Midt i denne Passiar dumped Doktoren ind.",
         "150Midt i denne Passiar dumped Doktoren ind. - Right in the middle of this "
         "passage, the Doctor dropped in on us without any warning at all"),
        # #551: a page number fused into a word, which glued_number cannot see.
        ("digit_mismatch", "Nej, svarer jeg, jeg36var lidt heldig endda!",
         "No, I answer, I was a little lucky still!"),
        # #1585: the Norwegian left in, then translated after it.
        ("norwegian_left", "«Ja, jeg skal saa gærne sluddre væk.",
         "«Ja, jeg skal saa gærne sluddre væk.» – “Yes, I shall very gladly chatter away.”"),
        ("empty", "Jeg bor i Oslo.", "   "),
    ],
)
def test_flags_real_faults(gate, no, en):
    assert gate in gates_hit(no, en)


def test_glued_number_catches_a_page_marker_before_a_quotation():
    # The first version of this check required a letter after the digit, so
    # `9«Naa` slipped through and 77 more sentences had to be patched later.
    assert "glued_number" in gates_hit("Og 9«Naa skal De høre», sagde han.", "And “Now you shall hear”, he said.")


def test_commentary_catches_the_model_talking_about_its_task():
    assert "commentary" in gates_hit("Jeg bor i Oslo.", "Here is the translation: I live in Oslo.")
    assert "commentary" in gates_hit("Jeg bor i Oslo.", "I cannot translate this sentence.")
    assert "commentary" in gates_hit(
        "Midt i denne Passiar.", "In the middle of this chat. (Note: Passiar is dated.)"
    )


# --- must not flag: the false positives that were measured -----------------


@pytest.mark.parametrize(
    "no,en",
    [
        # #2447, #5215, #5511, #6526, #6731 -- first-person narration, not a
        # refusal. The loose commentary check was wrong on all five.
        ("«Jeg kan ikke begribe, hvorfor De gaar her og vrænger Dem ud og ind for mig!",
         "I cannot comprehend why you are here, wriggling yourself in and out before me!"),
        ("Jeg kan ikke sige noget mer …»", "I cannot say anything more…"),
        ("«Jeg kan ikke modtage det.»", "I cannot receive it."),
        # #6676 -- `Læg Mærke til:` is "Note:".
        ("Læg Mærke til: jeg havde fra Begyndelsen ingen Grund til at tro andet.",
         "Note: I had from the beginning no reason to believe otherwise."),
    ],
)
def test_narration_is_not_commentary(no, en):
    assert "commentary" not in gates_hit(no, en)


@pytest.mark.parametrize(
    "no,en",
    [
        # Currency and toasts are kept deliberately. Repairing these is what
        # produced `ten pence` and `Cheers!`.
        ("Det kostede ti øre.", "It cost ten øre."),
        ("«Skaal!» sagde han.", "«Skål!» he said."),
        # A place name is not untranslated Norwegian.
        ("paa Vejen op fra Gjøvik.", "on the road up from Gjøvik."),
    ],
)
def test_a_single_kept_norwegian_word_is_not_a_fault(no, en):
    assert "norwegian_left" not in gates_hit(no, en)


@pytest.mark.parametrize(
    "no",
    [
        # Dano-Norwegian ordinals look exactly like a glued page number.
        "Det var den 12te Juni.",
        "Han kom den 3dje Gang.",
        "paa den 7de Dag.",
    ],
)
def test_ordinals_are_not_page_markers(no):
    assert "glued_number" not in gates_hit(no, "irrelevant")


def test_a_number_kept_before_closing_punctuation_survives():
    # `150»` is a legitimate number inside the sentence, not pagination; only
    # a digit against *opening* punctuation or a letter is suspect.
    assert "glued_number" not in gates_hit("Han sagde «150», og gik.", "He said “150”, and left.")


def test_terse_english_is_not_a_dropped_sentence():
    no = "Og saa gik han sin Vej uden at sige et eneste Ord til nogen af os."
    assert "too_short" not in gates_hit(no, "And then he left without a word to any of us.")


def test_numbers_written_out_in_english_are_not_a_mismatch():
    assert "digit_mismatch" in gates_hit("Han var 20 Aar.", "He was twenty years old.")


# --- the repair contract ---------------------------------------------------


def test_check_all_reports_the_sequence_number():
    sentences = [
        {"seq": 7, "no": "Pause.", "en": "40. Pause."},
        {"seq": 8, "no": "Jeg bor i Oslo.", "en": "I live in Oslo."},
    ]
    findings = check_all(sentences)
    assert [(f.seq, f.gate) for f in findings] == [(7, "leading_number")]
    assert findings[0].repairable


def test_a_sentence_missing_its_translation_is_a_finding_not_a_crash():
    assert [f.gate for f in check_all([{"seq": 1, "no": "Pause.", "en": ""}])] == ["empty"]


# --- the import refusal ----------------------------------------------------


def test_import_refuses_a_file_with_fixable_findings(tmp_path):
    from scripts.import_text import check_quality

    f = tmp_path / "x.json"
    with pytest.raises(SystemExit) as e:
        check_quality(f, [{"seq": 1, "no": "Pause.", "en": "40. Pause."}], force=False)
    assert "check_translation" in str(e.value)


def test_import_allows_fixable_findings_through_with_force(tmp_path):
    from scripts.import_text import check_quality

    check_quality(tmp_path / "x.json", [{"seq": 1, "no": "Pause.", "en": "40. Pause."}], force=True)


def test_import_does_not_block_on_a_fault_in_the_norwegian(tmp_path):
    # Retranslating cannot fix a page number fused into the source, so
    # blocking here would only teach you to pass --force by reflex.
    from scripts.import_text import check_quality

    check_quality(
        tmp_path / "x.json",
        [{"seq": 1, "no": "jeg36var heldig", "en": "I was lucky"}],
        force=False,
    )


# --- the echo gate ---------------------------------------------------------


def test_the_norwegian_handed_back_is_a_finding():
    # The real case: a repair answered with the source sentence. No other gate
    # could see it -- the word counts match, so too_long passes, and the
    # sentence contains no æ, ø or å, so norwegian_left passes too.
    no = "Midt i denne Passiar dumped Doktoren ind."
    assert "echoed_source" in gates_hit(no, no)
    assert gates_hit(no, "Right in the middle of this chat, the Doctor dropped in.") == set()


@pytest.mark.parametrize(
    "line", ["Pause.", "Sara.", "He-he-he.", "«Stop!", "Dagny Kielland."]
)
def test_a_short_line_that_is_its_own_translation_is_not_an_echo(line):
    # 80 of these in Mysterier, every one correct. They are why the check needs
    # a floor on length rather than a lower overlap threshold.
    assert "echoed_source" not in gates_hit(line, line.strip("«»"))

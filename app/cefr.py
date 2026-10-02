"""CEFR (A1–C2) progress estimation.

LangKey can't award a CEFR level — that takes an official test (Norskprøven,
prove.hkdir.no) — but it can estimate where a learner sits per skill. Each
skill's activity is converted into an "effective vocabulary" and read against
the receptive vocabulary sizes research associates with each level (Milton &
Alexiou's X-Lex studies; approximate midpoints, in lemmas). The CEFR's own
descriptors are can-do statements, not word counts, so this is a progress
gauge rather than a certification.
"""

LEVELS: list[tuple[str, int]] = [
    ("A1", 500),
    ("A2", 1200),
    ("B1", 2750),
    ("B2", 4500),
    ("C1", 6750),
    ("C2", 9000),
]


def estimate(effective: float) -> tuple[str | None, str | None, float]:
    """Map an effective vocabulary onto the ladder.

    Returns (level cleared, next level, progress toward the next level 0..1).
    Below A1 the level is None ("pre-A1"); at or past C2 the next is None.
    """
    cleared: str | None = None
    prev = 0
    for name, threshold in LEVELS:
        if effective >= threshold:
            cleared, prev = name, threshold
            continue
        return cleared, name, max(0.0, effective - prev) / (threshold - prev)
    return cleared, None, 1.0

"""Check a prepared translation, and optionally ask the model to repair it.

Runs the deterministic gates in scripts/translation_gates.py over a staging
JSON. With --repair it sends only the repairable findings back to the
translator with an instruction naming the specific fault, re-runs the same
gate on the reply, and keeps the new translation only if the fault is gone
and nothing new has appeared.

Nothing is rewritten silently: every accepted repair is recorded on the
sentence as `repairs`, so the staging file still shows what the model first
produced when you come to review it.

Usage:
    uv run python -m scripts.check_translation data/texts_staging/x.json
    uv run python -m scripts.check_translation data/texts_staging/x.json --repair
    uv run python -m scripts.check_translation data/texts_staging/x.json --show norwegian_left

Exit status is 1 while findings remain, so this can gate an import.
"""

import argparse
import collections
import json
import sys
from pathlib import Path

from scripts.prepare_text import DEFAULT_MODEL, ollama
from scripts.translation_gates import BY_NAME, GATES, SOURCE, Finding, check_all, check_sentence

MAX_ATTEMPTS = 2

REPAIR_PROMPT = (
    "{instruction}\n\n"
    "The Norwegian may use 19th-century Dano-Norwegian spelling. Reply with "
    "only the English translation of this sentence, nothing else.\n\n"
    "Norwegian: {no}\n"
)


def _gates_failing(sentence: dict, en: str) -> set[str]:
    return {f.gate for f in check_sentence(sentence["seq"], sentence["no"], en)}


def repair(sentence: dict, finding: Finding, model: str) -> bool:
    """Ask the model to fix one finding. True if a replacement was accepted."""
    gate = BY_NAME[finding.gate]
    before = sentence.get("en", "")
    # Faults that are in the Norwegian cannot be cleared by retranslating, so
    # they must not count against a candidate. Anything outside this set that
    # a candidate introduces is a fault the repair itself caused.
    pre_existing = _gates_failing(sentence, before)
    prompt = REPAIR_PROMPT.format(instruction=gate.instruction, no=sentence["no"])

    for _ in range(MAX_ATTEMPTS):
        candidate = ollama(model, prompt).strip()
        if not candidate or candidate == before:
            continue
        failing = _gates_failing(sentence, candidate)
        if finding.gate in failing:
            continue  # the fault we asked about is still there
        if failing - pre_existing:
            continue  # traded one fault for another, which is not a repair
        sentence["en"] = candidate
        sentence.setdefault("repairs", []).append(
            {"gate": finding.gate, "before": before, "after": candidate}
        )
        return True
    return False


def run_repairs(sentences: list[dict], findings: list[Finding], model: str) -> collections.Counter:
    todo = [f for f in findings if f.repairable]
    source_side = len(findings) - len(todo)
    print(f"repairing {len(todo)} translation-side finding{'' if len(todo) == 1 else 's'}"
          f" ({source_side} source-side, which no retranslation can fix)")
    by_seq = {s["seq"]: s for s in sentences}
    fixed: collections.Counter = collections.Counter()
    for i, f in enumerate(todo, 1):
        if repair(by_seq[f.seq], f, model):
            fixed[f.gate] += 1
        print(f"\r  {i}/{len(todo)} repaired {sum(fixed.values())}   ", end="", flush=True)
    print()
    return fixed


def report(findings: list[Finding], fixed: collections.Counter, examples: int) -> None:
    counts = collections.Counter(f.gate for f in findings)
    by_gate: dict[str, list[Finding]] = collections.defaultdict(list)
    for f in findings:
        by_gate[f.gate].append(f)

    print(f"\n{'gate':18} {'side':12} {'left':>5} {'fixed':>6}  what it means")
    for gate in GATES:
        if not counts[gate.name] and not fixed[gate.name]:
            continue
        print(f"{gate.name:18} {gate.side:12} {counts[gate.name]:5} "
              f"{fixed[gate.name] or '':>6}  {gate.summary}")

    for gate in GATES:
        shown = by_gate.get(gate.name, [])[:examples]
        if not shown:
            continue
        print(f"\n--- {gate.name} ({counts[gate.name]}) ---")
        for f in shown:
            print(f"  #{f.seq} {f.detail}")
            print(f"    NO: {f.no[:95]}")
            print(f"    EN: {f.en[:95]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path)
    ap.add_argument("--repair", action="store_true", help="ask the model to fix what it can")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--show", help="print every finding for this gate and stop")
    ap.add_argument("--examples", type=int, default=2, help="examples per gate (default 2)")
    args = ap.parse_args()

    if args.show and args.show not in BY_NAME:
        print(f"no such gate: {args.show}\nknown: {', '.join(BY_NAME)}", file=sys.stderr)
        return 2

    data = json.loads(args.file.read_text())
    sentences = data["sentences"]
    findings = check_all(sentences)
    print(f"{args.file.name}: {len(sentences)} sentences, {len(findings)} findings")

    if args.show:
        hits = [f for f in findings if f.gate == args.show]
        print(f"\n{len(hits)} × {args.show} — {BY_NAME[args.show].summary}\n")
        for f in hits:
            print(f"  #{f.seq} {f.detail}")
            print(f"    NO: {f.no[:100]}")
            print(f"    EN: {f.en[:100]}")
        return 1 if hits else 0

    fixed: collections.Counter = collections.Counter()
    if args.repair and findings:
        fixed = run_repairs(sentences, findings, args.model)
        if sum(fixed.values()):
            args.file.write_text(json.dumps(data, ensure_ascii=False, indent=1))
            print(f"rewrote {args.file}")
        findings = check_all(sentences)

    report(findings, fixed, args.examples)

    if not findings:
        print("\nclean")
        return 0
    if all(BY_NAME[f.gate].side == SOURCE for f in findings):
        print("\nEverything left is source-side: the Norwegian itself is wrong, so the")
        print("fix belongs in extraction (scripts/prepare_text.py), not in translation.")
    print("\nUse --show <gate> to see every case.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

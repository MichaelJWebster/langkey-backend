"""Flag clips whose opening word Piper swallowed.

Piper sometimes under-articulates the first word of a sentence: it comes out
quiet next to the syllable that follows, which sounds to the ear like the clip
was cut off at the start. The audio is intact -- the word is mumbled -- so
this cannot be found by looking at leading silence or file length. The clip
that prompted this, "Jeg kjoper frukt hver uke." (nvcc:0), has 360ms of
leading silence and a "Jeg" at half the level of the "kjo" after it.

Measuring it is fiddlier than it looks. Trying to isolate the first word by
its energy burst does not work: a word is not one unbroken burst, because /s/
in "smor" and /j/ in "Jeg" are quiet consonants with a dip before the vowel,
so the measurement stops early and reports most fricative-initial words as
swallowed. Bridging those dips does not work either -- gaps between words in
connected speech are frequently shorter than the dips inside a word, so the
measurement runs on and swallows the whole sentence.

So no segmentation: measure the loudest moment in a fixed window from the
start of speech, against the loudest moment in the clip. A healthy opening
word reaches most of the clip's level within 200ms; a swallowed one does not.

Run it after generating audio, so new content does not add more of them:

    uv run python -m scripts.check_audio_onsets              # check
    uv run python -m scripts.check_audio_onsets --update     # record findings

Exit status is 1 when a clip is weak and not already recorded in
data/audio_onset_issues.json, which is what makes this usable as a gate. The
recorded entries are a to-do list for the existing corpus; nothing here
regenerates or re-voices them.
"""

import argparse
import io
import json
import math
import struct
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from app.audio import LENGTH_SCALE, audio_object_key, voice_for_text
from app.config import get_settings
from app.content import _s3_client, load_deck

ISSUES_PATH = Path(__file__).parent.parent / "data" / "audio_onset_issues.json"

FRAME_MS = 20
SPEECH_ON = 0.15  # frame level, relative to the clip peak, that counts as speech
ONSET_WINDOW_MS = 200  # how long the opening word has to reach its level
# Calibrated on 131 sentences that all open with "Jeg": median 83.8%, and the
# strongest are talesyntese at 100% while the weakest are all pool voices at
# 40-55%. The same word in the same position reaching half the level is a
# synthesis difference, not prosody. The reported clip sits at 55%.
ONSET_FLOOR = 0.60


def onset_strength(data: bytes) -> float | None:
    """Loudest 20ms in the first ONSET_WINDOW_MS of speech, over the clip peak."""
    with wave.open(io.BytesIO(data)) as w:
        rate, frames = w.getframerate(), w.getnframes()
        samples = struct.unpack(f"<{frames}h", w.readframes(frames))

    size = int(rate * FRAME_MS / 1000)
    envelope = [
        math.sqrt(sum(s * s for s in samples[i : i + size]) / size)
        for i in range(0, frames - size, size)
    ]
    if not envelope:
        return None
    loudest = max(envelope) or 1.0
    envelope = [e / loudest for e in envelope]

    start = next((i for i, e in enumerate(envelope) if e > SPEECH_ON), None)
    if start is None:
        return None  # silence throughout; a different problem
    return max(envelope[start : start + ONSET_WINDOW_MS // FRAME_MS])


def deck_clips() -> list[tuple[str, bool]]:
    """(text, primary) for the example-sentence clips.

    Headwords are deliberately excluded. Many are an unstressed article or
    infinitive marker before a stressed word -- "et hus", "aa ville" -- where a
    quiet opening is correct Norwegian prosody rather than a defect, so the
    measure flags them without anything being wrong. The dictation ladder is
    excluded too: same text, same primary voice, other speeds.
    """
    entries, _ = load_deck()
    clips: dict[tuple[str, bool], None] = {}
    for entry in entries:
        for example in entry["examples"]:
            clips.setdefault((example["no"], False), None)
    return list(clips)


def load_issues() -> dict:
    if not ISSUES_PATH.exists():
        return {"clips": []}
    return json.loads(ISSUES_PATH.read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update", action="store_true", help="record what is found as the known-issue list"
    )
    parser.add_argument("--limit", type=int, help="check only the first N clips (quick run)")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    clips = deck_clips()
    if args.limit:
        clips = clips[: args.limit]
    bucket = get_settings().s3_bucket
    client = _s3_client()

    def measure(clip: tuple[str, bool]):
        text, primary = clip
        key = audio_object_key("nb", text, primary)
        try:
            data = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        except Exception:
            return None  # not generated yet; the tts run will make it
        strength = onset_strength(data)
        if strength is None:
            return None
        return {
            "text": text,
            "voice": voice_for_text(text, primary),
            "primary": primary,
            "scale": LENGTH_SCALE,
            "key": key,
            "onset_strength": round(strength, 3),
        }

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        measured = [m for m in pool.map(measure, clips) if m]

    weak = sorted(
        (m for m in measured if m["onset_strength"] < ONSET_FLOOR),
        key=lambda m: m["onset_strength"],
    )
    weak_keys = {m["key"] for m in weak}
    known_keys = {c["key"] for c in load_issues().get("clips", [])}
    fresh = [w for w in weak if w["key"] not in known_keys]

    print(f"measured {len(measured)} of {len(clips)} clips")
    print(f"swallowed opening: {len(weak)} ({len(weak) / max(len(measured), 1) * 100:.1f}%)")

    by_voice: dict[str, list[int]] = {}
    for m in measured:
        tally = by_voice.setdefault(m["voice"], [0, 0])
        tally[1] += 1
        if m["key"] in weak_keys:
            tally[0] += 1
    print("\nby voice:")
    for voice, (bad, total) in sorted(by_voice.items(), key=lambda kv: -kv[1][0] / kv[1][1]):
        print(f"  {voice:12} {bad:3}/{total:3} = {bad / total * 100:5.1f}%")

    if args.update:
        ISSUES_PATH.write_text(
            json.dumps(
                {
                    "note": (
                        "Clips whose opening word Piper swallowed. The audio is NOT "
                        "truncated -- the word is mumbled, quiet against the rest of "
                        "the clip. Recorded as a to-do for the existing corpus; fixing "
                        "means regenerating them on a different voice. Refresh with: "
                        "uv run python -m scripts.check_audio_onsets --update"
                    ),
                    "checked": date.today().isoformat(),
                    "metric": (
                        f"loudest 20ms within {ONSET_WINDOW_MS}ms of speech onset, over "
                        f"the clip peak; flagged below {ONSET_FLOOR}"
                    ),
                    "clips": weak,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        print(f"\nrecorded {len(weak)} clips in data/audio_onset_issues.json")
        return 0

    if fresh:
        print(f"\n{len(fresh)} NEW weak clip(s) not in the known-issue list:")
        for clip in fresh:
            print(
                f"  {clip['onset_strength'] * 100:5.1f}%  {clip['voice']:12} {clip['text'][:50]}"
            )
        print("\nReword the sentence, or accept and re-record the list with --update.")
        return 1

    print(f"\nno new weak clips ({len(known_keys)} already known)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

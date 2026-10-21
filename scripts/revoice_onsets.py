"""Re-voice the clips whose opening word Piper swallowed (ADR-028, ADR-049).

For each example clip the onset gate flags, synthesise the sentence on the
other pool voices, in an order fixed per sentence, and keep the first whose
opening reaches PICK_FLOOR -- a clear margin over the gate's ONSET_FLOOR, so a
re-voiced clip does not sit on the line. The choice goes into
data/voice_overrides.json, which app/audio.py and the generator both read, and
the clip is uploaded under its new key with its leading silence capped
(ADR-048). The old clip is left for the generator to prune.

Needs Piper, which the app does not depend on, and the voice models (copied
out of the langkey-tts image):

    uv run --with piper-tts python -m scripts.revoice_onsets --dry-run
    uv run --with piper-tts python -m scripts.revoice_onsets
    uv run python -m scripts.check_audio_onsets --update   # then re-record the gate
"""

import argparse
import hashlib
import io
import json
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from piper import PiperVoice, SynthesisConfig

from app.audio import (
    LENGTH_SCALE,
    VOICE_OVERRIDES,
    VOICE_OVERRIDES_PATH,
    VOICE_POOL,
    audio_object_key,
    trim_leading_silence,
    voice_for_text,
)
from app.config import get_settings
from app.content import _s3_client
from scripts.check_audio_onsets import ONSET_FLOOR, deck_clips, onset_strength

PICK_FLOOR = 0.75
# Never picked as a replacement: a third of its clips swallow the opening,
# three times any other speaker (ADR-028 named it for pruning).
AVOID = {"nvcc:4"}
VOICES_DIR = "/Volumes/nvm_2tb/home/michaelw/work/piper-voices"


def candidates(text: str, current: str) -> list[str]:
    """The other pool voices, in an order of their own for each sentence, so
    re-voiced clips spread across speakers instead of all landing on one."""
    pool = [v for v in VOICE_POOL if v != current and v not in AVOID]
    return sorted(pool, key=lambda v: hashlib.sha256(f"revoice|{text}|{v}".encode()).hexdigest())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voices", default=VOICES_DIR, help="directory holding the .onnx models")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    bucket = get_settings().s3_bucket
    client = _s3_client()

    def measure(text: str) -> float | None:
        key = audio_object_key("nb", text)
        try:
            return onset_strength(client.get_object(Bucket=bucket, Key=key)["Body"].read())
        except Exception:
            return None  # not generated yet

    existing: set[str] = set()
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="audio/nb/"):
        existing.update(o["Key"] for o in page.get("Contents", []))

    texts = [t for t, primary in deck_clips() if not primary]
    with ThreadPoolExecutor(16) as pool:
        strengths = dict(zip(texts, pool.map(measure, texts)))
    weak = sorted(t for t, s in strengths.items() if s is not None and s < ONSET_FLOOR)
    print(f"{len(weak)} of {len(texts)} example clips open too weakly")

    models = {
        "talesyntese": PiperVoice.load(f"{args.voices}/no_NO-talesyntese-medium.onnx"),
        "nvcc": PiperVoice.load(f"{args.voices}/no_NO-nvcc-medium.onnx"),
    }

    def synthesise(text: str, voice: str) -> bytes:
        model, _, speaker = voice.partition(":")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            models[model].synthesize_wav(
                text,
                w,
                syn_config=SynthesisConfig(
                    length_scale=LENGTH_SCALE, speaker_id=int(speaker) if speaker else None
                ),
            )
        return trim_leading_silence(buf.getvalue())[0]

    overrides = (
        json.loads(VOICE_OVERRIDES_PATH.read_text())
        if VOICE_OVERRIDES_PATH.exists()
        else {"voices": {}, "details": {}}
    )
    unfixed = []
    for i, text in enumerate(weak, 1):
        current = voice_for_text(text)
        best: tuple[float, str, bytes] | None = None
        for voice in candidates(text, current):
            clip = synthesise(text, voice)
            s = onset_strength(clip) or 0.0
            if best is None or s > best[0]:
                best = (s, voice, clip)
            if s >= PICK_FLOOR:
                break
        if best is None or best[0] < ONSET_FLOOR:
            unfixed.append((text, current, best[0] if best else None))
            continue
        strength, voice, clip = best
        print(f"  {i:3}/{len(weak)}  {current:11} {strengths[text]:.2f} -> {voice:11} {strength:.2f}  {text}")
        overrides["voices"][text] = voice
        overrides["details"][text] = {
            "was": current,
            "onset_was": round(strengths[text], 3),
            "onset": round(strength, 3),
            "date": date.today().isoformat(),
        }
        if not args.dry_run:
            # The key derives from the override, so it must be in effect first.
            VOICE_OVERRIDES[text] = voice
            key = audio_object_key("nb", text)
            # Re-voiced onto the primary voice, the clip is the copy ladder's
            # 1.6 rung, which already exists and may have a cached alignment
            # (ADR-043): keep it rather than overwrite it with a new take.
            if key not in existing:
                client.put_object(Bucket=bucket, Key=key, Body=clip, ContentType="audio/wav")

    for text, voice, s in unfixed:
        print(f"  NOT FIXED  {voice:11} best {s}  {text}")
    print(f"re-voiced {len(weak) - len(unfixed)}, not fixed {len(unfixed)}")
    if not args.dry_run:
        overrides["note"] = (
            "Example sentences re-voiced because the hash-picked pool voice swallowed the "
            "first word (ADR-028, ADR-049). Written by scripts/revoice_onsets.py; read by "
            "app/audio.py and docker/piper/generate_audio.py."
        )
        overrides = {"note": overrides.pop("note"), **overrides}
        VOICE_OVERRIDES_PATH.write_text(json.dumps(overrides, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()

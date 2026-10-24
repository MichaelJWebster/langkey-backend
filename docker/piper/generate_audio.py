"""Generate Piper TTS audio for every deck text missing from MinIO.

Reads the published deck Parquet, derives content-addressed keys (must match
app/audio.py — voice + length_scale are part of the hash), synthesises missing
clips, uploads them, and prunes clips no longer referenced. Idempotent.

Voices: headwords use the primary single-speaker voice; example sentences get
a stable per-sentence pick from the pool (primary + 10 nvcc speakers).
"""

import hashlib
import io
import json
import os
import tempfile
import wave
from array import array

import boto3
import duckdb
from piper import PiperVoice, SynthesisConfig

LANGUAGE = os.environ.get("LANGKEY_LANGUAGE", "nb")
BUCKET = os.environ.get("LANGKEY_S3_BUCKET", "langkey-content")

# Calibrated: Piper's length_scale is sub-linear on these voices (keep in
# sync with app/audio.py).
LENGTH_SCALE = 1.6
DICTATION_SCALES = [4.0, 3.4, 2.8, 2.2, 1.6]  # dictation ladder = imitation ladder (ADR-046)
PRONUNCIATION_SCALES = [4.0, 3.4, 2.8, 2.2, 1.6]  # imitation ladder, primary voice (ADR-033)
# Leading silence is capped at 0.5 s and the key carries the format (keep in
# sync with app/audio.py, which explains both).
LEADING_GAP_MAX = 0.5
SPEECH_DBFS = -45
CLIP_FORMAT = "lead0.5"
PRIMARY_VOICE = "talesyntese"
# Conversation characters' ladder and default rung (keep in sync with
# app/audio.py, which explains the 2.5).
CONVERSATION_SCALES = [4.0, 3.4, 2.5, 1.6]
CONVERSATION_SCALE = 2.5
# A conversation clip whose opening is this weak is synthesised again (ADR-028
# measure; Piper's output varies run to run), keeping the strongest attempt.
ONSET_FLOOR = 0.60
ONSET_TRIES = 8
VOICE_POOL = [PRIMARY_VOICE] + [f"nvcc:{n}" for n in range(10)]

MODELS = {
    "talesyntese": "/voices/no_NO-talesyntese-medium.onnx",
    "nvcc": "/voices/no_NO-nvcc-medium.onnx",
}

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("LANGKEY_S3_ENDPOINT_URL", "http://minio:9000"),
    aws_access_key_id=os.environ.get("LANGKEY_S3_ACCESS_KEY", "langkey"),
    aws_secret_access_key=os.environ.get("LANGKEY_S3_SECRET_KEY", "langkey-dev"),
    region_name=os.environ.get("LANGKEY_S3_REGION", "auto"),  # R2 needs "auto"
)


# Per-sentence re-voicing (app/audio.py, ADR-049), mounted from the backend's data/.
OVERRIDES_PATH = os.environ.get("LANGKEY_VOICE_OVERRIDES", "/data/voice_overrides.json")
VOICE_OVERRIDES: dict[str, str] = (
    json.load(open(OVERRIDES_PATH))["voices"] if os.path.exists(OVERRIDES_PATH) else {}
)


def voice_for_text(text: str, primary: bool = False) -> str:
    if primary:
        return PRIMARY_VOICE
    if text.strip() in VOICE_OVERRIDES:
        return VOICE_OVERRIDES[text.strip()]
    pick = int(hashlib.sha256(f"voice|{text.strip()}".encode()).hexdigest()[:8], 16)
    return VOICE_POOL[pick % len(VOICE_POOL)]


def audio_object_key(
    language: str, text: str, primary: bool = False, scale: float = LENGTH_SCALE, voice: str | None = None
) -> str:
    voice = voice or voice_for_text(text, primary)
    digest = hashlib.sha256(f"{voice}|{scale}|{text.strip()}|{CLIP_FORMAT}".encode()).hexdigest()[:20]
    return f"audio/{language}/{digest}.wav"


def trim_leading_silence(data: bytes, keep: float = LEADING_GAP_MAX) -> tuple[bytes, float]:
    """Cut a WAV's leading silence down to `keep` seconds (as app/audio.py)."""
    with wave.open(io.BytesIO(data)) as w:
        params = w.getparams()
        samples = array("h", w.readframes(w.getnframes()))
    rate = params.framerate
    size = int(rate * 0.02)
    threshold = 32768 * 10 ** (SPEECH_DBFS / 20)
    onset = next(
        (
            i
            for i in range(0, len(samples) - size + 1, size)
            if (sum(x * x for x in samples[i : i + size]) / size) ** 0.5 > threshold
        ),
        None,
    )
    cut = 0 if onset is None else max(0, onset - int(keep * rate))
    if cut == 0:
        return data, 0.0
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(samples[cut:].tobytes())
    return out.getvalue(), cut / rate


def deck_variants() -> dict[tuple[str, bool, float], None]:
    """(text, primary, scale) variants.

    Headwords get the normal primary clip; example sentences get a pool-voice
    flashcard clip and the primary-voice dictation ladder. Both then get the
    whole imitation ladder, which overlaps the dictation scales by coincidence
    rather than by design -- the dict below deduplicates the overlap.
    """
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        s3.download_fileobj(BUCKET, f"decks/{LANGUAGE}.parquet", tmp)
        tmp.flush()
        rows = duckdb.sql(f"SELECT no, examples FROM read_parquet('{tmp.name}')").fetchall()
    variants: dict[tuple[str, bool, float], None] = {}
    for word, examples in rows:
        variants[(word, True, LENGTH_SCALE)] = None
        for scale in PRONUNCIATION_SCALES:
            variants.setdefault((word, True, scale), None)
        for ex in examples:
            variants.setdefault((ex["no"], False, LENGTH_SCALE), None)
            for scale in DICTATION_SCALES:
                variants.setdefault((ex["no"], True, scale), None)
            for scale in PRONUNCIATION_SCALES:
                variants.setdefault((ex["no"], True, scale), None)
    return variants


def dialogue_clips() -> set[tuple[str, str, float]]:
    """(text, voice, scale) for every line a conversation character speaks --
    each node's line, each answer's reply, and the fixed "sorry?" lines in
    every character's voice -- on the conversation ladder. Read from the
    mounted data/, not the deck."""
    path = f"/data/dialogues_{LANGUAGE}.json"
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        doc = json.load(f)
    fixed = [doc.get("fixed", {}).get(k) for k in ("unclear", "choice", "offscript")]
    clips: set[tuple[str, str, float]] = set()
    for d in doc.get("dialogues", []):
        voice = d["character"].get("voice", PRIMARY_VOICE)
        texts = list(fixed)
        for node in d["nodes"].values():
            texts.append(node["say"])
            texts += [a.get("reply") for a in node["answers"]]
        for text in texts:
            if text:
                clips.update((text, voice, scale) for scale in CONVERSATION_SCALES)
    return clips


def onset_strength(data: bytes) -> float:
    """Loudest 20 ms in the first 200 ms of speech, over the clip peak -- the
    measure in scripts/check_audio_onsets.py (keep in sync)."""
    with wave.open(io.BytesIO(data)) as w:
        rate, frames = w.getframerate(), w.getnframes()
        samples = array("h", w.readframes(frames))
    size = int(rate * 0.02)
    env = [
        (sum(v * v for v in samples[i : i + size]) / size) ** 0.5
        for i in range(0, frames - size, size)
    ]
    if not env:
        return 1.0
    peak = max(env) or 1.0
    env = [e / peak for e in env]
    start = next((i for i, e in enumerate(env) if e > 0.15), None)
    return 1.0 if start is None else max(env[start : start + 10])


def existing_keys() -> set[str]:
    keys: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"audio/{LANGUAGE}/"):
        keys.update(obj["Key"] for obj in page.get("Contents", []))
    return keys


def main() -> None:
    # key -> (text, voice, scale, retry a weak opening)
    expected: dict[str, tuple[str, str, float, bool]] = {}
    variants = deck_variants()
    for t, primary, scale in variants:
        expected[audio_object_key(LANGUAGE, t, primary, scale)] = (t, voice_for_text(t, primary), scale, False)
    conversation = dialogue_clips()
    for t, voice, scale in conversation:
        expected[audio_object_key(LANGUAGE, t, scale=scale, voice=voice)] = (t, voice, scale, True)
    print(f"{len(conversation)} conversation clips", flush=True)

    have = existing_keys()
    todo = sorted((key, *expected[key]) for key in expected.keys() - have)
    stale = sorted(have - expected.keys())
    # Without the overrides mounted, every re-voiced clip would be pruned and
    # regenerated on the voice that swallowed its first word.
    print(f"{len(VOICE_OVERRIDES)} voice overrides from {OVERRIDES_PATH}", flush=True)
    print(
        f"{len(expected)} variants, {len(have)} clips exist, generating {len(todo)}, pruning {len(stale)}",
        flush=True,
    )

    if todo:
        voices = {name: PiperVoice.load(path) for name, path in MODELS.items()}
        weak = 0
        for i, (key, text, voice_id, scale, retry) in enumerate(todo, 1):
            model_name, _, speaker = voice_id.partition(":")
            config = SynthesisConfig(length_scale=scale, speaker_id=int(speaker) if speaker else None)
            best, best_onset = b"", -1.0
            for _ in range(ONSET_TRIES if retry else 1):
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wav:
                    voices[model_name].synthesize_wav(text, wav, syn_config=config)
                clip, _ = trim_leading_silence(buf.getvalue())
                strength = onset_strength(clip) if retry else 1.0
                if strength > best_onset:
                    best, best_onset = clip, strength
                if best_onset >= ONSET_FLOOR:
                    break
            weak += best_onset < ONSET_FLOOR
            s3.upload_fileobj(io.BytesIO(best), BUCKET, key, ExtraArgs={"ContentType": "audio/wav"})
            if i % 100 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}", flush=True)
        if weak:
            print(f"  {weak} conversation clip(s) still open weakly after {ONSET_TRIES} tries", flush=True)

    for i in range(0, len(stale), 1000):
        s3.delete_objects(
            Bucket=BUCKET,
            Delete={"Objects": [{"Key": k} for k in stale[i : i + 1000]]},
        )
    print("done", flush=True)


if __name__ == "__main__":
    main()

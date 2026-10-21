"""Generate Piper TTS audio for every deck text missing from MinIO.

Reads the published deck Parquet, derives content-addressed keys (must match
app/audio.py — voice + length_scale are part of the hash), synthesises missing
clips, uploads them, and prunes clips no longer referenced. Idempotent.

Voices: headwords use the primary single-speaker voice; example sentences get
a stable per-sentence pick from the pool (primary + 10 nvcc speakers).
"""

import hashlib
import io
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


def voice_for_text(text: str, primary: bool = False) -> str:
    if primary:
        return PRIMARY_VOICE
    pick = int(hashlib.sha256(f"voice|{text.strip()}".encode()).hexdigest()[:8], 16)
    return VOICE_POOL[pick % len(VOICE_POOL)]


def audio_object_key(language: str, text: str, primary: bool = False, scale: float = LENGTH_SCALE) -> str:
    voice = voice_for_text(text, primary)
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


def existing_keys() -> set[str]:
    keys: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"audio/{LANGUAGE}/"):
        keys.update(obj["Key"] for obj in page.get("Contents", []))
    return keys


def main() -> None:
    variants = deck_variants()
    expected = {
        audio_object_key(LANGUAGE, t, primary, scale): (t, primary, scale)
        for t, primary, scale in variants
    }
    have = existing_keys()
    todo = sorted((key, *expected[key]) for key in expected.keys() - have)
    stale = sorted(have - expected.keys())
    print(
        f"{len(variants)} variants, {len(have)} clips exist, generating {len(todo)}, pruning {len(stale)}",
        flush=True,
    )

    if todo:
        voices = {name: PiperVoice.load(path) for name, path in MODELS.items()}
        for i, (key, text, primary, scale) in enumerate(todo, 1):
            voice_id = voice_for_text(text, primary)
            model_name, _, speaker = voice_id.partition(":")
            config = SynthesisConfig(
                length_scale=scale,
                speaker_id=int(speaker) if speaker else None,
            )
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav:
                voices[model_name].synthesize_wav(text, wav, syn_config=config)
            clip, _ = trim_leading_silence(buf.getvalue())
            s3.upload_fileobj(io.BytesIO(clip), BUCKET, key, ExtraArgs={"ContentType": "audio/wav"})
            if i % 100 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}", flush=True)

    for i in range(0, len(stale), 1000):
        s3.delete_objects(
            Bucket=BUCKET,
            Delete={"Objects": [{"Key": k} for k in stale[i : i + 1000]]},
        )
    print("done", flush=True)


if __name__ == "__main__":
    main()

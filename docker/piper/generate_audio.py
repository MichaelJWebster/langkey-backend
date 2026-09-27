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

import boto3
import duckdb
from piper import PiperVoice, SynthesisConfig

LANGUAGE = os.environ.get("LANGKEY_LANGUAGE", "nb")
BUCKET = os.environ.get("LANGKEY_S3_BUCKET", "langkey-content")

LENGTH_SCALE = 1.25
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
)


def voice_for_text(text: str, primary: bool = False) -> str:
    if primary:
        return PRIMARY_VOICE
    pick = int(hashlib.sha256(f"voice|{text.strip()}".encode()).hexdigest()[:8], 16)
    return VOICE_POOL[pick % len(VOICE_POOL)]


def audio_object_key(language: str, text: str, primary: bool = False) -> str:
    voice = voice_for_text(text, primary)
    digest = hashlib.sha256(f"{voice}|{LENGTH_SCALE}|{text.strip()}".encode()).hexdigest()[:20]
    return f"audio/{language}/{digest}.wav"


def deck_texts() -> dict[str, bool]:
    """text -> is_headword (headwords keep the primary voice)."""
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        s3.download_fileobj(BUCKET, f"decks/{LANGUAGE}.parquet", tmp)
        tmp.flush()
        rows = duckdb.sql(f"SELECT no, examples FROM read_parquet('{tmp.name}')").fetchall()
    texts: dict[str, bool] = {}
    for word, examples in rows:
        texts[word] = True
        for ex in examples:
            texts.setdefault(ex["no"], False)
    return texts


def existing_keys() -> set[str]:
    keys: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"audio/{LANGUAGE}/"):
        keys.update(obj["Key"] for obj in page.get("Contents", []))
    return keys


def main() -> None:
    texts = deck_texts()
    expected = {audio_object_key(LANGUAGE, t, primary): (t, primary) for t, primary in texts.items()}
    have = existing_keys()
    todo = sorted((key, *expected[key]) for key in expected.keys() - have)
    stale = sorted(have - expected.keys())
    print(
        f"{len(texts)} texts, {len(have)} clips exist, generating {len(todo)}, pruning {len(stale)}",
        flush=True,
    )

    if todo:
        voices = {name: PiperVoice.load(path) for name, path in MODELS.items()}
        for i, (key, text, primary) in enumerate(todo, 1):
            voice_id = voice_for_text(text, primary)
            model_name, _, speaker = voice_id.partition(":")
            config = SynthesisConfig(
                length_scale=LENGTH_SCALE,
                speaker_id=int(speaker) if speaker else None,
            )
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav:
                voices[model_name].synthesize_wav(text, wav, syn_config=config)
            buf.seek(0)
            s3.upload_fileobj(buf, BUCKET, key, ExtraArgs={"ContentType": "audio/wav"})
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

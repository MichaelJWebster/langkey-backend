"""Generate Piper TTS audio for every deck text missing from MinIO.

Reads the published deck Parquet, derives content-addressed keys (must match
app/audio.py's audio_object_key), synthesises missing clips, uploads them.
Idempotent: re-runs only generate what's new.
"""

import hashlib
import io
import os
import tempfile
import wave

import boto3
import duckdb
from piper import PiperVoice

LANGUAGE = os.environ.get("LANGKEY_LANGUAGE", "nb")
BUCKET = os.environ.get("LANGKEY_S3_BUCKET", "langkey-content")
MODEL = os.environ.get("PIPER_MODEL", "/voices/no_NO-talesyntese-medium.onnx")

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("LANGKEY_S3_ENDPOINT_URL", "http://minio:9000"),
    aws_access_key_id=os.environ.get("LANGKEY_S3_ACCESS_KEY", "langkey"),
    aws_secret_access_key=os.environ.get("LANGKEY_S3_SECRET_KEY", "langkey-dev"),
)


def audio_object_key(language: str, text: str) -> str:
    digest = hashlib.sha256(text.strip().encode()).hexdigest()[:20]
    return f"audio/{language}/{digest}.wav"


def deck_texts() -> set[str]:
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        s3.download_fileobj(BUCKET, f"decks/{LANGUAGE}.parquet", tmp)
        tmp.flush()
        rows = duckdb.sql(f"SELECT no, examples FROM read_parquet('{tmp.name}')").fetchall()
    texts: set[str] = set()
    for word, examples in rows:
        texts.add(word)
        for ex in examples:
            texts.add(ex["no"])
    return texts


def existing_keys() -> set[str]:
    keys: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"audio/{LANGUAGE}/"):
        keys.update(obj["Key"] for obj in page.get("Contents", []))
    return keys


def main() -> None:
    texts = deck_texts()
    have = existing_keys()
    todo = sorted(
        (t, key) for t in texts if (key := audio_object_key(LANGUAGE, t)) not in have
    )
    print(f"{len(texts)} texts, {len(have)} clips exist, generating {len(todo)}", flush=True)
    if not todo:
        return

    voice = PiperVoice.load(MODEL)
    for i, (text, key) in enumerate(todo, 1):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            voice.synthesize_wav(text, wav)
        buf.seek(0)
        s3.upload_fileobj(buf, BUCKET, key, ExtraArgs={"ContentType": "audio/wav"})
        if i % 50 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()

# LangKey speech service

NB-Whisper behind an HTTP endpoint. A separate container from the API, in the
same repo (ADR-032): the API is comfortable on Cloudflare's `basic` tier
(¼ vCPU, 1 GiB) while this needs ~1.5 GB and CPU bursts, so sharing one image
would mean paying `standard-4` to serve flashcards — and every API deploy
would ship the model weights.

The model is Norwegian-trained, and that is a requirement rather than a
preference (ADR-031). NB-Whisper is the National Library of Norway's retrain
of Whisper on 66,000 hours of Norwegian: WER 6.6 against plain large-v3's
10.4 on Fleurs, and 2.2 against 6.8 on NST.

## Running it on the Mac

```bash
uv sync --group speech          # torch + transformers, several GB
LANGKEY_SPEECH_MODEL=NbAiLab/nb-whisper-small \
  uv run uvicorn speech_service.main:app --port 8001
```

Then point the API at it:

```bash
LANGKEY_STT_BACKEND=http LANGKEY_STT_URL=http://127.0.0.1:8001 \
  uv run uvicorn app.main:app --reload
```

The weights download on first use. Set `HF_HOME` to keep them off the system
disk — on this machine that means `/Volumes/nvm_2tb/home/michaelw/work`.

## Endpoints

- `POST /transcribe` — multipart `audio` file plus optional `language`.
  Returns `{text, language, model, duration_ms, confidence, segments}`.
- `GET /health` — reports whether the model is loaded, without loading it.

## Why no GPU

Utterances are seconds long. faster-whisper's published CPU benchmark
(small/int8, 8 threads) transcribes 13 minutes of audio in 1m42s on 1.5 GB —
several times faster than real time. A GPU buys latency on `large` and
concurrency, and one learner needs neither; an always-on g4dn.xlarge in Sydney
is roughly $500/month (ADR-024).

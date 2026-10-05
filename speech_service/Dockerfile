# Speech service: NB-Whisper behind HTTP. Separate image from the API
# (ADR-032) so the API stays on Cloudflare's `basic` tier and API deploys do
# not ship model weights.
#
# Build from the repo root:
#   docker build -f speech_service/Dockerfile -t langkey-speech .
FROM python:3.12-slim

# ffmpeg decodes what the browser records: webm/opus from Chrome and Firefox,
# mp4 from Safari. Without it librosa can only read wav.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/langkey

COPY pyproject.toml ./
COPY speech_service ./speech_service

RUN pip install --no-cache-dir uv \
    && uv pip install --system --no-cache --group speech .

# Bake the weights in: a scale-to-zero cold start must not pull from Hugging
# Face (ADR-024). The image cap on Cloudflare containers is the instance disk,
# so the model size decides the instance type.
ARG SPEECH_MODEL=NbAiLab/nb-whisper-small
ENV LANGKEY_SPEECH_MODEL=${SPEECH_MODEL} \
    HF_HOME=/srv/langkey/.hf \
    LANGKEY_SPEECH_PRELOAD=true
RUN python -c "\
from transformers import pipeline; \
import os; \
pipeline('automatic-speech-recognition', model=os.environ['LANGKEY_SPEECH_MODEL'])"

EXPOSE 8001
CMD ["uvicorn", "speech_service.main:app", "--host", "0.0.0.0", "--port", "8001"]

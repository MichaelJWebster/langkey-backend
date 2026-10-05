# Speech service: NB-Whisper behind HTTP. Separate image from the API
# (ADR-032) so the API stays on Cloudflare's `basic` tier and API deploys do
# not ship model weights.
#
# Build from the repo root:
#   docker build -f speech_service/Dockerfile -t langkey-speech .
FROM python:3.12-slim

# No apt ffmpeg: PyAV bundles the ffmpeg libraries in its wheel, which keeps
# the image smaller and the Mac free of a Homebrew dependency.

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

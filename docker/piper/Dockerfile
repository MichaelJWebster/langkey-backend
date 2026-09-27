FROM python:3.12-slim

RUN pip install --no-cache-dir piper-tts boto3 duckdb

# Norwegian Bokmål voice, baked into the image (cached layer).
ARG VOICE_BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main/no/no_NO/talesyntese/medium
RUN mkdir -p /voices && \
    python -c "import urllib.request as u; \
u.urlretrieve('${VOICE_BASE}/no_NO-talesyntese-medium.onnx', '/voices/no_NO-talesyntese-medium.onnx'); \
u.urlretrieve('${VOICE_BASE}/no_NO-talesyntese-medium.onnx.json', '/voices/no_NO-talesyntese-medium.onnx.json')"

WORKDIR /srv
COPY generate_audio.py .
CMD ["python", "generate_audio.py"]

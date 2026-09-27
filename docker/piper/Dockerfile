FROM python:3.12-slim

RUN pip install --no-cache-dir piper-tts boto3 duckdb

# Norwegian Bokmål voices, baked into the image (cached layer):
# talesyntese (single speaker, primary) + nvcc (10 speakers, example pool).
ARG HF=https://huggingface.co/rhasspy/piper-voices/resolve/main/no/no_NO
RUN mkdir -p /voices && \
    python -c "import urllib.request as u; \
u.urlretrieve('${HF}/talesyntese/medium/no_NO-talesyntese-medium.onnx', '/voices/no_NO-talesyntese-medium.onnx'); \
u.urlretrieve('${HF}/talesyntese/medium/no_NO-talesyntese-medium.onnx.json', '/voices/no_NO-talesyntese-medium.onnx.json'); \
u.urlretrieve('${HF}/nvcc/medium/no_NO-nvcc-medium.onnx', '/voices/no_NO-nvcc-medium.onnx'); \
u.urlretrieve('${HF}/nvcc/medium/no_NO-nvcc-medium.onnx.json', '/voices/no_NO-nvcc-medium.onnx.json')"

WORKDIR /srv
COPY generate_audio.py .
CMD ["python", "generate_audio.py"]

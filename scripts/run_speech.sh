#!/usr/bin/env bash
# Start the speech service that backs the pronunciation check (ADR-036).
#
# Native rather than a compose service on purpose: the model runs on Metal,
# which Docker on the Mac cannot reach. Nothing restarts this for you, so it
# is gone after a reboot and the check quietly disappears with it -- that is
# what this script is for.
#
#   scripts/run_speech.sh            # foreground, ctrl-C to stop
#   scripts/run_speech.sh --daemon   # background, survives this terminal
#
# The API finds it through LANGKEY_STT_URL in .env. With the service down the
# route answers 503 and the app simply does not offer the check.
set -euo pipefail

VENV=${LANGKEY_SPEECH_VENV:-/Volumes/nvm_2tb/home/michaelw/work/langkey-speech-venv}
export HF_HOME=${HF_HOME:-/Volumes/nvm_2tb/home/michaelw/work/huggingface}
export LANGKEY_SPEECH_MODEL=${LANGKEY_SPEECH_MODEL:-NbAiLab/nb-whisper-medium}
export LANGKEY_SPEECH_PRELOAD=${LANGKEY_SPEECH_PRELOAD:-true}
PORT=${LANGKEY_SPEECH_PORT:-8001}
LOG=${LANGKEY_SPEECH_LOG:-/tmp/langkey-speech.log}

cd "$(dirname "$0")/.."

if [ ! -x "$VENV/bin/python" ]; then
  echo "No speech venv at $VENV" >&2
  echo "It holds torch and transformers, which deliberately stay out of the" >&2
  echo "API's .venv. See speech_service/README.md to build it." >&2
  exit 1
fi

if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "Already running on port $PORT:"
  curl -fsS "http://127.0.0.1:$PORT/health"; echo
  exit 0
fi

run=("$VENV/bin/python" -m uvicorn speech_service.main:app --host 127.0.0.1 --port "$PORT")

if [ "${1:-}" = "--daemon" ]; then
  nohup "${run[@]}" > "$LOG" 2>&1 &
  echo "Starting $LANGKEY_SPEECH_MODEL on port $PORT (pid $!), logging to $LOG"
  for _ in $(seq 1 24); do
    if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
      curl -fsS "http://127.0.0.1:$PORT/health"; echo
      exit 0
    fi
    sleep 5
  done
  echo "Did not come up within two minutes; see $LOG" >&2
  exit 1
fi

echo "Starting $LANGKEY_SPEECH_MODEL on port $PORT. Ctrl-C to stop."
exec "${run[@]}"

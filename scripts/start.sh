#!/usr/bin/env bash
# Bring up every LangKey service for local development.
#
#   ./start.sh            start whatever is not already running
#   ./start.sh --status   report what is up, change nothing
#
# A service that holds its port but will not answer is reported as wedged,
# with the tail of its log, rather than treated as "not running" -- starting a
# second copy would only fail to bind the port the dead one still owns.
#
# Safe to run when things are already going: each service is checked first and
# left alone if it answers, so this never restarts the API out from under a
# session or interrupts anything. It only ever starts things -- there is no
# stop or reset here, deliberately, because `docker compose down -v` would
# take the review history with it. Stopping lives in ./stop.sh, which also
# leaves the volumes alone.
set -uo pipefail

# The real file lives in langkey-backend/scripts/ and is symlinked into the
# umbrella, because the umbrella directory is not a git repo -- the same reason
# ARCHITECTURE.md lives in the backend repo and is symlinked back. So resolve
# the symlink and take the umbrella from the real path, and this works whether
# it is run as ~/work/lang_app/start.sh or straight out of scripts/.
self=${BASH_SOURCE[0]}
while [ -L "$self" ]; do
  link=$(readlink "$self")
  case $link in
    /*) self=$link ;;
    *)  self="$(cd "$(dirname "$self")" && pwd)/$link" ;;
  esac
done
ROOT="$(cd "$(dirname "$self")/../.." && pwd)"
BACKEND="$ROOT/langkey-backend"
FRONTEND="$ROOT/langkey-frontend"
LOGS="$ROOT/.logs"

API_URL=http://localhost:8000/api/v1/health
SPEECH_URL=http://127.0.0.1:8001/health
WEB_URL=http://localhost:5173

API_PORT=8000
SPEECH_PORT=8001
WEB_PORT=5173

if [ -t 1 ]; then
  DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; OFF=$'\033[0m'
else
  DIM=''; GREEN=''; YELLOW=''; RED=''; OFF=''
fi
ok()    { printf '  %sok%s      %s\n' "$GREEN" "$OFF" "$1"; }
note()  { printf '  %s--%s      %s\n' "$DIM" "$OFF" "$1"; }
warn()  { printf '  %swarn%s    %s\n' "$YELLOW" "$OFF" "$1"; }
fail()  { printf '  %sfailed%s  %s\n' "$RED" "$OFF" "$1"; }

up() { curl -fsS --max-time 2 "$1" >/dev/null 2>&1; }

# Pids listening on a TCP port. Empty when the port is free.
port_pids() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u; }

# A service that holds its port but will not answer.
#
# uvicorn --reload and vite both bind the socket in a parent process and serve
# from a child. When the child dies -- a migration that raises, a seed that
# hits a constraint, an import error on reload -- the parent keeps the socket
# bound with nothing behind it. Connections are then accepted into the backlog
# and hang forever, which reads as a slow start rather than a dead one, and
# starting a second copy cannot help: it fails to bind the port the corpse
# still owns. So say so, show the reason, and name the pids to kill.
wedged() {
  local name=$1 port=$2 log=$3 pids
  pids=$(port_pids "$port")
  [ -n "$pids" ] || return 1

  local list=${pids//$'\n'/ }
  fail "$name holds port $port but does not answer — wedged, not starting"
  note "listening pid(s): $list"
  local pid
  for pid in $pids; do
    # No child under the listener is the specific tell: the parent holds the
    # socket, so an absent worker is exactly this failure and not a slow boot.
    if [ -z "$(pgrep -P "$pid" 2>/dev/null)" ]; then
      note "pid $pid has no worker process under it — the worker died"
    fi
  done
  if [ -s "$log" ]; then
    note "the reason is usually the last traceback in $log:"
    tail -12 "$log" | sed 's/^/            /'
  else
    note "nothing logged in $log"
  fi
  note "to recover:  ./stop.sh && ./start.sh      (or: kill $list)"
  return 0
}

# Poll a URL until it answers, for at most $2 seconds.
#
# The deadline is real wall-clock time, from bash's own SECONDS. Counting
# iterations instead -- which this did until 2026-10-12 -- is wrong by however
# long a probe takes, and the probe is slowest in exactly the case worth
# waiting through: a port held by a dead worker accepts the connection into its
# backlog and never answers, so every `up` burns the full curl timeout. That
# made `wait_for "$API_URL" 60` wait up to 3m15s, measured, and turned a wedged
# port into "start.sh is taking several minutes".
wait_for() {
  local url=$1 deadline=$((SECONDS + $2))
  while [ "$SECONDS" -lt "$deadline" ]; do
    up "$url" && return 0
    sleep 1
  done
  return 1
}

# Why the port did not come up. Called after a failed wait, when the state has
# had time to settle -- the pre-flight `wedged` check runs before anything is
# launched, so it cannot see a corpse that was still exiting at the time.
diagnose() {
  local name=$1 port=$2 log=$3 pids
  pids=$(port_pids "$port")
  if grep -q 'Address already in use' "$log" 2>/dev/null; then
    note "$log says the port was already in use, so this $name never bound."
    note "something else still held port $port — usually an earlier copy that"
    note "had not finished exiting. ./stop.sh clears it."
  fi
  [ -n "$pids" ] && note "port $port is held by pid(s): ${pids//$'\n'/ }"
  tail -5 "$log" 2>/dev/null | sed 's/^/          /'
}

STATUS_ONLY=false
[ "${1:-}" = "--status" ] && STATUS_ONLY=true

WEDGED=false  # set by wedged(); nothing useful to say at the end if so

echo
echo "LangKey — local services"
echo

# --- Docker ------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "  Docker is not installed, or not on PATH."
  echo
  echo "  Postgres and MinIO run in containers, so nothing else can start"
  echo "  without it. Install Docker Desktop, then run this again."
  echo
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "  The Docker daemon is not running."
  echo
  echo "  Postgres (the review history) and MinIO (audio clips and your"
  echo "  recordings) both live in containers, so there is nothing useful to"
  echo "  start until it is up."
  echo
  echo "    open -a Docker        and give it about thirty seconds"
  echo "    ./start.sh            then run this again"
  echo
  exit 1
fi

# --- data services ------------------------------------------------------------
echo "Data"
running_containers=$(cd "$BACKEND" && docker compose ps --services --filter status=running 2>/dev/null)
for svc in postgres minio; do
  if grep -qx "$svc" <<<"$running_containers"; then
    ok "$svc already running"
  elif $STATUS_ONLY; then
    note "$svc not running"
  else
    printf '  %s...%s     starting %s\n' "$DIM" "$OFF" "$svc"
    if (cd "$BACKEND" && docker compose up -d --wait "$svc" >/dev/null 2>&1); then
      ok "$svc started"
    else
      fail "$svc did not come up — try: cd langkey-backend && docker compose up $svc"
      exit 1
    fi
  fi
done

mkdir -p "$LOGS"

# --- API ----------------------------------------------------------------------
echo
echo "Backend"
if up "$API_URL"; then
  ok "api already running        http://localhost:8000"
elif wedged "api" "$API_PORT" "$LOGS/api.log"; then
  WEDGED=true
elif $STATUS_ONLY; then
  note "api not running"
else
  # --reload, so editing backend files picks up without a restart. Migrations
  # and seeding run on startup, which is why this waits a good while.
  printf '\n=== starting api %s ===\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOGS/api.log"
  # Append rather than truncate: two runs at once used to overwrite each other
  # here, which destroyed the one line that said what went wrong.
  (cd "$BACKEND" && nohup uv run uvicorn app.main:app --reload \
      >> "$LOGS/api.log" 2>&1 &)
  started=$SECONDS
  if wait_for "$API_URL" 60; then
    ok "api started               http://localhost:8000  ($((SECONDS - started))s)"
  else
    fail "api did not answer within $((SECONDS - started))s — see $LOGS/api.log"
    diagnose "api" "$API_PORT" "$LOGS/api.log"
    exit 1
  fi
fi

# --- speech service ------------------------------------------------------------
# Optional: without it the pronunciation check is simply absent (ADR-036), and
# it needs the separate venv that holds torch, so a missing one is not an error.
if up "$SPEECH_URL"; then
  ok "speech already running     http://127.0.0.1:8001"
elif wedged "speech" "$SPEECH_PORT" /tmp/langkey-speech.log; then
  warn "the app still works; the pronunciation check will be absent"
elif $STATUS_ONLY; then
  note "speech not running — no pronunciation check"
elif [ -x "${LANGKEY_SPEECH_VENV:-/Volumes/nvm_2tb/home/michaelw/work/langkey-speech-venv}/bin/python" ]; then
  printf '  %s...%s     starting speech (loading the model takes a moment)\n' "$DIM" "$OFF"
  if "$BACKEND/scripts/run_speech.sh" --daemon >/dev/null 2>&1; then
    ok "speech started            http://127.0.0.1:8001  (/tmp/langkey-speech.log)"
  else
    warn "speech did not start — see /tmp/langkey-speech.log"
    warn "the app still works; the pronunciation check will be absent"
  fi
else
  warn "no speech venv — pronunciation check will be absent"
  warn "see langkey-backend/speech_service/README.md to build it"
fi

# --- frontend -------------------------------------------------------------------
echo
echo "Frontend"
if up "$WEB_URL"; then
  ok "vite already running       $WEB_URL"
elif wedged "vite" "$WEB_PORT" "$LOGS/web.log"; then
  WEDGED=true
elif $STATUS_ONLY; then
  note "vite not running"
else
  printf '\n=== starting vite %s ===\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOGS/web.log"
  (cd "$FRONTEND" && nohup npm run dev >> "$LOGS/web.log" 2>&1 &)
  started=$SECONDS
  if wait_for "$WEB_URL" 45; then
    ok "vite started              $WEB_URL  ($((SECONDS - started))s)"
  else
    fail "vite did not answer within $((SECONDS - started))s — see $LOGS/web.log"
    diagnose "vite" "$WEB_PORT" "$LOGS/web.log"
    exit 1
  fi
fi

echo
if $WEDGED; then
  echo "  Something is wedged — see above. ./stop.sh clears it without"
  echo "  touching the database, then ./start.sh brings it back."
  echo
  exit 1
fi
echo "  Open $WEB_URL — log in as dev@langkey.dev / langkey-dev"
echo "  ${DIM}Containers survive a reboot; the API, speech and vite do not.${OFF}"
echo

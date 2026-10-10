#!/usr/bin/env bash
# Bring the LangKey stack down, including when something is stuck.
#
#   ./stop.sh            stop everything: vite, speech, api, Postgres, MinIO
#   ./stop.sh --apps     stop only the local processes, leave the containers up
#   ./stop.sh --status   report what is running, change nothing
#
# Your data is never at risk here. Containers are stopped, not removed, and no
# volume is ever touched: `docker compose down -v` would take the whole review
# history with it, so this script does not own a flag that could reach it.
# `./start.sh` brings everything back.
#
# The usual reason to reach for this is a wedged service -- uvicorn --reload
# and vite both bind their port in a parent and serve from a child, so a dead
# child leaves the port held with nothing answering, and start.sh cannot
# recover that on its own (it would fail to bind the port the corpse owns).
set -uo pipefail

# The real file lives in langkey-backend/scripts/ and is symlinked into the
# umbrella, because the umbrella directory is not a git repo -- the same reason
# ARCHITECTURE.md lives in the backend repo and is symlinked back. So resolve
# the symlink and take the umbrella from the real path, and this works whether
# it is run as ~/work/lang_app/stop.sh or straight out of scripts/.
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

# Pids listening on a TCP port. Empty when the port is free.
port_pids() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u; }

# This script and every process above it, so a pattern can never match the
# shell that invoked it -- a terminal whose command line happens to mention
# `uvicorn app.main:app` would otherwise be a target, and killing it would
# take this script with it.
SELF=" $$ "
_p=$$
while [ "$_p" -gt 1 ]; do
  _p=$(ps -o ppid= -p "$_p" 2>/dev/null | tr -d ' ')
  [ -n "$_p" ] || break
  SELF="$SELF$_p "
done
unset _p

# Everything to kill for one service: whoever holds the port, plus anything
# matching the command pattern. Both are needed. The port alone misses a
# process that crashed before binding or runs under a different parent; the
# pattern alone misses vite, whose listener is a node process several levels
# below `npm run dev`.
targets() {
  local port=$1 pattern=$2 pids=() pid
  for pid in $(port_pids "$port") $(pgrep -f "$pattern" 2>/dev/null); do
    case "$SELF" in *" $pid "*) continue ;; esac
    pids+=("$pid")
  done
  printf '%s\n' "${pids[@]+"${pids[@]}"}" | awk 'NF' | sort -un
}

# TERM, wait, then KILL what is left, and report against the port rather than
# against the kill -- a process can refuse to die, and the port is what the
# next ./start.sh actually needs free.
stop_service() {
  local name=$1 port=$2 pattern=$3
  local pids
  pids=$(targets "$port" "$pattern")
  if [ -z "$pids" ]; then
    note "$name not running"
    return 0
  fi

  printf '  %s...%s     stopping %s (pid %s)\n' "$DIM" "$OFF" "$name" "${pids//$'\n'/ }"
  # shellcheck disable=SC2086  # deliberate word splitting: one kill, many pids
  kill $pids 2>/dev/null

  local waited=0
  while [ "$waited" -lt 10 ]; do
    [ -z "$(targets "$port" "$pattern")" ] && break
    sleep 1
    waited=$((waited + 1))
  done

  local left
  left=$(targets "$port" "$pattern")
  if [ -n "$left" ]; then
    warn "$name ignored SIGTERM — forcing"
    # shellcheck disable=SC2086
    kill -9 $left 2>/dev/null
    sleep 1
    left=$(targets "$port" "$pattern")
  fi

  if [ -n "$left" ]; then
    fail "$name still holding port $port (pid ${left//$'\n'/ })"
    return 1
  fi
  ok "$name stopped"
}

report() {
  local name=$1 port=$2 pattern=$3 pids
  pids=$(targets "$port" "$pattern")
  if [ -z "$pids" ]; then
    note "$name not running"
  else
    ok "$name running (pid ${pids//$'\n'/ })"
  fi
}

APPS_ONLY=false
STATUS_ONLY=false
case "${1:-}" in
  --apps)   APPS_ONLY=true ;;
  --status) STATUS_ONLY=true ;;
  "")       ;;
  *)
    echo "usage: ./stop.sh [--apps | --status]" >&2
    exit 2
    ;;
esac

echo
echo "LangKey — stopping local services"
echo

PROBLEMS=false

# --- local processes ------------------------------------------------------------
# Stopped before the containers: the API holds Postgres connections, and
# shutting it down first saves it a pile of connection errors on the way out.
echo "Processes"
if $STATUS_ONLY; then
  report "vite"   "$WEB_PORT"    'langkey-frontend.*vite|vite.*langkey-frontend'
  report "api"    "$API_PORT"    'uvicorn app.main:app'
  report "speech" "$SPEECH_PORT" 'uvicorn speech_service.main'
else
  stop_service "vite"   "$WEB_PORT"    'langkey-frontend.*vite|vite.*langkey-frontend' || PROBLEMS=true
  stop_service "api"    "$API_PORT"    'uvicorn app.main:app' || PROBLEMS=true
  stop_service "speech" "$SPEECH_PORT" 'uvicorn speech_service.main' || PROBLEMS=true
fi

# --- containers ----------------------------------------------------------------
echo
echo "Data"
if $APPS_ONLY; then
  note "postgres and minio left running (--apps)"
elif ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  note "docker is not available — nothing to stop"
else
  running=$(cd "$BACKEND" && docker compose ps --services --filter status=running 2>/dev/null)
  if [ -z "$running" ]; then
    note "postgres and minio already stopped"
  elif $STATUS_ONLY; then
    for svc in $running; do ok "$svc running"; done
  else
    printf '  %s...%s     stopping containers\n' "$DIM" "$OFF"
    # `stop`, never `down`: the containers and their volumes stay, so the next
    # ./start.sh is a restart rather than a fresh database.
    if (cd "$BACKEND" && docker compose stop >/dev/null 2>&1); then
      ok "postgres and minio stopped  ${DIM}(data kept)${OFF}"
    else
      fail "containers did not stop — try: cd langkey-backend && docker compose stop"
      PROBLEMS=true
    fi
  fi
fi

echo
if $STATUS_ONLY; then
  echo "  ${DIM}Nothing was changed. ./stop.sh stops the lot.${OFF}"
elif $PROBLEMS; then
  echo "  Something would not stop — see above."
  echo "  ${DIM}lsof -nP -iTCP:$API_PORT -sTCP:LISTEN  shows what still holds the API port.${OFF}"
  echo
  exit 1
elif $APPS_ONLY; then
  echo "  Processes stopped; Postgres and MinIO are still up. ./start.sh brings"
  echo "  the rest back."
else
  echo "  All stopped. ./start.sh brings it back."
  echo "  ${DIM}Your review history is untouched — the volumes were never dropped.${OFF}"
fi
echo

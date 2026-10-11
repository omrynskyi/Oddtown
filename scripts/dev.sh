#!/usr/bin/env bash
# Starts, stops and checks the local Oddtown stack. The Makefile calls this.
#
#   scripts/dev.sh start    infra if .env needs it, then observer, world and UI, then verify
#   scripts/dev.sh stop     stop the three services (Docker is left running)
#   scripts/dev.sh restart  stop, then start again in the same world mode
#   scripts/dev.sh status   what is up, on which port, and whether it answers
#   scripts/dev.sh verify   health checks only, exits 1 if anything is down
#   scripts/dev.sh smoke    verify, then one real run until every character is online, then stop it
#
# Safe to run again at any time: a service that already answers is left alone.
# Logs and pid files go in .oddtown/dev/. WORLD=mock plays the scripted mock run
# instead of starting real characters, which needs no LLM and no Docker.

set -euo pipefail
cd "$(dirname "$0")/.."

DEV=.oddtown/dev
mkdir -p "$DEV"

# Load .env, but let anything already set win, so `make dev TRANSPORT=http` works.
given=$(export -p)
set -a
# shellcheck disable=SC1091
[ -f .env ] && . ./.env
set +a
eval "$given"

OBSERVER_PORT=${OBSERVER_PORT:-8100}
WORLD_PORT=${WORLD_PORT:-8200}
UI_PORT=${UI_PORT:-5173}
# The mode the stack was last started in. `start` defaults to the real world,
# everything else (status, stop, restart) to the last mode.
# The file holds the world mode, then the transport and registry for a real world.
RUNNING_MODE=$(cat "$DEV/world.mode" 2>/dev/null || echo real)
if [ "${1:-}" = start ]; then WORLD=${WORLD:-real}; else WORLD=${WORLD:-${RUNNING_MODE%% *}}; fi
wanted_mode() { if [ "$WORLD" = mock ]; then echo mock; else echo "real ${TRANSPORT:-} ${REGISTRY:-}"; fi; }

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; }

# name -> port, health URL, command, and a pattern that identifies the process
port_of() { case $1 in observer) echo "$OBSERVER_PORT" ;; world) echo "$WORLD_PORT" ;; ui) echo "$UI_PORT" ;; esac; }
health_of() {
  case $1 in
    observer) echo "http://127.0.0.1:$OBSERVER_PORT/runs" ;;
    world) echo "http://127.0.0.1:$WORLD_PORT/openapi.json" ;;
    ui) echo "http://localhost:$UI_PORT/" ;;
  esac
}
pattern_of() { case $1 in observer) echo "observer.app" ;; world) echo "world.service|observer.mock_world" ;; ui) echo "vite" ;; esac; }

launch() {
  local log="$DEV/$1.log"
  [ "$1" = world ] && wanted_mode >"$DEV/world.mode"
  case $1 in
    observer) nohup .venv/bin/uvicorn observer.app:app --port "$OBSERVER_PORT" >"$log" 2>&1 & ;;
    world)
      if [ "$WORLD" = mock ]; then
        nohup .venv/bin/uvicorn observer.mock_world:app --port "$WORLD_PORT" >"$log" 2>&1 &
      else
        nohup .venv/bin/python -m world.service --port "$WORLD_PORT" >"$log" 2>&1 &
      fi
      ;;
    ui) (cd ui && exec nohup node_modules/.bin/vite --port "$UI_PORT" --strictPort) >"$log" 2>&1 & ;;
  esac
  echo $! >"$DEV/$1.pid"
}

answers() { curl -fsS -o /dev/null --max-time 2 "$1" 2>/dev/null; }
listener() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1 || true; }

needs_docker() { [ "${TRANSPORT:-}" = slim ] || [ "${REGISTRY:-}" = agntcy ]; }

infra() {
  if [ "$WORLD" = mock ] || ! needs_docker; then
    ok "Docker not needed (TRANSPORT=${TRANSPORT:-unset}, REGISTRY=${REGISTRY:-unset}, WORLD=$WORLD)"
    return
  fi
  if ! docker info >/dev/null 2>&1; then
    fail "Docker isn't running, and .env asks for SLIM or the AGNTCY Directory."
    echo "    Start Docker Desktop, or set TRANSPORT=http and REGISTRY=file in .env."
    exit 1
  fi
  docker compose up -d --wait >"$DEV/docker.log" 2>&1 || {
    fail "docker compose up failed, see $DEV/docker.log"
    tail -20 "$DEV/docker.log"
    exit 1
  }
  ok "SLIM and the Directory are up (docker compose)"
}

check_llm() {
  local model=${ODDTOWN_MODEL:-}
  # Even the mock world gets a real end-of-run summary from the observer, so check the model either way.
  if [ "$model" = mock ]; then
    ok "LLM: mock model, no calls"
    return
  fi
  if [ -n "${ODDTOWN_LLM_BASE_URL:-}" ]; then
    local models
    if ! models=$(curl -fsS --max-time 3 "$ODDTOWN_LLM_BASE_URL/models" 2>/dev/null); then
      warn "LLM endpoint $ODDTOWN_LLM_BASE_URL isn't answering (is Ollama running?)"
    elif [ -n "$model" ] && ! grep -qF "\"$model\"" <<<"$models"; then
      warn "LLM endpoint is up but doesn't list $model (try: ollama pull $model)"
    else
      ok "LLM: ${model:-default model} at $ODDTOWN_LLM_BASE_URL"
    fi
  elif [ -z "${OPENCODE_API_KEY:-}" ] && [[ "$model" != claude-* ]]; then
    warn "No OPENCODE_API_KEY and no ODDTOWN_LLM_BASE_URL, so LLM calls will fail. Set ODDTOWN_MODEL=mock to run without one."
  else
    ok "LLM: ${model:-big-pickle}"
  fi
}

start_one() {
  local name=$1 port url pid
  port=$(port_of "$name")
  url=$(health_of "$name")
  if answers "$url"; then
    if [ "$name" = world ] && [ "$RUNNING_MODE" != "$(wanted_mode)" ]; then
      warn "world is already running as \"$RUNNING_MODE\", not \"$(wanted_mode)\". To switch, run make restart with the same settings"
    else
      ok "$name already running on :$port"
    fi
    return
  fi
  pid=$(listener "$port")
  if [ -n "$pid" ]; then
    fail "$name: port $port is taken by pid $pid ($(ps -o comm= -p "$pid")) and it isn't answering $url"
    exit 1
  fi
  launch "$name"
  for _ in $(seq 1 60); do
    if answers "$url"; then
      ok "$name started on :$port (log: $DEV/$name.log)"
      return
    fi
    if ! kill -0 "$(cat "$DEV/$name.pid")" 2>/dev/null; then break; fi
    sleep 0.5
  done
  fail "$name didn't come up. Last lines of $DEV/$name.log:"
  tail -20 "$DEV/$name.log" | sed 's/^/    /'
  exit 1
}

stop_one() {
  local name=$1 port pids=() pid
  port=$(port_of "$name")
  [ -f "$DEV/$name.pid" ] && pids+=("$(cat "$DEV/$name.pid")")
  # Also stop one started by hand in a terminal, as long as it is ours.
  pid=$(listener "$port")
  if [ -n "$pid" ] && ps -o command= -p "$pid" | grep -Eq "$(pattern_of "$name")"; then pids+=("$pid"); fi
  local stopped=0
  for pid in "${pids[@]+"${pids[@]}"}"; do
    if kill "$pid" 2>/dev/null; then stopped=1; fi
  done
  rm -f "$DEV/$name.pid"
  for _ in $(seq 1 20); do [ -z "$(listener "$port")" ] && break; sleep 0.25; done
  if [ -n "$(listener "$port")" ]; then
    warn "$name: port $port is still held by pid $(listener "$port"), which isn't an Oddtown process. Left alone."
  elif [ $stopped = 1 ]; then
    ok "$name stopped"
  else
    ok "$name wasn't running"
  fi
}

verify() {
  local bad=0 name
  for name in observer world ui; do
    if answers "$(health_of "$name")"; then
      ok "$name answers on :$(port_of "$name")"
    else
      fail "$name isn't answering on :$(port_of "$name")"
      bad=1
    fi
  done
  if [ "$WORLD" != mock ] && needs_docker; then
    if docker compose ps --status running --services 2>/dev/null | grep -q slim; then
      ok "SLIM container running"
    else
      fail "SLIM container isn't running"
      bad=1
    fi
  fi
  check_llm
  return $bad
}

# One real run through the whole stack: create it, wait for every character to move in, stop it.
smoke() {
  local run_id
  run_id=$(curl -fsS --max-time 30 -X POST "http://127.0.0.1:$WORLD_PORT/runs" -H 'content-type: application/json' \
    -d '{"seed": "smoke test: a coffee subscription"}' | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["run_id"])') || {
    fail "the world service didn't create a run"
    exit 1
  }
  ok "created $run_id"
  for _ in $(seq 1 120); do
    if curl -fsS "http://127.0.0.1:$OBSERVER_PORT/runs/$run_id/events" | grep -q '"run.ready"'; then
      local born
      born=$(curl -fsS "http://127.0.0.1:$OBSERVER_PORT/runs/$run_id/events" | grep -o '"agent.born"' | wc -l | tr -d ' ')
      ok "the town came online: $born characters moved in, events reached the observer"
      curl -fsS -o /dev/null -X POST "http://127.0.0.1:$WORLD_PORT/runs/$run_id/stop" 2>/dev/null && ok "stopped $run_id" || true
      return
    fi
    sleep 0.5
  done
  fail "$run_id never reached run.ready in 60s. See $DEV/world.log"
  exit 1
}

case ${1:-} in
  start)
    bold "Infrastructure"
    infra
    bold "Services"
    for name in observer world ui; do start_one "$name"; done
    bold "Checks"
    verify
    echo
    bold "Oddtown is up: http://localhost:$UI_PORT"
    echo "  observer http://localhost:$OBSERVER_PORT · world http://localhost:$WORLD_PORT ($WORLD) · logs: make logs · stop: make stop"
    ;;
  stop)
    for name in ui world observer; do stop_one "$name"; done
    ;;
  restart)
    for name in ui world observer; do stop_one "$name"; done
    WORLD=$WORLD exec "$0" start
    ;;
  status | verify)
    verify
    ;;
  smoke)
    verify
    bold "Smoke run"
    smoke
    ;;
  *)
    echo "usage: $0 start|stop|restart|status|verify|smoke" >&2
    exit 2
    ;;
esac

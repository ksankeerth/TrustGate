#!/usr/bin/env bash
# Stop whatever start-all.sh started. Safe to run when nothing is running.
set -uo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

stop_port_holder() {
  # Fallback for a server that outlived its pid file (killed launcher, manual
  # start, crash between spawn and pid write). Without this, an orphaned
  # process keeps the port and start-all.sh refuses to start.
  local name="$1" port="$2"
  local pids
  pids="$(lsof -i ":$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    return 1
  fi
  echo "$name: no valid pid file, but port $port is still held by pid(s) $(echo "$pids" | tr '\n' ' ')- stopping"
  # shellcheck disable=SC2086
  kill -TERM $pids 2>/dev/null || true
  sleep 2
  pids="$(lsof -i ":$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill -KILL $pids 2>/dev/null || true
  fi
  echo "$name: stopped"
  return 0
}

stop_service() {
  local name="$1" pid_file="$RUN_DIR/$2.pid" port="$3"

  if [ ! -f "$pid_file" ]; then
    stop_port_holder "$name" "$port" || echo "$name: not running (no pid file)"
    return
  fi

  local pid
  pid="$(cat "$pid_file")"
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pid_file"
    stop_port_holder "$name" "$port" || echo "$name: not running (stale pid file removed)"
    return
  fi

  echo "$name: stopping (pid $pid)..."
  # start.sh runs the server as a child, so signal the whole process group.
  kill -TERM -- "-$(ps -o pgid= "$pid" | tr -d ' ')" 2>/dev/null || kill -TERM "$pid" 2>/dev/null

  for _ in $(seq 15); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done

  if kill -0 "$pid" 2>/dev/null; then
    echo "$name: did not stop gracefully, forcing"
    kill -KILL -- "-$(ps -o pgid= "$pid" | tr -d ' ')" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
  fi

  rm -f "$pid_file"

  # The wrapper may have exited while the real server kept the port.
  stop_port_holder "$name" "$port" >/dev/null 2>&1 || true
  echo "$name: stopped"
}

stop_service "TrustGate" trustgate "$TRUSTGATE_PORT"
stop_service "ThunderID" thunderid "$THUNDERID_PORT"

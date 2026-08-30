#!/usr/bin/env bash
# Start ThunderID and TrustGate together and wait until both are serving.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

if [ ! -d "$THUNDERID_HOME" ]; then
  echo "ThunderID is not downloaded yet. Run first:" >&2
  echo "  ./deployment/fetch-thunderid.sh" >&2
  exit 1
fi

mkdir -p "$RUN_DIR"

THUNDERID_PID_FILE="$RUN_DIR/thunderid.pid"
TRUSTGATE_PID_FILE="$RUN_DIR/trustgate.pid"
THUNDERID_LOG="$RUN_DIR/thunderid.log"
THUNDERID_SETUP_LOG="$RUN_DIR/thunderid-setup.log"
TRUSTGATE_LOG="$RUN_DIR/trustgate.log"
# setup.sh generates key material and seeds the admin user, then exits; start.sh
# serves. Setup must run exactly once per distribution, so a marker records it.
SETUP_MARKER="$THUNDERID_HOME/.trustgate-setup-complete"

running() {
  local pid_file="$1"
  [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

port_busy() {
  lsof -i ":$1" -sTCP:LISTEN -t >/dev/null 2>&1
}

wait_for() {
  local name="$1" url="$2" log="$3" pid_file="$4" attempts="${5:-60}"
  printf "  waiting for %s " "$name"
  for _ in $(seq "$attempts"); do
    if ! kill -0 "$(cat "$pid_file")" 2>/dev/null; then
      echo " process exited!"
      echo "--- last 30 lines of $log ---" >&2
      tail -30 "$log" >&2
      return 1
    fi
    # -k: ThunderID serves HTTPS with a self-signed certificate locally.
    if curl -sk --max-time 2 -o /dev/null "$url"; then
      echo " ready"
      return 0
    fi
    printf "."
    sleep 1
  done
  echo " timed out"
  echo "--- last 30 lines of $log ---" >&2
  tail -30 "$log" >&2
  return 1
}

# --- ThunderID ---------------------------------------------------------------
if running "$THUNDERID_PID_FILE"; then
  echo "ThunderID already running (pid $(cat "$THUNDERID_PID_FILE"))"
else
  if port_busy "$THUNDERID_PORT"; then
    echo "error: port $THUNDERID_PORT is already in use by another process" >&2
    exit 1
  fi

  # setup.sh generates certificates and other key material, seeds the default
  # resources and admin user, then exits. Without it, start.sh fails looking for
  # config/certs/crypto.key. It is a one-time step per distribution.
  if [ ! -f "$SETUP_MARKER" ]; then
    echo "First run: running ThunderID setup (generates key material, seeds admin user)..."
    if (cd "$THUNDERID_HOME" && ./setup.sh \
          --admin-username "$THUNDERID_ADMIN_USERNAME" \
          --admin-password "$THUNDERID_ADMIN_PASSWORD" \
          --direct-auth-secret "$THUNDERID_DIRECT_AUTH_SECRET" >"$THUNDERID_SETUP_LOG" 2>&1); then
      touch "$SETUP_MARKER"
      echo "  setup complete"
    else
      echo "  setup FAILED" >&2
      tail -30 "$THUNDERID_SETUP_LOG" >&2
      exit 1
    fi
  fi

  echo "Starting ThunderID on port $THUNDERID_PORT..."
  # start.sh resolves paths relative to its own directory, so run it from there.
  # nohup + </dev/null fully detaches it, so the server outlives this script and
  # the terminal that launched it. start.sh spawns the binary and waits on it,
  # so this pid is the wrapper -- stop-all.sh signals the process group.
  # set -m makes the background job its own process group leader, so stopping
  # one service does not signal the other -- without it both share this
  # script's group and killing either takes down both.
  # The redirection wraps the whole brace group, not just nohup: `&` backgrounds
  # the entire `cd && ...` list, so bash forks a subshell for it, and that
  # subshell would otherwise inherit this script's stdout -- holding a caller's
  # pipe open (e.g. `start-all.sh | tee log` would never return).
  (set -m; { cd "$THUNDERID_HOME" && nohup ./start.sh; } >"$THUNDERID_LOG" 2>&1 </dev/null &
   echo $! >"$THUNDERID_PID_FILE")

  wait_for "ThunderID" "$THUNDERID_BASE_URL/health/liveness" "$THUNDERID_LOG" "$THUNDERID_PID_FILE" 90 || exit 1
fi

# --- TrustGate ---------------------------------------------------------------
if running "$TRUSTGATE_PID_FILE"; then
  echo "TrustGate already running (pid $(cat "$TRUSTGATE_PID_FILE"))"
else
  if port_busy "$TRUSTGATE_PORT"; then
    echo "error: port $TRUSTGATE_PORT is already in use by another process" >&2
    exit 1
  fi

  if [ ! -x "$PROJECT_ROOT/.venv/bin/uvicorn" ]; then
    echo "error: $PROJECT_ROOT/.venv is missing. Create it first:" >&2
    echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
  fi

  echo "Starting TrustGate on port $TRUSTGATE_PORT..."
  (set -m; { cd "$PROJECT_ROOT" && nohup .venv/bin/uvicorn app.main:app --port "$TRUSTGATE_PORT"; } >"$TRUSTGATE_LOG" 2>&1 </dev/null &
   echo $! >"$TRUSTGATE_PID_FILE")

  wait_for "TrustGate" "http://127.0.0.1:$TRUSTGATE_PORT/health" "$TRUSTGATE_LOG" "$TRUSTGATE_PID_FILE" 30 || exit 1
fi

cat <<EOF

Both services are up.

  ThunderID console : $THUNDERID_BASE_URL/console
  ThunderID API     : $THUNDERID_BASE_URL
  admin user        : $THUNDERID_ADMIN_USERNAME / $THUNDERID_ADMIN_PASSWORD
  TrustGate API     : http://127.0.0.1:$TRUSTGATE_PORT
  TrustGate docs    : http://127.0.0.1:$TRUSTGATE_PORT/docs

  logs : $RUN_DIR/
  stop : ./deployment/stop-all.sh
EOF

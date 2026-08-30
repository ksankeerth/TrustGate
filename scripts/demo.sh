#!/usr/bin/env bash
# End-to-end demo of the challenge -> verify -> status -> review flow.
# Requires the service to already be running (see README quickstart).
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
USER_REF="${USER_REF:-demo-user-$(date +%s)}"
SAMPLES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../samples" && pwd)"

json_field() {
  python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"
}

echo "== 1) POST /challenge =="
CHALLENGE_JSON=$(curl -sf -X POST "$BASE_URL/challenge")
echo "$CHALLENGE_JSON"
CHALLENGE_ID=$(echo "$CHALLENGE_JSON" | json_field challenge_id)

echo
echo "== 2) POST /verify (selfie + id_photo + one liveness frame) =="
VERIFY_JSON=$(curl -sf -X POST "$BASE_URL/verify" \
  -F "challenge_id=$CHALLENGE_ID" \
  -F "user_ref=$USER_REF" \
  -F "selfie=@$SAMPLES_DIR/demo_selfie.txt" \
  -F "id_photo=@$SAMPLES_DIR/demo_id_photo.txt" \
  -F "liveness_frames=@$SAMPLES_DIR/demo_frame_1.txt")
echo "$VERIFY_JSON"
STATE=$(echo "$VERIFY_JSON" | json_field state)

echo
echo "== 3) GET /status/$USER_REF =="
curl -sf "$BASE_URL/status/$USER_REF"
echo

if [ "$STATE" != "PROVISIONAL" ]; then
  echo
  echo "Sync tier did not grant PROVISIONAL this run (state=$STATE)."
  echo "This is expected sometimes: the current layers are deterministic"
  echo "hash-based stubs, not real models, and liveness/injection risk also"
  echo "depends on the random per-challenge nonce -- so outcomes vary run to"
  echo "run until the real ML layers land. REJECTED is terminal, so there is"
  echo "no async review to run this time. Re-run the script to try again."
  exit 0
fi

DOCUMENT_JOB_ID=$(echo "$VERIFY_JSON" | json_field document_job_id)

echo
echo "== 4) POST /review/$DOCUMENT_JOB_ID (reviewer approves) =="
curl -sf -X POST "$BASE_URL/review/$DOCUMENT_JOB_ID" \
  -H "Content-Type: application/json" \
  -d '{"decision":"ALLOW","reviewer_note":"document looks genuine"}'
echo

echo
echo "== 5) GET /status/$USER_REF (after review) =="
curl -sf "$BASE_URL/status/$USER_REF"
echo

#!/usr/bin/env bash
# End-to-end demo of the challenge -> verify -> status -> review flow.
# Requires the service to already be running (see README quickstart).
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
USER_REF="${USER_REF:-demo-user-$(date +%s)}"
SAMPLES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../samples" && pwd)"

FRAMES_DIR="$(mktemp -d)"
trap 'rm -rf "$FRAMES_DIR"' EXIT

json_field() {
  python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"
}

echo "== 1) POST /challenge =="
CHALLENGE_JSON=$(curl -sf -X POST "$BASE_URL/challenge")
echo "$CHALLENGE_JSON"
CHALLENGE_ID=$(echo "$CHALLENGE_JSON" | json_field challenge_id)
NONCE=$(echo "$CHALLENGE_JSON" | json_field nonce)

# A real client would capture these while performing the prompted actions.
# Two frames of differing brightness stand in, so the liveness layer's
# inter-frame variation check sees something other than one repeated still.
echo
echo "== 2) Generate liveness frames and bind them to the challenge nonce =="
python3 - "$FRAMES_DIR" "$NONCE" <<'PY'
import hashlib, hmac, sys
from PIL import Image

frames_dir, nonce = sys.argv[1], sys.argv[2]
frames = []
for index, brightness in enumerate((40, 200)):
    path = f"{frames_dir}/frame_{index}.jpg"
    Image.new("RGB", (96, 96), (brightness,) * 3).save(path, format="JPEG")
    with open(path, "rb") as handle:
        frames.append(handle.read())

binding = hmac.new(nonce.encode(), b"".join(frames), hashlib.sha256).hexdigest()
with open(f"{frames_dir}/binding.txt", "w") as handle:
    handle.write(binding)
print(f"frame_binding={binding}")
PY
FRAME_BINDING=$(cat "$FRAMES_DIR/binding.txt")

echo
echo "== 3) POST /verify (selfie + id_photo + bound liveness frames) =="
VERIFY_JSON=$(curl -sf -X POST "$BASE_URL/verify" \
  -F "challenge_id=$CHALLENGE_ID" \
  -F "user_ref=$USER_REF" \
  -F "frame_binding=$FRAME_BINDING" \
  -F "selfie=@$SAMPLES_DIR/demo_selfie.txt" \
  -F "id_photo=@$SAMPLES_DIR/demo_id_photo.txt" \
  -F "liveness_frames=@$FRAMES_DIR/frame_0.jpg" \
  -F "liveness_frames=@$FRAMES_DIR/frame_1.jpg")
echo "$VERIFY_JSON"
STATE=$(echo "$VERIFY_JSON" | json_field state)

echo
echo "== 4) GET /status/$USER_REF =="
curl -sf "$BASE_URL/status/$USER_REF"
echo

if [ "$STATE" != "PROVISIONAL" ]; then
  echo
  echo "Sync tier did not grant PROVISIONAL this run (state=$STATE)."
  echo "This is expected sometimes: face match, deepfake and injection are"
  echo "still hash-based stubs by default, so their risk varies run to run"
  echo "until the real layers are enabled. REJECTED is terminal, so there is"
  echo "no async review to run this time. Re-run the script to try again."
  exit 0
fi

DOCUMENT_JOB_ID=$(echo "$VERIFY_JSON" | json_field document_job_id)

echo
echo "== 5) POST /review/$DOCUMENT_JOB_ID (reviewer approves) =="
curl -sf -X POST "$BASE_URL/review/$DOCUMENT_JOB_ID" \
  -H "Content-Type: application/json" \
  -d '{"decision":"ALLOW","reviewer_note":"document looks genuine"}'
echo

echo
echo "== 6) GET /status/$USER_REF (after review) =="
curl -sf "$BASE_URL/status/$USER_REF"
echo

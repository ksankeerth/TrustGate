#!/usr/bin/env bash
# End-to-end demo of the challenge -> verify -> status -> review flow.
# Requires the service to already be running (see README quickstart).
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
USER_REF="${USER_REF:-demo-user-$(date +%s)}"

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

# Synthetic stand-ins for what a real client would capture -- NOT real faces.
# They carry per-pixel noise and camera EXIF so the liveness and injection
# demonstrators see something shaped like a genuine capture; the frames differ
# in brightness so the inter-frame variation check sees actual change. The
# face-match and deepfake layers are off by default, so no real face is needed.
echo
echo "== 2) Generate capture images and bind the frames to the challenge nonce =="
python3 - "$FRAMES_DIR" "$NONCE" <<'PY'
import hashlib, hmac, random, sys
from PIL import Image

frames_dir, nonce = sys.argv[1], sys.argv[2]


def capture(path, seed, size=160):
    rng = random.Random(seed)
    image = Image.new("RGB", (size, size))
    image.putdata([(rng.randint(0, 255),) * 3 for _ in range(size * size)])
    exif = Image.Exif()
    exif[271] = "DemoPhone"
    exif[272] = "DemoPhone 15"
    exif[306] = "2026:08:30 10:00:00"
    image.save(path, format="JPEG", exif=exif, quality=95)
    with open(path, "rb") as handle:
        return handle.read()


capture(f"{frames_dir}/selfie.jpg", seed=1)
capture(f"{frames_dir}/id_photo.jpg", seed=2)
frames = [capture(f"{frames_dir}/frame_{i}.jpg", seed=10 + i) for i in range(2)]

binding = hmac.new(nonce.encode(), b"".join(frames), hashlib.sha256).hexdigest()
with open(f"{frames_dir}/binding.txt", "w") as handle:
    handle.write(binding)
print(f"frame_binding={binding}")
PY
FRAME_BINDING=$(cat "$FRAMES_DIR/binding.txt")

# The specimen MRZ published in ICAO Doc 9303 Part 4. Its check digits are
# valid, so the document worker escalates to human review rather than
# auto-rejecting. Corrupt any character to watch it auto-reject instead.
MRZ='P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<
L898902C36UTO7408122F1204159ZE184226B<<<<<10'

echo
echo "== 3) POST /verify (selfie + id_photo + bound frames + MRZ) =="
VERIFY_JSON=$(curl -sf -X POST "$BASE_URL/verify" \
  -F "challenge_id=$CHALLENGE_ID" \
  -F "user_ref=$USER_REF" \
  -F "frame_binding=$FRAME_BINDING" \
  -F "mrz_text=$MRZ" \
  -F "selfie=@$FRAMES_DIR/selfie.jpg" \
  -F "id_photo=@$FRAMES_DIR/id_photo.jpg" \
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
echo "== 5) GET /document/$DOCUMENT_JOB_ID (async worker result) =="
sleep 1
curl -sf "$BASE_URL/document/$DOCUMENT_JOB_ID"
echo

echo
echo "== 6) POST /review/$DOCUMENT_JOB_ID (reviewer approves) =="
curl -sf -X POST "$BASE_URL/review/$DOCUMENT_JOB_ID" \
  -H "Content-Type: application/json" \
  -d '{"decision":"ALLOW","reviewer_note":"document looks genuine"}'
echo

echo
echo "== 7) GET /status/$USER_REF (after review) =="
curl -sf "$BASE_URL/status/$USER_REF"
echo

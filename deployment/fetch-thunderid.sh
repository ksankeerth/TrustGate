#!/usr/bin/env bash
# Download and unpack a ThunderID release distribution into deployment/dist/.
# Idempotent: skips the download if the distribution is already unpacked.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

if [ -d "$THUNDERID_HOME" ]; then
  echo "ThunderID $THUNDERID_VERSION already unpacked at:"
  echo "  $THUNDERID_HOME"
  echo "Delete that directory to re-download."
  exit 0
fi

mkdir -p "$DIST_DIR"

echo "Platform : $THUNDERID_PLATFORM"
echo "Version  : $THUNDERID_VERSION"
echo "Source   : $THUNDERID_URL"
echo

if [ ! -f "$DIST_DIR/$THUNDERID_ZIP" ]; then
  echo "Downloading (~34MB)..."
  # --fail so a 404 (bad version/platform) is an error rather than a saved HTML page.
  curl -fL --progress-bar -o "$DIST_DIR/$THUNDERID_ZIP.part" "$THUNDERID_URL"
  mv "$DIST_DIR/$THUNDERID_ZIP.part" "$DIST_DIR/$THUNDERID_ZIP"
else
  echo "Archive already downloaded; reusing it."
fi

echo "Unpacking..."
unzip -q "$DIST_DIR/$THUNDERID_ZIP" -d "$DIST_DIR"

if [ ! -d "$THUNDERID_HOME" ]; then
  echo "error: expected $THUNDERID_HOME after unpacking; archive layout may have changed" >&2
  exit 1
fi

chmod +x "$THUNDERID_HOME/start.sh" 2>/dev/null || true

echo
echo "ThunderID $THUNDERID_VERSION ready at:"
echo "  $THUNDERID_HOME"
echo
echo "Next: ./deployment/start-all.sh"

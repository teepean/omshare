#!/usr/bin/env bash
#
# download-photos.sh — join the Olympus camera's WiFi and download every photo
# into the current directory, then disconnect (restoring your internet).
#
# Usage:
#   cd /where/you/want/the/photos
#   /Users/teemu/omshare/download-photos.sh                # uses the camera below
#   /Users/teemu/omshare/download-photos.sh <SSID> <PASSWORD>   # a different camera
#
# Re-runnable and resume-safe: files already present (matching size + timestamp)
# are skipped, so if the camera drops you can just run it again.
#
set -uo pipefail

# --- Camera credentials (override by passing SSID + PASSWORD as arguments) ----
SSID="${1:-E-M10MKII-P-BHLB51309}"
PASSWORD="${2:-21472323}"

# --- Locate the omshare install (the .venv next to this script) ---------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OMSHARE="$SCRIPT_DIR/.venv/bin/omshare"
DEST="$(pwd)"
MAX_ATTEMPTS=5

if [ ! -x "$OMSHARE" ]; then
  echo "error: omshare not found at $OMSHARE" >&2
  echo "       Set it up first:  cd $SCRIPT_DIR && python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

echo "Camera : $SSID"
echo "Saving to: $DEST  (flat — all photos directly in this folder)"
echo "Tip: keep the camera awake — half-press the shutter if a run stalls."

# --- Always disconnect on exit, so internet comes back ------------------------
cleanup() {
  echo
  echo "=== Disconnecting from camera (restoring internet) ==="
  "$OMSHARE" disconnect >/dev/null 2>&1 || true
}
trap cleanup EXIT

# --- Connect + download, retrying on drops (download resumes each time) --------
for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  echo
  echo "=== Attempt $attempt/$MAX_ATTEMPTS: joining camera WiFi ==="
  if ! "$OMSHARE" connect --ssid "$SSID" --password "$PASSWORD"; then
    echo "Could not join the camera WiFi — retrying in 3s..."
    sleep 3
    continue
  fi

  echo "=== Downloading ==="
  out="$("$OMSHARE" download -o "$DEST" --organize flat 2>&1)"
  echo "$out"

  # The card is empty -> done.
  if echo "$out" | grep -q "No matching files"; then
    echo "Nothing on the camera to download."
    break
  fi
  # A completed run reports "Done: ... 0 failed." — the whole listing was fetched
  # without the camera dropping, so we're finished (new or all-skipped alike).
  if echo "$out" | grep -qE '^Done:.* 0 failed\.$'; then
    complete=1
    break
  fi
  echo "Run incomplete (camera may have dropped) — reconnecting to fetch the rest..."
  sleep 2
done

# --- Unmistakable completion banner with a file count -------------------------
count=$(find "$DEST" -maxdepth 1 -type f \
          \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.orf' \
             -o -iname '*.mov' -o -iname '*.mp4' \) | wc -l | tr -d ' ')
echo
echo "=================================================="
if [ "${complete:-0}" = "1" ]; then
  echo " ✅  TRANSFER COMPLETE — $count photo/video file(s)"
else
  echo " ⚠️  TRANSFER INCOMPLETE after $MAX_ATTEMPTS attempts — $count file(s) so far."
  echo "     Keep the camera awake and run this script again to fetch the rest"
  echo "     (already-downloaded files are skipped)."
fi
echo "     Folder: $DEST"
echo "=================================================="

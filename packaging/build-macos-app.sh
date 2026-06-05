#!/usr/bin/env bash
#
# Build a double-clickable macOS app (dist/omshare.app) with PyInstaller.
#
# RUN THIS ON THE MAC (a .app can only be built on macOS). Works on Apple
# Silicon (M-series) and Intel.
#
#   cd omshare
#   ./packaging/build-macos-app.sh
#   open dist/omshare.app          # or drag it into /Applications
#
# Optional: place an icon at packaging/omshare.icns to brand the app.
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."   # repo root

PY="${PYTHON:-python3}"
BUILD_VENV=".build-venv"

echo "==> Creating build venv ($BUILD_VENV)"
"$PY" -m venv "$BUILD_VENV"
# shellcheck disable=SC1091
source "$BUILD_VENV/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet ".[gui]" pyinstaller

ICON_ARG=()
if [ -f packaging/omshare.icns ]; then
  ICON_ARG=(--icon packaging/omshare.icns)
fi

echo "==> Building dist/omshare.app"
pyinstaller --noconfirm --clean --windowed \
  --name omshare \
  --osx-bundle-identifier com.teepean.omshare \
  "${ICON_ARG[@]}" \
  packaging/omshare_gui.py

echo
echo "Done. The app is at: dist/omshare.app"
echo "Open it with:  open dist/omshare.app"
echo "Install it with: cp -R dist/omshare.app /Applications/"
echo
echo "Note: it is not code-signed/notarized. The first launch needs"
echo "right-click > Open (or: System Settings > Privacy & Security > Open Anyway)."

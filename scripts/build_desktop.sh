#!/usr/bin/env bash
# Build a portable desktop binary for the current OS (PyInstaller).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

pick_python() {
  local c ver
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      ver="$("$c" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
      case "$ver" in
        3.11|3.12|3.13) echo "$c"; return 0 ;;
      esac
      FALLBACK="$c"
    fi
  done
  echo "${FALLBACK:-python3}"
}

PYTHON="$(pick_python)"
if [[ ! -d .venv ]]; then
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements-desktop.txt

echo "Building Portage $(tr -d '[:space:]' <VERSION 2>/dev/null || echo '?') with $(python --version)…"
pyinstaller --noconfirm --clean Portage.spec

echo
echo "Done."
echo "  Folder binary: dist/Portage/"
case "$(uname -s)" in
  Darwin)
    echo "  macOS app:     dist/Portage.app"
    echo "  Tip: also use the repo-local Portage.app for a no-build double-click launch."
    ;;
  Linux)
    echo "  Run: ./dist/Portage/Portage"
    echo "  Or install the .desktop file: ./scripts/install-linux-desktop.sh"
    ;;
esac

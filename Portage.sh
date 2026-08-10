#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PYTHONUNBUFFERED=1
export PATH="/Library/Frameworks/Python.framework/Versions/3.13/bin:/Library/Frameworks/Python.framework/Versions/3.12/bin:/Library/Frameworks/Python.framework/Versions/3.11/bin:/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"
# shellcheck disable=SC1091
source "$ROOT/scripts/ensure_deps.sh"
ensure_python_deps "$ROOT"
# Force native arm64 on Apple Silicon even if this shell is under Rosetta.
if [[ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]] && command -v arch >/dev/null 2>&1; then
  exec arch -arm64 "$ROOT/.venv/bin/python" -m app.desktop
fi
exec "$ROOT/.venv/bin/python" -m app.desktop

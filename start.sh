#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

pick_python() {
  local c ver
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      ver="$("$c" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
      # Prefer 3.11–3.13 for current pydantic wheels; 3.14 may need newer deps.
      case "$ver" in
        3.11|3.12|3.13) echo "$c"; return 0 ;;
      esac
      FALLBACK="$c"
    fi
  done
  echo "${FALLBACK:-python3}"
}

PYTHON="$(pick_python)"
echo "Using $($PYTHON --version 2>&1)"

if [[ ! -d .venv ]]; then
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt

if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — add your Foundry URL and API key."
fi

exec python -m app.main

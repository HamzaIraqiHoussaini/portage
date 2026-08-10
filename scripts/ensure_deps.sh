#!/usr/bin/env bash
# Shared: create/update .venv and install deps only when requirement stamps change.
# Usage: source this after cd "$ROOT", with PYTHON set (or auto-picked).
ensure_python_deps() {
  local root="${1:-.}"
  local stamp_dir="$root/.venv"
  local stamp="$stamp_dir/.deps-stamp"
  local req="$root/requirements.txt"
  local req_desktop="$root/requirements-desktop.txt"
  local hash
  local native_arch
  native_arch="$(uname -m)"

  # True Apple Silicon hardware (1) even if this shell is Rosetta-translated.
  is_apple_silicon() {
    [[ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]]
  }

  run_venv_python() {
    local py="$root/.venv/bin/python"
    if is_apple_silicon && command -v arch >/dev/null 2>&1; then
      arch -arm64 "$py" "$@"
    else
      "$py" "$@"
    fi
  }

  pick_python_local() {
    if [[ -x "$root/.venv/bin/python" ]]; then
      echo "$root/.venv/bin/python"
      return
    fi
    local c ver
    for c in python3.13 python3.12 python3.11 python3; do
      if command -v "$c" >/dev/null 2>&1; then
        ver="$("$c" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
        case "$ver" in
          3.11|3.12|3.13) echo "$c"; return ;;
        esac
        FALLBACK="$c"
      fi
    done
    echo "${FALLBACK:-python3}"
  }

  local py
  py="${PYTHON:-$(pick_python_local)}"

  if [[ ! -d "$root/.venv" ]]; then
    if is_apple_silicon && command -v arch >/dev/null 2>&1; then
      arch -arm64 "$py" -m venv "$root/.venv" 2>/dev/null || "$py" -m venv "$root/.venv"
    else
      "$py" -m venv "$root/.venv"
    fi
  fi
  # shellcheck disable=SC1091
  source "$root/.venv/bin/activate"

  # Stamp on hardware arch (arm64), not process arch (may be x86_64 under Rosetta).
  local stamp_arch="$native_arch"
  if is_apple_silicon; then
    stamp_arch="arm64"
  fi

  hash="$(
    {
      cat "$req" 2>/dev/null || true
      echo "---"
      cat "$req_desktop" 2>/dev/null || true
      echo "arch:$stamp_arch"
      run_venv_python -c 'import sys; print(sys.version)'
    } | shasum -a 256 | awk '{print $1}'
  )"

  if [[ -f "$stamp" ]] && [[ "$(cat "$stamp")" == "$hash" ]]; then
    if run_venv_python -c "import fastapi, uvicorn; import webview" 2>/dev/null; then
      return 0
    fi
  fi

  run_venv_python -m pip install -q --upgrade pip
  run_venv_python -m pip install -q -r "$req"
  if [[ -f "$req_desktop" ]]; then
    run_venv_python -m pip install -q -r "$req_desktop"
  fi
  echo "$hash" >"$stamp"
}

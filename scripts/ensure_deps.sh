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

  # Finder-launched .apps often have a tiny PATH — include common Python installs.
  export PATH="/Library/Frameworks/Python.framework/Versions/3.13/bin:/Library/Frameworks/Python.framework/Versions/3.12/bin:/Library/Frameworks/Python.framework/Versions/3.11/bin:/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"

  run_venv_python() {
    local py="$root/.venv/bin/python"
    if is_apple_silicon && command -v arch >/dev/null 2>&1; then
      arch -arm64 "$py" "$@"
    else
      "$py" "$@"
    fi
  }

  py_version() {
    local c="$1"
    "$c" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true
  }

  py_ok() {
    case "$(py_version "$1")" in
      3.11|3.12|3.13) return 0 ;;
      *) return 1 ;;
    esac
  }

  pick_python_local() {
    local c
    for c in python3.13 python3.12 python3.11 \
      /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 \
      /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 \
      /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 \
      /opt/homebrew/bin/python3.13 \
      /opt/homebrew/bin/python3.12 \
      /opt/homebrew/bin/python3.11; do
      if command -v "$c" >/dev/null 2>&1 || [[ -x "$c" ]]; then
        if py_ok "$c"; then
          echo "$c"
          return
        fi
      fi
    done
    return 1
  }

  # If an existing venv is too old (<3.11), remove it so we recreate.
  if [[ -x "$root/.venv/bin/python" ]]; then
    if ! py_ok "$root/.venv/bin/python"; then
      rm -rf "$root/.venv"
    fi
  fi

  local py
  if [[ -n "${PYTHON:-}" ]] && py_ok "$PYTHON"; then
    py="$PYTHON"
  else
    py="$(pick_python_local)" || {
      echo "Portage needs Python 3.11–3.13. Install from https://www.python.org/downloads/ then reopen." >&2
      return 1
    }
  fi

  if [[ ! -d "$root/.venv" ]]; then
    if is_apple_silicon && command -v arch >/dev/null 2>&1; then
      arch -arm64 "$py" -m venv "$root/.venv" 2>/dev/null || "$py" -m venv "$root/.venv"
    else
      "$py" -m venv "$root/.venv"
    fi
  fi
  # shellcheck disable=SC1091
  source "$root/.venv/bin/activate"

  if ! py_ok "$root/.venv/bin/python"; then
    echo "Venv Python is not 3.11–3.13." >&2
    return 1
  fi

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

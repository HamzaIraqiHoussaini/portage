#!/usr/bin/env bash
# Install a user-level .desktop shortcut that launches without a terminal.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
chmod +x "$ROOT/Portage.sh"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APP_DIR"
ICON_SRC="$ROOT/static/favicon.svg"
ICON_DST="$HOME/.local/share/icons/portage.svg"
mkdir -p "$(dirname "$ICON_DST")"
cp "$ICON_SRC" "$ICON_DST"

cat > "$APP_DIR/portage.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.5
Name=Portage
X-Portage-Version=$(tr -d '[:space:]' <"$ROOT/VERSION" 2>/dev/null || echo 0.4.0)
Comment=Carry coding chats across Cursor, Claude Code, ChatGPT, Antigravity to Foundry or Bedrock
Exec=$ROOT/Portage.sh
Path=$ROOT
Icon=$ICON_DST
Terminal=false
Categories=Development;Utility;
StartupNotify=true
DESKTOP

chmod +x "$APP_DIR/portage.desktop"
update-desktop-database "$APP_DIR" 2>/dev/null || true
echo "Installed: $APP_DIR/portage.desktop"
echo "You can launch Portage from your app menu."

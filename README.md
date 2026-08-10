# Portage

**Carry coding chats between tools — continue them on Microsoft Foundry or AWS Bedrock.**

Portage is a local desktop (and browser) app that picks up conversations from **Cursor**, **Claude Code**, **ChatGPT**, and **Antigravity**, then lets you keep going with your own Foundry or Bedrock credentials — without fighting IDE BYOK limits.

> *Portage* (n.): carrying a boat between two bodies of water. Same idea for chats.

---

## Install

### Requirements

- **Python 3.11–3.13** (recommended)
- macOS, Windows, or Linux
- Optional: Cursor / Claude Code installed locally for auto-import
- Windows desktop: [WebView2](https://developer.microsoft.com/microsoft-edge/webview2/) (usually preinstalled)
- Linux desktop: WebKitGTK (e.g. `gir1.2-webkit2-4.1` / `webkit2gtk`)

### 1. Clone

```bash
git clone https://github.com/HamzaIraqiHoussaini/portage.git
cd portage
```

### 2a. Browser mode (fastest)

```bash
chmod +x start.sh
./start.sh
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765).

Manual equivalent:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m app.main
```

### 2b. Desktop app (no Terminal window)

Install desktop deps once:

```bash
pip install -r requirements-desktop.txt
```

Then double-click (keep these next to the repo checkout):

| OS | Launch |
| --- | --- |
| **macOS** | `Portage.app` |
| **Windows** | `Portage.vbs` (or `Portage.bat`) |
| **Linux** | `./scripts/install-linux-desktop.sh` then open **Portage** from your app menu · or `./Portage.sh` |

Or from a terminal:

```bash
python -m app.desktop
```

**macOS notes**

- Gatekeeper: Right-click → Open, or  
  `xattr -dr com.apple.quarantine Portage.app`
- Apple Silicon: the launcher forces native ARM so Rosetta doesn’t break `pydantic` wheels. If launch still fails:  
  `rm -rf .venv && python3 -m venv .venv && pip install -r requirements-desktop.txt`
- Logs: `~/Library/Logs/Portage.log`

**Portable builds** (relocatable binaries):

```bash
./scripts/build_desktop.sh      # macOS / Linux
scripts\build_desktop.bat       # Windows
```

Output lands in `dist/Portage/` (and `dist/Portage.app` on macOS). Frozen config lives in `~/.portage/`.

---

## First-run setup

1. Open **Provider** → choose **Foundry** or **AWS Bedrock**.
2. Paste credentials → **Connect & test**.
3. Under **Sources**:
   - Toggle **Cursor** / **Claude Code** when detected on this machine.
   - Click **ChatGPT** or **Antigravity** (or **Import…**) to load an export file.
4. Open a conversation from the rail (or hit **New**) and chat.

Token use for the last reply, this chat, and the session shows in the stage header.

---

## Features

- **Providers:** Microsoft Foundry (Anthropic Messages) or AWS Bedrock Converse
- **Imports:** Cursor & Claude Code (auto) · ChatGPT `conversations.json` · Antigravity JSON/Markdown exports
- **Workspaces:** link local folders for context
- **Skills:** type `/` to invoke agent skills
- **Optional Cursor write-back** into the original transcript
- **Security-minded local app:** localhost-only API mutations, SSRF checks on Foundry URLs, CSP, sanitization, size limits

---

## Configuration

- Copy `.env.example` → `.env` for defaults (optional; the UI can save a local bridge config).
- Dev data: `./data/`
- Desktop frozen data: `~/.portage/` (legacy `~/.cursor-foundry-chat/` is still read if present)

---

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

Unofficial community project. Not affiliated with Anysphere (Cursor), Anthropic, OpenAI, Google, Microsoft, or Amazon.

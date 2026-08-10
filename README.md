# Cursor Foundry Chat

Continue Cursor agent conversations on **Microsoft Foundry** (Claude / Anthropic Messages API), with your local Cursor skills and settings loaded into context — and write replies back into the same Cursor transcript so you can return to Cursor without re-briefing.

## Why this exists

Cursor’s Azure BYOK path does not expose Foundry Claude’s Anthropic Messages API cleanly for every setup. This local bridge:

1. Connects to your Foundry Anthropic endpoint (`…/anthropic/v1/messages`)
2. Lists Cursor agent chats from `~/.cursor/projects/**/agent-transcripts`
3. Imports Cursor / agent skills + relevant `settings.json` keys into the system prompt
4. Chats via Foundry Opus (or your deployment name)
5. Appends the exchange back to the Cursor JSONL transcript (+ conversation search index)

## Requirements

- macOS (paths assume Cursor’s macOS Application Support layout; Linux/Windows welcome via PRs)
- Python 3.11+
- A Microsoft Foundry (Azure AI) Anthropic-compatible deployment and API key
- Existing Cursor agent transcripts to continue

## Quick start

```bash
git clone https://github.com/HamzaIraqiHoussaini/cursor-foundry-chat.git
cd cursor-foundry-chat
chmod +x start.sh
./start.sh
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765).

Python **3.11–3.13** recommended (`start.sh` prefers those). Python 3.14 may fail until dependency wheels catch up.

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit FOUNDRY_* values
python -m app.main
```

### Foundry connection

In the UI (or `.env`):

| Field | Example |
| --- | --- |
| Messages URL | `https://YOUR-RESOURCE.services.ai.azure.com/anthropic/v1/messages` |
| API key | your Foundry / Azure AI key |
| Model | `claude-opus-5` (your deployment name) |
| Anthropic version | `2023-06-01` |

Keys entered in the UI are stored locally in `data/bridge-config.json` (gitignored). Prefer `.env` for headless use.

## What gets imported

**Skills** (catalog + full priority bodies, budget-capped):

- `~/.cursor/skills-cursor`
- `~/.cursor/skills`
- `~/.agents/skills`
- `~/.claude/skills`
- Cursor plugin skill caches under `~/.cursor/plugins/cache`

**Settings:** Cursor-relevant keys from `~/Library/Application Support/Cursor/User/settings.json` (`cursor.*`, editor/terminal/workbench, etc.).

## Write-back behavior

When “Write replies into Cursor transcript” is on, each turn:

1. Appends a user + assistant message (+ `turn_ended`) to the chat’s agent transcript JSONL
2. Updates Cursor’s `conversation-search.db` FTS body / `updated_at`

Bridged assistant messages are prefixed with `[foundry-bridge]`.

**Caveats (please read):**

- This targets **agent transcript** sync (the JSONL Cursor uses for agent history / search). Live Composer bubble UI may need a refresh / reopen to show new turns.
- Do not run write-back against a chat Cursor is actively rewriting if you can avoid races; prefer continuing when the agent turn is idle.
- This tool never needs your Cursor account password; it only reads/writes local files Cursor already stores on disk.

## API (local)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/status` | Connection + skills summary |
| POST | `/api/connect` | Save Foundry config + smoke test |
| GET | `/api/chats` | List agent transcripts |
| GET | `/api/chats/{id}` | Load thread |
| POST | `/api/chat` | Send message (optional write-back) |
| GET | `/api/skills` | Skill catalog |
| GET | `/api/cursor-settings` | Filtered settings snapshot |

## Security

- Runs on `127.0.0.1` by default — do not expose to the public internet without auth.
- Never commit `.env` or `data/bridge-config.json`.
- Write-back mutates Cursor local DBs/files; keep backups if you customize aggressively.

## Development

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8765
```

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

Unofficial community project. Not affiliated with Anysphere (Cursor) or Microsoft. Cursor’s on-disk formats can change; if write-back breaks after an update, open an issue.

# Portage backlog (after Cursor-parity phases 0–3)

Shipped in this program: bottom composer, workspace agent tools + diffs, rich Cursor/Claude tool cards, `@` mentions, checkpoints + Reject restore.

## Shipped follow-ups

- Colorized `+`/`−` lines in the diff drawer
- Allowlisted `run_command` under workspace cwd (no shell; git limited to status/diff/log/…)
- Symlink-safe workspace walks for grep / file index
- Checkpoint list UI in the chat header (restore with confirm)
- Effort selector + visible thinking (summarized display when the model supports it)
- Plan mode (read-only tools) and `spawn_subagent` for focused research
- Chat mechanics: edit & resubmit, regenerate, fork, rewind, copy, ⌥↑ prompt recall
- Prompt outline, Compact summary, and edit-branch ‹ › version switching
- Soft apply mode (propose patches until Accept / Discard)
- Per-turn `apply_patch` write budget (6 calls)
- Streaming token deltas inside Foundry tool rounds
- Message attachments (text files + images)

## Explicitly out of scope (for now)

- Integrated terminal panel
- Full git blame / PR review UI
- LSP / IntelliSense / multi-tab code editor
- Background Agents / Bugbot / Browser tooling
- Full Cursor Settings sync
- Writing structured `tool_use` blocks back into Cursor JSONL (writeback stays text-safe)

## Still nice later

- Full branch tree map UI (beyond per-message ‹ ›)
- Bedrock tool-round streaming (Foundry streams today; AWS falls back to one-shot rounds)

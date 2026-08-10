# Portage backlog (after Cursor-parity phases 0–3)

Shipped in this program: bottom composer, workspace agent tools + diffs, rich Cursor/Claude tool cards, `@` mentions, checkpoints + Reject restore.

## Explicitly out of scope (for now)

Do not start these until phases 0–3 are stable in daily use:

- Integrated terminal panel
- Full git blame / PR review UI
- LSP / IntelliSense / multi-tab code editor
- Background Agents / Bugbot / Browser tooling
- Full Cursor Settings sync
- Writing structured `tool_use` blocks back into Cursor JSONL (writeback stays text-safe)

## Nice follow-ups

- Streaming token deltas inside a single tool round (not just per text block)
- Allowlisted `run_command` under workspace cwd
- Apply proposed patches without writing until user confirms (soft apply mode)
- Checkpoint list UI in chat header

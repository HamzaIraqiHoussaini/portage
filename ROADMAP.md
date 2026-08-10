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
- Soft apply / Propose mode (propose patches until Accept / Discard)
- Per-turn `apply_patch` write budget (6 successful calls)
- Streaming token deltas inside Foundry tool rounds
- Message attachments (text files + images; server caps; images Foundry-only)
- Propose Accept conflict check (`before_hash`) when the file changed on disk
- Composer workspace linking cue; sidebar Claude Code + Imported filters
- Diff drawer Accept / Discard when reviewing pending proposals
- Bedrock tool-round streaming (live deltas during AWS agent tool rounds)
- Edit branch map panel (Branches) for switching forks beyond per-message ‹ ›

## Explicitly out of scope (for now)

- Integrated terminal panel
- Full git blame / PR review UI
- LSP / IntelliSense / multi-tab code editor
- Background Agents / Bugbot / Browser tooling
- Full Cursor Settings sync
- Writing structured `tool_use` blocks back into Cursor JSONL (writeback stays text-safe)

## Still nice later

_(Empty for now — open a new review pass or pick a fresh feature when you want more.)_

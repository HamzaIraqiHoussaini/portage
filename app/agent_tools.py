"""Workspace-sandboxed filesystem tools for the Portage agent loop."""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

MAX_READ_BYTES = 200_000
MAX_WRITE_BYTES = 400_000
MAX_GREP_HITS = 40
MAX_LIST_ENTRIES = 80
MAX_DIFF_CHARS = 24_000
MAX_CMD_OUTPUT = 40_000
MAX_CMD_TIMEOUT = 30
MAX_PATCHES_PER_TURN = 6

ALLOWED_COMMANDS = frozenset(
    {
        "python",
        "python3",
        "node",
        "npm",
        "npx",
        "pip",
        "pip3",
        "pytest",
        "cargo",
        "go",
        "make",
        "git",
        "rg",
        "ls",
        "cat",
        "wc",
        "head",
        "tail",
        "echo",
        "pwd",
        "which",
    }
)
GIT_ALLOWED_SUBCOMMANDS = frozenset(
    {"status", "diff", "log", "show", "ls-files", "rev-parse", "branch", "describe"}
)

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_dir",
        "description": "List files and folders under a path relative to the linked workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path inside the workspace (default '.').",
                }
            },
        },
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the linked workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "grep",
        "description": "Search for a regex pattern in text files under the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Relative file or directory to search (default '.').",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "apply_patch",
        "description": (
            "Create or overwrite a text file with the given content. "
            "Prefer full-file content for reliability. Returns a unified diff."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string", "description": "Full new file contents."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run an allowlisted command in the linked workspace (no shell). "
            "Allowed binaries: python, node, npm, git (status/diff/log/…), pytest, cargo, go, make, rg, ls, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Argv list, e.g. [\"git\", \"status\", \"--short\"].",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "spawn_subagent",
        "description": (
            "Spawn a focused subagent to research or explore part of the workspace in parallel. "
            "Pass a clear prompt and optional short label. The subagent returns a text summary. "
            "Use for investigation only — it cannot nest further subagents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Task instructions for the subagent.",
                },
                "label": {
                    "type": "string",
                    "description": "Short UI label (e.g. 'auth routes').",
                },
            },
            "required": ["prompt"],
        },
    },
]

WRITE_TOOLS = frozenset({"apply_patch"})
SUBAGENT_TOOLS = frozenset({"spawn_subagent"})


class ToolError(RuntimeError):
    pass


def resolve_in_workspace(workspace: str, rel: str | None) -> Path:
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise ToolError(f"Workspace missing: {root}")
    raw = (rel or ".").strip() or "."
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", raw):
        raise ToolError("Absolute paths are not allowed; use a path relative to the workspace.")
    if "\0" in raw:
        raise ToolError("Invalid path")
    # Resolve through symlinks, then re-check containment.
    target = (root / raw).resolve()
    if not target.is_relative_to(root):
        raise ToolError("Path escapes the linked workspace.")
    return target


def rel_to_workspace(workspace: str, path: Path) -> str:
    root = Path(workspace).expanduser().resolve()
    try:
        return str(path.resolve().relative_to(root)).replace("\\", "/")
    except ValueError:
        return path.name


def unified_diff(path_label: str, before: str, after: str) -> str:
    a = before.splitlines(keepends=True)
    b = after.splitlines(keepends=True)
    if not a and not before:
        a = []
    diff = difflib.unified_diff(
        a,
        b,
        fromfile=f"a/{path_label}",
        tofile=f"b/{path_label}",
        lineterm="",
    )
    text = "\n".join(diff)
    if len(text) > MAX_DIFF_CHARS:
        return text[: MAX_DIFF_CHARS - 20] + "\n… (diff truncated)"
    return text


def execute_tool(
    name: str,
    raw_input: dict[str, Any] | None,
    *,
    workspace: str,
    mode: str = "agent",
    soft_apply: bool = False,
) -> dict[str, Any]:
    """Run one tool. Returns {content, file_change?}."""
    args = raw_input if isinstance(raw_input, dict) else {}
    if name in SUBAGENT_TOOLS:
        raise ToolError("spawn_subagent is handled by the agent loop, not execute_tool")
    if mode == "plan" and name in WRITE_TOOLS:
        raise ToolError("Plan mode is read-only — switch to Agent mode to edit files.")
    if name == "list_dir":
        return _list_dir(workspace, str(args.get("path") or "."))
    if name == "read_file":
        return _read_file(workspace, str(args.get("path") or ""))
    if name == "grep":
        return _grep(workspace, str(args.get("pattern") or ""), str(args.get("path") or "."))
    if name == "apply_patch":
        return _apply_patch(
            workspace,
            str(args.get("path") or ""),
            str(args.get("content") or ""),
            dry_run=soft_apply or mode == "soft",
        )
    if name == "run_command":
        return _run_command(workspace, args.get("command"))
    raise ToolError(f"Unknown tool: {name}")


def iter_workspace_files(workspace: str, start: Path | None = None, *, limit: int = 400):
    """Yield files under workspace without following symlink directories outside the root."""
    root = Path(workspace).expanduser().resolve()
    base = start or root
    if not base.exists():
        return
    stack = [base]
    seen = 0
    while stack and seen < limit:
        current = stack.pop()
        try:
            if current.is_symlink():
                real = current.resolve()
                if not real.is_relative_to(root):
                    continue
                current = real
            if current.is_file():
                if any(part.startswith(".") for part in current.relative_to(root).parts):
                    continue
                yield current
                seen += 1
                continue
            if not current.is_dir():
                continue
            for child in sorted(current.iterdir(), key=lambda p: p.name.lower()):
                if child.name.startswith("."):
                    continue
                if child.is_symlink():
                    try:
                        real = child.resolve()
                    except OSError:
                        continue
                    if not real.is_relative_to(root):
                        continue
                    if real.is_dir():
                        stack.append(real)
                    elif real.is_file():
                        yield real
                        seen += 1
                        if seen >= limit:
                            return
                elif child.is_dir():
                    stack.append(child)
                elif child.is_file():
                    yield child
                    seen += 1
                    if seen >= limit:
                        return
        except OSError:
            continue


def _list_dir(workspace: str, rel: str) -> dict[str, Any]:
    path = resolve_in_workspace(workspace, rel)
    if not path.exists():
        raise ToolError(f"Not found: {rel}")
    if not path.is_dir():
        raise ToolError(f"Not a directory: {rel}")
    entries: list[str] = []
    try:
        children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        raise ToolError(str(e)) from e
    for child in children[:MAX_LIST_ENTRIES]:
        if child.name.startswith("."):
            continue
        mark = "/" if child.is_dir() else ""
        entries.append(f"{child.name}{mark}")
    more = max(0, len(children) - MAX_LIST_ENTRIES)
    body = "\n".join(entries) or "(empty)"
    if more:
        body += f"\n… and {more} more"
    return {"content": body}


def _read_file(workspace: str, rel: str) -> dict[str, Any]:
    if not rel.strip():
        raise ToolError("path is required")
    path = resolve_in_workspace(workspace, rel)
    if not path.is_file():
        raise ToolError(f"Not a file: {rel}")
    data = path.read_bytes()
    if len(data) > MAX_READ_BYTES:
        data = data[:MAX_READ_BYTES]
        truncated = True
    else:
        truncated = False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError("File is not valid UTF-8 text") from None
    if truncated:
        text += "\n… (truncated)"
    return {"content": text}


def _grep(workspace: str, pattern: str, rel: str) -> dict[str, Any]:
    if not pattern:
        raise ToolError("pattern is required")
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ToolError(f"Invalid regex: {e}") from e
    root = resolve_in_workspace(workspace, rel)
    hits: list[str] = []
    files: list[Path] = []
    if root.is_file():
        files = [root]
    elif root.is_dir():
        for p in iter_workspace_files(workspace, root, limit=400):
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".pyc"}:
                continue
            files.append(p)
    else:
        raise ToolError(f"Not found: {rel}")

    for path in files:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:2048]:
            continue
        try:
            text = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                label = rel_to_workspace(workspace, path)
                hits.append(f"{label}:{i}: {line.strip()[:200]}")
                if len(hits) >= MAX_GREP_HITS:
                    return {"content": "\n".join(hits) + "\n… (hit limit)"}
    return {"content": "\n".join(hits) if hits else "(no matches)"}


def _run_command(workspace: str, command: Any) -> dict[str, Any]:
    import shutil
    import subprocess

    if not isinstance(command, list) or not command:
        raise ToolError("command must be a non-empty argv array")
    argv = [str(x) for x in command]
    if any("\0" in a for a in argv):
        raise ToolError("Invalid argument")
    binary = Path(argv[0]).name
    if binary not in ALLOWED_COMMANDS:
        raise ToolError(f"Command not allowed: {binary}")
    if binary == "git":
        sub = next((a for a in argv[1:] if not a.startswith("-")), "")
        if sub not in GIT_ALLOWED_SUBCOMMANDS:
            raise ToolError(
                f"git subcommand not allowed: {sub or '(missing)'}. "
                f"Allowed: {', '.join(sorted(GIT_ALLOWED_SUBCOMMANDS))}"
            )
    resolved = shutil.which(binary)
    if not resolved:
        raise ToolError(f"Command not found on PATH: {binary}")
    cwd = Path(workspace).expanduser().resolve()
    if not cwd.is_dir():
        raise ToolError("Workspace missing")
    try:
        proc = subprocess.run(  # noqa: S603 — argv only, allowlisted binary, no shell
            [resolved, *argv[1:]],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=MAX_CMD_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"Command timed out after {MAX_CMD_TIMEOUT}s") from e
    except OSError as e:
        raise ToolError(str(e)) from e
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    if len(out) > MAX_CMD_OUTPUT:
        out = out[:MAX_CMD_OUTPUT] + "\n… (truncated)"
    header = f"$ {' '.join(argv)}\n(exit {proc.returncode})\n"
    return {"content": header + (out.strip() or "(no output)")}


def _apply_patch(
    workspace: str,
    rel: str,
    content: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not rel.strip():
        raise ToolError("path is required")
    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        raise ToolError(f"content exceeds {MAX_WRITE_BYTES} bytes")
    path = resolve_in_workspace(workspace, rel)
    root = Path(workspace).expanduser().resolve()
    if not path.is_relative_to(root):
        raise ToolError("Path escapes the linked workspace.")
    before = ""
    existed = path.exists()
    if existed:
        if not path.is_file():
            raise ToolError("Cannot overwrite a directory")
        before = path.read_text(encoding="utf-8", errors="replace")
    label = rel_to_workspace(workspace, path)
    diff = unified_diff(label, before, content)
    change = {
        "path": label,
        "op": "update" if existed else "create",
        "diff": diff,
    }
    if dry_run:
        change["pending"] = True
        change["content"] = content
        summary = (
            f"Proposed {'update' if existed else 'create'} for {label} "
            f"({len(content)} chars) — soft apply, not written yet"
        )
        return {"content": summary, "file_change": change}

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    summary = f"{'Updated' if existed else 'Created'} {label} ({len(content)} chars)"
    return {"content": summary, "file_change": change}


def apply_pending_patch(workspace: str, *, path: str, content: str) -> dict[str, Any]:
    """Write a previously proposed soft-apply patch to disk."""
    return _apply_patch(workspace, path, content, dry_run=False)


def anthropic_tools_payload(
    *,
    mode: str = "agent",
    allow_subagents: bool = True,
) -> list[dict[str, Any]]:
    tools = list(TOOL_DEFINITIONS)
    # soft mode keeps apply_patch but writes are deferred (dry_run).
    if mode == "plan":
        tools = [t for t in tools if t["name"] not in WRITE_TOOLS]
    if not allow_subagents:
        tools = [t for t in tools if t["name"] not in SUBAGENT_TOOLS]
    return tools


def bedrock_tool_config(*, mode: str = "agent", allow_subagents: bool = True) -> dict[str, Any]:
    tools = []
    for t in anthropic_tools_payload(mode=mode, allow_subagents=allow_subagents):
        tools.append(
            {
                "toolSpec": {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": {"json": t["input_schema"]},
                }
            }
        )
    return {"tools": tools}


def index_workspace_files(workspace: str, *, limit: int = 400) -> list[str]:
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        return []
    out = [rel_to_workspace(workspace, p) for p in iter_workspace_files(workspace, root, limit=limit)]
    return sorted(out)


def expand_mentions(message: str, workspace: str | None, *, max_files: int = 6, max_chars: int = 40_000) -> str:
    """Expand @path mentions into file contents for the model."""
    if not workspace or "@" not in message:
        return message
    paths = re.findall(r"@([^\s@]+)", message)
    if not paths:
        return message
    chunks = [message, "", "# Mentioned files"]
    used = 0
    budget = max_chars
    for rel in paths:
        if used >= max_files:
            break
        clean = rel.strip(".,;:()[]{}\"'")
        try:
            path = resolve_in_workspace(workspace, clean)
        except ToolError:
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > budget:
            text = text[:budget] + "\n… (truncated)"
        budget -= len(text)
        used += 1
        chunks.append(f"\n## {clean}\n```\n{text}\n```")
        if budget <= 0:
            break
    return "\n".join(chunks) if used else message

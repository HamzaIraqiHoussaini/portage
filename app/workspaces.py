"""Workspace linking + local (non-Cursor) conversations."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .providers import load_bridge_config, save_bridge_config

SAFE_CHAT_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass
class LocalMessage:
    role: str
    text: str
    usage: dict[str, int] | None = None
    blocks: list[dict[str, Any]] | None = None
    file_changes: list[dict[str, Any]] | None = None
    checkpoint_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class LocalChat:
    id: str
    title: str
    created_at: int
    updated_at: int
    workspace: str | None = None
    source: str = "local"
    messages: list[LocalMessage] = field(default_factory=list)
    usage_last: dict[str, int] = field(default_factory=dict)
    usage_total: dict[str, int] = field(default_factory=dict)
    # ChatGPT-style edit branches: msg_id -> {active, variants:[{text, following}]}
    branches: dict[str, Any] = field(default_factory=dict)
    # When materialized from a Cursor agent chat, keep pointers for write-back.
    origin_chat_id: str | None = None
    origin_transcript_path: str | None = None

    def to_summary(self) -> dict[str, Any]:
        preview = ""
        for m in reversed(self.messages):
            if m.text.strip():
                preview = m.text.strip()[:160]
                break
        return {
            "id": self.id,
            "title": self.title,
            "updated_at": self.updated_at,
            "project": self.workspace or "local",
            "transcript_path": "",
            "source": self.source,
            "preview": preview,
            "workspace": self.workspace,
            "usage_last": dict(self.usage_last or {}),
            "usage_total": dict(self.usage_total or {}),
            "deletable": True,
        }

    def to_dict(self) -> dict[str, Any]:
        msgs: list[dict[str, Any]] = []
        for idx, m in enumerate(self.messages):
            item: dict[str, Any] = {
                "role": m.role,
                "text": m.text,
                "index": idx,
                "id": m.id,
            }
            if m.usage:
                item["usage"] = dict(m.usage)
            if m.blocks:
                item["blocks"] = list(m.blocks)
            if m.file_changes:
                item["file_changes"] = list(m.file_changes)
            if m.checkpoint_id:
                item["checkpoint_id"] = m.checkpoint_id
            branch = (self.branches or {}).get(m.id)
            if isinstance(branch, dict) and branch.get("variants"):
                item["branch"] = {
                    "active": int(branch.get("active") or 0),
                    "count": len(branch.get("variants") or []),
                }
            msgs.append(item)
        return {
            **self.to_summary(),
            "messages": msgs,
            "branches": dict(self.branches or {}),
        }


def cursor_detection(settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    projects = s.projects_dir
    support = s.cursor_support_dir
    detected = projects.is_dir() or support.is_dir()
    transcript_count = 0
    if projects.is_dir():
        transcript_count = sum(1 for _ in projects.glob("*/agent-transcripts/*/*.jsonl"))
    return {
        "detected": detected,
        "projects_dir": str(projects),
        "support_dir": str(support),
        "transcript_hint_count": transcript_count,
        "settings_exists": s.settings_json.exists(),
    }


def list_workspaces(settings: Settings | None = None) -> list[dict[str, Any]]:
    cfg = load_bridge_config(settings)
    raw = cfg.get("workspaces") or []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            path = Path(item).expanduser()
            out.append({"path": str(path), "name": path.name, "exists": path.is_dir()})
        elif isinstance(item, dict) and item.get("path"):
            path = Path(str(item["path"])).expanduser()
            out.append(
                {
                    "path": str(path),
                    "name": item.get("name") or path.name,
                    "exists": path.is_dir(),
                }
            )
    return out


def add_workspace(path_str: str, settings: Settings | None = None) -> dict[str, Any]:
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    cfg = load_bridge_config(settings)
    workspaces = list(cfg.get("workspaces") or [])
    normalized = []
    for item in workspaces:
        p = item if isinstance(item, str) else (item or {}).get("path")
        if p:
            normalized.append({"path": str(Path(p).expanduser().resolve()), "name": Path(p).name})
    if not any(w["path"] == str(path) for w in normalized):
        normalized.append({"path": str(path), "name": path.name})
    save_bridge_config({"workspaces": normalized}, settings)
    return {"path": str(path), "name": path.name, "exists": True}


def ensure_workspace(path_str: str | None, settings: Settings | None = None) -> str | None:
    """Link a folder if needed and return its absolute path, or None on failure."""
    if not path_str or not str(path_str).strip():
        return None
    try:
        return add_workspace(path_str, settings)["path"]
    except (FileNotFoundError, NotADirectoryError, OSError, PermissionError):
        return None


def probe_disk_access(settings: Settings | None = None) -> dict[str, Any]:
    """Best-effort macOS access checks — FDA is not always required."""
    s = settings or get_settings()
    home = Path.home()

    def _can_list(path: Path) -> bool:
        try:
            next(path.iterdir(), None)
            return True
        except StopIteration:
            return True
        except PermissionError:
            return False
        except OSError as e:
            # EPERM / EACCES
            if getattr(e, "errno", None) in (1, 13):
                return False
            return path.exists()

    def _can_read_file(path: Path) -> bool:
        try:
            if not path.exists():
                return False
            with path.open("rb") as f:
                f.read(1)
            return True
        except PermissionError:
            return False
        except OSError as e:
            if getattr(e, "errno", None) in (1, 13):
                return False
            return False

    docs = home / "Documents"
    desktop = home / "Desktop"
    downloads = home / "Downloads"
    cursor_projects = s.projects_dir
    state_db = s.state_vscdb

    return {
        "documents": _can_list(docs) if docs.exists() else None,
        "desktop": _can_list(desktop) if desktop.exists() else None,
        "downloads": _can_list(downloads) if downloads.exists() else None,
        "cursor_projects": _can_list(cursor_projects) if cursor_projects.exists() else None,
        "cursor_state_db": _can_read_file(state_db) if state_db.exists() else None,
        "bundle_id": "app.portage.desktop",
        "notes": [
            "Cursor-style access: link folders via Settings (or auto-link from a Cursor chat). "
            "macOS then grants Files and Folders for that path — prefer this over Full Disk Access.",
            "Full Disk Access is only needed if Portage cannot read/write "
            "~/Library/Application Support/Cursor (write-back) or protected folders after linking.",
            "Portage.app is currently unsigned; if macOS forgets permissions after updates, "
            "re-add Portage under System Settings → Privacy & Security → Files and Folders "
            "(and Full Disk Access only if write-back still fails).",
        ],
    }


def remove_workspace(path_str: str, settings: Settings | None = None) -> None:
    path = str(Path(path_str).expanduser().resolve())
    cfg = load_bridge_config(settings)
    workspaces = []
    for item in cfg.get("workspaces") or []:
        p = item if isinstance(item, str) else (item or {}).get("path")
        if not p:
            continue
        if str(Path(p).expanduser().resolve()) != path:
            workspaces.append(
                {"path": str(Path(p).expanduser().resolve()), "name": Path(p).name}
            )
    save_bridge_config({"workspaces": workspaces}, settings)


def validate_chat_id(chat_id: str) -> str:
    cid = (chat_id or "").strip()
    if not SAFE_CHAT_ID_RE.fullmatch(cid):
        raise ValueError("Invalid chat id")
    return cid


def _chat_path(chat_id: str, settings: Settings) -> Path:
    cid = validate_chat_id(chat_id)
    root = settings.conversations_dir.resolve()
    path = (root / f"{cid}.json").resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid chat path")
    return path


def resolve_allowed_workspace(
    workspace: str | None,
    *,
    settings: Settings | None = None,
    allow_stored: str | None = None,
) -> str | None:
    """Only allow linked workspaces (or the chat's already-stored workspace)."""
    if not workspace or not str(workspace).strip():
        return None
    try:
        target = str(Path(workspace).expanduser().resolve())
    except OSError as e:
        raise ValueError(f"Invalid workspace path: {e}") from e

    allowed = {w["path"] for w in list_workspaces(settings)}
    if allow_stored:
        try:
            allowed.add(str(Path(allow_stored).expanduser().resolve()))
        except OSError:
            pass
    if target not in allowed:
        raise ValueError("Workspace must be a linked folder")
    return target


def list_local_chats(settings: Settings | None = None) -> list[dict[str, Any]]:
    s = settings or get_settings()
    out: list[dict[str, Any]] = []
    for path in sorted(s.conversations_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        chat = _from_dict(data)
        out.append(chat.to_summary())
    return out


def load_local_chat(chat_id: str, settings: Settings | None = None) -> LocalChat | None:
    s = settings or get_settings()
    try:
        path = _chat_path(chat_id, s)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        return _from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def delete_local_chat(chat_id: str, settings: Settings | None = None) -> None:
    """Delete a Portage-local chat and its pending patches / checkpoints.

    Does not touch Cursor / Claude Code app transcripts.
    """
    import shutil

    from . import pending_patches

    s = settings or get_settings()
    path = _chat_path(chat_id, s)
    if not path.is_file():
        raise FileNotFoundError(
            "Only local Portage chats can be deleted. "
            "Cursor / Claude Code linked chats stay in those apps."
        )
    path.unlink(missing_ok=True)
    try:
        pending_patches.discard_all(chat_id, s)
    except ValueError:
        pass
    cp_root = (s.conversations_dir.parent / "checkpoints" / chat_id).resolve()
    root = (s.conversations_dir.parent / "checkpoints").resolve()
    if cp_root.is_dir() and cp_root.is_relative_to(root):
        shutil.rmtree(cp_root, ignore_errors=True)


def create_local_chat(
    *,
    title: str | None = None,
    workspace: str | None = None,
    settings: Settings | None = None,
) -> LocalChat:
    s = settings or get_settings()
    ws = None
    if workspace:
        ws = resolve_allowed_workspace(workspace, settings=s)
    now = int(time.time() * 1000)
    chat = LocalChat(
        id=str(uuid.uuid4()),
        title=(title or "New conversation").strip() or "New conversation",
        created_at=now,
        updated_at=now,
        workspace=ws,
        source="local",
        messages=[],
    )
    _save(chat, s)
    return chat


def append_local_exchange(
    chat_id: str,
    *,
    user_text: str,
    assistant_text: str,
    settings: Settings | None = None,
    usage: dict[str, int] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    file_changes: list[dict[str, Any]] | None = None,
    checkpoint_id: str | None = None,
) -> LocalChat:
    s = settings or get_settings()
    chat = load_local_chat(chat_id, s)
    if not chat:
        raise FileNotFoundError(f"Local chat not found: {chat_id}")
    chat.messages.append(LocalMessage(role="user", text=user_text))
    assistant_usage = None
    if usage:
        assistant_usage = {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
    chat.messages.append(
        LocalMessage(
            role="assistant",
            text=assistant_text,
            usage=assistant_usage,
            blocks=list(blocks) if blocks else None,
            file_changes=list(file_changes) if file_changes else None,
            checkpoint_id=checkpoint_id,
        )
    )
    chat.updated_at = int(time.time() * 1000)
    if chat.title in ("New conversation", chat.id) and user_text.strip():
        line = user_text.strip().splitlines()[0]
        chat.title = (line[:80] + ("…" if len(line) > 80 else ""))
    if assistant_usage:
        chat.usage_last = dict(assistant_usage)
        total = dict(chat.usage_total or {})
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            total[key] = int(total.get(key) or 0) + int(assistant_usage.get(key) or 0)
        chat.usage_total = total
    sync_branch_following(chat)
    _save(chat, s)
    return chat


def truncate_local_chat(
    chat_id: str,
    *,
    keep_until: int,
    settings: Settings | None = None,
) -> LocalChat:
    """Keep messages[0..keep_until] inclusive; drop everything after."""
    s = settings or get_settings()
    chat = load_local_chat(chat_id, s)
    if not chat:
        raise FileNotFoundError(f"Local chat not found: {chat_id}")
    if keep_until < -1 or keep_until >= len(chat.messages):
        raise ValueError("keep_until out of range")
    chat.messages = chat.messages[: keep_until + 1]
    chat.updated_at = int(time.time() * 1000)
    _save(chat, s)
    return chat


def _serialize_message(m: LocalMessage) -> dict[str, Any]:
    item: dict[str, Any] = {"role": m.role, "text": m.text, "id": m.id}
    if m.usage:
        item["usage"] = dict(m.usage)
    if m.blocks:
        item["blocks"] = list(m.blocks)
    if m.file_changes:
        item["file_changes"] = list(m.file_changes)
    if m.checkpoint_id:
        item["checkpoint_id"] = m.checkpoint_id
    return item


def _deserialize_message(m: dict[str, Any]) -> LocalMessage | None:
    if m.get("role") not in ("user", "assistant", "system"):
        return None
    usage_raw = m.get("usage") if isinstance(m.get("usage"), dict) else None
    usage = None
    if usage_raw:
        usage = {
            k: int(usage_raw.get(k) or 0)
            for k in ("input_tokens", "output_tokens", "total_tokens")
        }
    return LocalMessage(
        role=str(m.get("role")),
        text=str(m.get("text") or ""),
        usage=usage,
        blocks=list(m["blocks"]) if isinstance(m.get("blocks"), list) else None,
        file_changes=list(m["file_changes"]) if isinstance(m.get("file_changes"), list) else None,
        checkpoint_id=str(m["checkpoint_id"]) if m.get("checkpoint_id") else None,
        id=str(m["id"]) if m.get("id") else str(uuid.uuid4()),
    )


def edit_local_user_message(
    chat_id: str,
    *,
    index: int,
    text: str,
    settings: Settings | None = None,
) -> LocalChat:
    """Replace a user message and drop all messages after it (edit & resubmit).

    Preserves the discarded path as a ChatGPT-style branch variant.
    """
    s = settings or get_settings()
    chat = load_local_chat(chat_id, s)
    if not chat:
        raise FileNotFoundError(f"Local chat not found: {chat_id}")
    if index < 0 or index >= len(chat.messages):
        raise ValueError("message index out of range")
    msg = chat.messages[index]
    if msg.role != "user":
        raise ValueError("Only user messages can be edited")
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("Edited message cannot be empty")

    following = [_serialize_message(m) for m in chat.messages[index + 1 :]]
    group = chat.branches.get(msg.id) if isinstance(chat.branches, dict) else None
    if not isinstance(group, dict):
        group = {"active": 0, "variants": []}
    variants = list(group.get("variants") or [])
    # Snapshot current path before mutating.
    if not variants:
        variants.append({"text": msg.text, "following": following})
    else:
        active = int(group.get("active") or 0)
        if 0 <= active < len(variants):
            variants[active] = {"text": msg.text, "following": following}
        else:
            variants.append({"text": msg.text, "following": following})
    variants.append({"text": cleaned, "following": []})
    chat.branches[msg.id] = {"active": len(variants) - 1, "variants": variants}

    msg.text = cleaned
    chat.messages = chat.messages[: index + 1]
    chat.updated_at = int(time.time() * 1000)
    if chat.title in ("New conversation", chat.id) and cleaned:
        line = cleaned.splitlines()[0]
        chat.title = line[:80] + ("…" if len(line) > 80 else "")
    _save(chat, s)
    return chat


def sync_branch_following(chat: LocalChat) -> None:
    """After a new assistant lands, store following messages on the active edit branch."""
    if not chat.messages:
        return
    # Find the last user message that has a branch group.
    for i in range(len(chat.messages) - 1, -1, -1):
        msg = chat.messages[i]
        if msg.role != "user":
            continue
        group = (chat.branches or {}).get(msg.id)
        if not isinstance(group, dict) or not group.get("variants"):
            continue
        active = int(group.get("active") or 0)
        variants = list(group.get("variants") or [])
        if not (0 <= active < len(variants)):
            continue
        following = [_serialize_message(m) for m in chat.messages[i + 1 :]]
        variants[active] = {"text": msg.text, "following": following}
        chat.branches[msg.id] = {"active": active, "variants": variants}
        return


def switch_message_branch(
    chat_id: str,
    *,
    message_id: str,
    variant_index: int,
    settings: Settings | None = None,
) -> LocalChat:
    """Switch a user message to another stored edit branch (ChatGPT prev/next)."""
    s = settings or get_settings()
    chat = load_local_chat(chat_id, s)
    if not chat:
        raise FileNotFoundError(f"Local chat not found: {chat_id}")
    idx = next((i for i, m in enumerate(chat.messages) if m.id == message_id), -1)
    if idx < 0:
        raise ValueError("Message not found in chat")
    msg = chat.messages[idx]
    if msg.role != "user":
        raise ValueError("Branches only apply to user messages")
    group = (chat.branches or {}).get(message_id)
    if not isinstance(group, dict):
        raise ValueError("No branches for this message")
    variants = list(group.get("variants") or [])
    if variant_index < 0 or variant_index >= len(variants):
        raise ValueError("variant_index out of range")

    # Save current path into the active variant before switching.
    active = int(group.get("active") or 0)
    if 0 <= active < len(variants):
        variants[active] = {
            "text": msg.text,
            "following": [_serialize_message(m) for m in chat.messages[idx + 1 :]],
        }

    chosen = variants[variant_index] if isinstance(variants[variant_index], dict) else {}
    msg.text = str(chosen.get("text") or msg.text)
    following_raw = chosen.get("following") if isinstance(chosen.get("following"), list) else []
    restored: list[LocalMessage] = []
    for item in following_raw:
        if isinstance(item, dict):
            parsed = _deserialize_message(item)
            if parsed:
                restored.append(parsed)
    chat.messages = chat.messages[: idx + 1] + restored
    chat.branches[message_id] = {"active": variant_index, "variants": variants}
    chat.updated_at = int(time.time() * 1000)
    _save(chat, s)
    return chat


def compact_local_chat(
    chat_id: str,
    *,
    summary: str,
    keep_last: int = 6,
    settings: Settings | None = None,
) -> LocalChat:
    """Replace older turns with a summary message; keep the last keep_last messages."""
    s = settings or get_settings()
    chat = load_local_chat(chat_id, s)
    if not chat:
        raise FileNotFoundError(f"Local chat not found: {chat_id}")
    keep_last = max(0, min(int(keep_last), len(chat.messages)))
    if len(chat.messages) <= keep_last + 1:
        raise ValueError("Not enough messages to compact")
    kept = chat.messages[-keep_last:] if keep_last else []
    summary_text = (summary or "").strip()
    if not summary_text:
        raise ValueError("Summary is empty")
    marker = LocalMessage(
        role="user",
        text=(
            "# Compacted earlier conversation\n\n"
            "The following is a concise summary of earlier turns. "
            "Continue from here with full fidelity to this summary.\n\n"
            f"{summary_text}"
        ),
    )
    ack = LocalMessage(
        role="assistant",
        text="Understood — I'll treat the compacted summary as prior context and continue from the recent turns below.",
    )
    chat.messages = [marker, ack, *kept]
    chat.updated_at = int(time.time() * 1000)
    _save(chat, s)
    return chat


def drop_last_assistant(
    chat_id: str,
    *,
    settings: Settings | None = None,
) -> tuple[LocalChat, str]:
    """Remove the trailing assistant message for regenerate. Returns (chat, last_user_text)."""
    s = settings or get_settings()
    chat = load_local_chat(chat_id, s)
    if not chat:
        raise FileNotFoundError(f"Local chat not found: {chat_id}")
    if not chat.messages or chat.messages[-1].role != "assistant":
        raise ValueError("Last message is not an assistant reply to regenerate")
    chat.messages.pop()
    if not chat.messages or chat.messages[-1].role != "user":
        raise ValueError("No user message found before the assistant reply")
    user_text = chat.messages[-1].text
    chat.updated_at = int(time.time() * 1000)
    _save(chat, s)
    return chat, user_text


def append_local_assistant(
    chat_id: str,
    *,
    assistant_text: str,
    settings: Settings | None = None,
    usage: dict[str, int] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    file_changes: list[dict[str, Any]] | None = None,
    checkpoint_id: str | None = None,
) -> LocalChat:
    """Append only an assistant message (after edit/regenerate where user already exists)."""
    s = settings or get_settings()
    chat = load_local_chat(chat_id, s)
    if not chat:
        raise FileNotFoundError(f"Local chat not found: {chat_id}")
    assistant_usage = None
    if usage:
        assistant_usage = {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
    chat.messages.append(
        LocalMessage(
            role="assistant",
            text=assistant_text,
            usage=assistant_usage,
            blocks=list(blocks) if blocks else None,
            file_changes=list(file_changes) if file_changes else None,
            checkpoint_id=checkpoint_id,
        )
    )
    chat.updated_at = int(time.time() * 1000)
    if assistant_usage:
        chat.usage_last = dict(assistant_usage)
        total = dict(chat.usage_total or {})
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            total[key] = int(total.get(key) or 0) + int(assistant_usage.get(key) or 0)
        chat.usage_total = total
    sync_branch_following(chat)
    _save(chat, s)
    return chat


def fork_local_chat(
    chat_id: str,
    *,
    up_to_index: int | None = None,
    settings: Settings | None = None,
) -> LocalChat:
    """Create a new local chat copying messages through up_to_index (inclusive)."""
    s = settings or get_settings()
    src = load_local_chat(chat_id, s)
    if not src:
        raise FileNotFoundError(f"Local chat not found: {chat_id}")
    end = len(src.messages) - 1 if up_to_index is None else up_to_index
    if end < -1 or end >= len(src.messages):
        raise ValueError("up_to_index out of range")
    now = int(time.time() * 1000)
    copied = [
        LocalMessage(
            role=m.role,
            text=m.text,
            usage=dict(m.usage) if m.usage else None,
            blocks=list(m.blocks) if m.blocks else None,
            file_changes=list(m.file_changes) if m.file_changes else None,
            checkpoint_id=None,
        )
        for m in src.messages[: end + 1]
    ]
    title = src.title
    if not title.startswith("Fork ·"):
        title = f"Fork · {title}"[:120]
    chat = LocalChat(
        id=str(uuid.uuid4()),
        title=title,
        created_at=now,
        updated_at=now,
        workspace=src.workspace,
        source="local",
        messages=copied,
        usage_last=dict(src.usage_last or {}),
        usage_total={},
        origin_chat_id=src.origin_chat_id,
        origin_transcript_path=src.origin_transcript_path,
    )
    _save(chat, s)
    return chat


def workspace_context_block(workspace: str | None, max_files: int = 40) -> str:
    if not workspace:
        return ""
    path = Path(workspace).expanduser()
    if not path.is_dir():
        return f"# Linked workspace\n\nPath missing: `{workspace}`\n"

    entries: list[str] = []
    try:
        for child in sorted(path.iterdir())[:max_files]:
            kind = "dir" if child.is_dir() else "file"
            entries.append(f"- ({kind}) `{child.name}`")
    except OSError as e:
        return f"# Linked workspace\n\nCould not read `{path}`: {e}\n"

    more = ""
    try:
        total = sum(1 for _ in path.iterdir())
        if total > max_files:
            more = f"\n_(+{total - max_files} more entries not listed)_\n"
    except OSError:
        pass

    return (
        f"# Linked workspace\n\nPath: `{path}`\n\n"
        f"Top-level entries:\n" + "\n".join(entries) + more
    )


def _from_dict(data: dict[str, Any]) -> LocalChat:
    msgs: list[LocalMessage] = []
    for m in data.get("messages") or []:
        if not isinstance(m, dict):
            continue
        parsed = _deserialize_message(m)
        if parsed:
            msgs.append(parsed)
    usage_last = data.get("usage_last") if isinstance(data.get("usage_last"), dict) else {}
    usage_total = data.get("usage_total") if isinstance(data.get("usage_total"), dict) else {}
    branches = data.get("branches") if isinstance(data.get("branches"), dict) else {}
    origin_chat_id = data.get("origin_chat_id")
    origin_transcript_path = data.get("origin_transcript_path")
    return LocalChat(
        id=str(data.get("id") or uuid.uuid4()),
        title=str(data.get("title") or "Conversation"),
        created_at=int(data.get("created_at") or 0),
        updated_at=int(data.get("updated_at") or 0),
        workspace=data.get("workspace"),
        source=str(data.get("source") or "local"),
        messages=msgs,
        usage_last={k: int(usage_last.get(k) or 0) for k in ("input_tokens", "output_tokens", "total_tokens")},
        usage_total={k: int(usage_total.get(k) or 0) for k in ("input_tokens", "output_tokens", "total_tokens")},
        branches=dict(branches),
        origin_chat_id=str(origin_chat_id) if origin_chat_id else None,
        origin_transcript_path=str(origin_transcript_path) if origin_transcript_path else None,
    )


def _save(chat: LocalChat, settings: Settings) -> None:
    path = _chat_path(chat.id, settings)
    messages = [_serialize_message(m) for m in chat.messages]
    payload = {
        "id": chat.id,
        "title": chat.title,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
        "workspace": chat.workspace,
        "source": chat.source,
        "messages": messages,
        "usage_last": dict(chat.usage_last or {}),
        "usage_total": dict(chat.usage_total or {}),
        "branches": dict(chat.branches or {}),
    }
    if chat.origin_chat_id:
        payload["origin_chat_id"] = chat.origin_chat_id
    if chat.origin_transcript_path:
        payload["origin_transcript_path"] = chat.origin_transcript_path
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

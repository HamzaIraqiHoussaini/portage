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
        }

    def to_dict(self) -> dict[str, Any]:
        msgs: list[dict[str, Any]] = []
        for m in self.messages:
            item: dict[str, Any] = {"role": m.role, "text": m.text}
            if m.usage:
                item["usage"] = dict(m.usage)
            if m.blocks:
                item["blocks"] = list(m.blocks)
            if m.file_changes:
                item["file_changes"] = list(m.file_changes)
            if m.checkpoint_id:
                item["checkpoint_id"] = m.checkpoint_id
            msgs.append(item)
        return {
            **self.to_summary(),
            "messages": msgs,
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
        if m.get("role") not in ("user", "assistant", "system"):
            continue
        usage_raw = m.get("usage") if isinstance(m.get("usage"), dict) else None
        usage = None
        if usage_raw:
            usage = {
                k: int(usage_raw.get(k) or 0)
                for k in ("input_tokens", "output_tokens", "total_tokens")
            }
        msgs.append(
            LocalMessage(
                role=str(m.get("role")),
                text=str(m.get("text") or ""),
                usage=usage,
                blocks=list(m["blocks"]) if isinstance(m.get("blocks"), list) else None,
                file_changes=list(m["file_changes"])
                if isinstance(m.get("file_changes"), list)
                else None,
                checkpoint_id=str(m["checkpoint_id"]) if m.get("checkpoint_id") else None,
            )
        )
    usage_last = data.get("usage_last") if isinstance(data.get("usage_last"), dict) else {}
    usage_total = data.get("usage_total") if isinstance(data.get("usage_total"), dict) else {}
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
    )


def _save(chat: LocalChat, settings: Settings) -> None:
    path = _chat_path(chat.id, settings)
    messages: list[dict[str, Any]] = []
    for m in chat.messages:
        item: dict[str, Any] = {"role": m.role, "text": m.text}
        if m.usage:
            item["usage"] = dict(m.usage)
        if m.blocks:
            item["blocks"] = list(m.blocks)
        if m.file_changes:
            item["file_changes"] = list(m.file_changes)
        if m.checkpoint_id:
            item["checkpoint_id"] = m.checkpoint_id
        messages.append(item)
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
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

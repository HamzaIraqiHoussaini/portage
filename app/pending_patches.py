"""Soft-apply pending file patches (propose until Accept)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .workspaces import SAFE_CHAT_ID_RE


def _root(settings: Settings) -> Path:
    root = settings.conversations_dir.parent / "pending_patches"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _chat_dir(chat_id: str, settings: Settings) -> Path:
    if not SAFE_CHAT_ID_RE.match(str(chat_id or "")):
        raise ValueError("Invalid chat id")
    dest = (_root(settings) / chat_id).resolve()
    root = _root(settings).resolve()
    if not dest.is_relative_to(root):
        raise ValueError("Invalid chat path")
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def store_pending(
    *,
    chat_id: str,
    path: str,
    content: str,
    op: str,
    diff: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Persist a proposed patch; returns metadata without full content."""
    s = settings or get_settings()
    patch_id = str(uuid.uuid4())
    dest = _chat_dir(chat_id, s)
    meta = {
        "id": patch_id,
        "chat_id": chat_id,
        "path": path,
        "op": op or "update",
        "diff": diff or "",
        "created_at": int(time.time() * 1000),
        "status": "pending",
    }
    payload = {**meta, "content": content}
    (dest / f"{patch_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return meta


def load_pending(chat_id: str, patch_id: str, settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    if not SAFE_CHAT_ID_RE.match(str(patch_id or "")):
        raise ValueError("Invalid patch id")
    dest = _chat_dir(chat_id, s)
    path = (dest / f"{patch_id}.json").resolve()
    if not path.is_relative_to(dest.resolve()) or not path.is_file():
        raise FileNotFoundError("Pending patch not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FileNotFoundError("Pending patch not found")
    return data


def discard_pending(chat_id: str, patch_id: str, settings: Settings | None = None) -> None:
    s = settings or get_settings()
    if not SAFE_CHAT_ID_RE.match(str(patch_id or "")):
        raise ValueError("Invalid patch id")
    dest = _chat_dir(chat_id, s)
    path = (dest / f"{patch_id}.json").resolve()
    if path.is_relative_to(dest.resolve()) and path.is_file():
        path.unlink(missing_ok=True)


def list_pending(chat_id: str, settings: Settings | None = None) -> list[dict[str, Any]]:
    s = settings or get_settings()
    dest = _chat_dir(chat_id, s)
    out: list[dict[str, Any]] = []
    for f in sorted(dest.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        out.append(
            {
                "id": data.get("id") or f.stem,
                "path": data.get("path"),
                "op": data.get("op"),
                "diff": data.get("diff") or "",
                "created_at": data.get("created_at"),
                "status": data.get("status") or "pending",
                "pending": True,
            }
        )
    return out

"""Soft-apply pending file patches (propose until Accept)."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .workspaces import SAFE_CHAT_ID_RE

# Drop unaccepted proposals after a week so disk does not grow unbounded.
PENDING_TTL_MS = 7 * 24 * 60 * 60 * 1000
DIFF_PREVIEW_CHARS = 180


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _root(settings: Settings) -> Path:
    root = settings.conversations_dir.parent / "pending_patches"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _chat_dir(chat_id: str, settings: Settings, *, create: bool = True) -> Path:
    if not SAFE_CHAT_ID_RE.match(str(chat_id or "")):
        raise ValueError("Invalid chat id")
    dest = (_root(settings) / chat_id).resolve()
    root = _root(settings).resolve()
    if not dest.is_relative_to(root):
        raise ValueError("Invalid chat path")
    if create:
        dest.mkdir(parents=True, exist_ok=True)
    return dest


def _is_expired(data: dict[str, Any], *, now_ms: int | None = None) -> bool:
    created = int(data.get("created_at") or 0)
    if created <= 0:
        return False
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    return (now - created) > PENDING_TTL_MS


def prune_expired(
    chat_id: str | None = None,
    settings: Settings | None = None,
) -> int:
    """Remove pending patches older than PENDING_TTL_MS. Returns count removed."""
    s = settings or get_settings()
    now = int(time.time() * 1000)
    removed = 0
    roots: list[Path]
    if chat_id:
        try:
            roots = [_chat_dir(chat_id, s, create=False)]
        except ValueError:
            return 0
    else:
        roots = [p for p in _root(s).iterdir() if p.is_dir()]

    for dest in roots:
        if not dest.is_dir():
            continue
        for f in list(dest.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                f.unlink(missing_ok=True)
                removed += 1
                continue
            if not isinstance(data, dict) or _is_expired(data, now_ms=now):
                f.unlink(missing_ok=True)
                removed += 1
        # Drop empty chat dirs after prune.
        try:
            if dest.is_dir() and not any(dest.iterdir()):
                dest.rmdir()
        except OSError:
            pass
    return removed


def store_pending(
    *,
    chat_id: str,
    path: str,
    content: str,
    op: str,
    diff: str,
    before_hash: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Persist a proposed patch; returns metadata without full content."""
    s = settings or get_settings()
    prune_expired(chat_id, s)
    patch_id = str(uuid.uuid4())
    dest = _chat_dir(chat_id, s)
    meta = {
        "id": patch_id,
        "chat_id": chat_id,
        "path": path,
        "op": op or "update",
        "diff": diff or "",
        "before_hash": before_hash or "",
        "created_at": int(time.time() * 1000),
        "status": "pending",
    }
    payload = {**meta, "content": content}
    (dest / f"{patch_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    # List responses omit full diffs; keep them only in the stored payload.
    return {
        "id": patch_id,
        "chat_id": chat_id,
        "path": path,
        "op": meta["op"],
        "before_hash": meta["before_hash"],
        "created_at": meta["created_at"],
        "status": "pending",
        "pending": True,
    }


def load_pending(chat_id: str, patch_id: str, settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    if not SAFE_CHAT_ID_RE.match(str(patch_id or "")):
        raise ValueError("Invalid patch id")
    dest = _chat_dir(chat_id, s, create=False)
    path = (dest / f"{patch_id}.json").resolve()
    if not path.is_relative_to(dest.resolve()) or not path.is_file():
        raise FileNotFoundError("Pending patch not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FileNotFoundError("Pending patch not found")
    if _is_expired(data):
        path.unlink(missing_ok=True)
        raise FileNotFoundError("Pending patch expired")
    return data


def discard_pending(chat_id: str, patch_id: str, settings: Settings | None = None) -> None:
    s = settings or get_settings()
    if not SAFE_CHAT_ID_RE.match(str(patch_id or "")):
        raise ValueError("Invalid patch id")
    dest = _chat_dir(chat_id, s, create=False)
    path = (dest / f"{patch_id}.json").resolve()
    if path.is_relative_to(dest.resolve()) and path.is_file():
        path.unlink(missing_ok=True)


def discard_all(chat_id: str, settings: Settings | None = None) -> int:
    s = settings or get_settings()
    try:
        dest = _chat_dir(chat_id, s, create=False)
    except ValueError:
        return 0
    if not dest.is_dir():
        return 0
    n = 0
    for f in dest.glob("*.json"):
        f.unlink(missing_ok=True)
        n += 1
    try:
        if dest.is_dir() and not any(dest.iterdir()):
            shutil.rmtree(dest, ignore_errors=True)
    except OSError:
        pass
    return n


def list_pending(
    chat_id: str,
    settings: Settings | None = None,
    *,
    include_diff: bool = False,
) -> list[dict[str, Any]]:
    s = settings or get_settings()
    prune_expired(chat_id, s)
    try:
        dest = _chat_dir(chat_id, s, create=False)
    except ValueError:
        return []
    if not dest.is_dir():
        return []
    out: list[dict[str, Any]] = []
    now = int(time.time() * 1000)
    for f in sorted(dest.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if _is_expired(data, now_ms=now):
            f.unlink(missing_ok=True)
            continue
        diff = str(data.get("diff") or "")
        item: dict[str, Any] = {
            "id": data.get("id") or f.stem,
            "path": data.get("path"),
            "op": data.get("op"),
            "before_hash": data.get("before_hash") or "",
            "created_at": data.get("created_at"),
            "status": data.get("status") or "pending",
            "pending": True,
            "patch_id": data.get("id") or f.stem,
        }
        if include_diff:
            item["diff"] = diff
        else:
            item["has_diff"] = bool(diff.strip())
            item["diff_preview"] = diff[:DIFF_PREVIEW_CHARS]
        out.append(item)
    return out

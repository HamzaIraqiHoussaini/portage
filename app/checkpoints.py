"""Per-turn workspace checkpoints for restore / reject."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .agent_tools import resolve_in_workspace, rel_to_workspace, ToolError


def _checkpoints_root(settings: Settings) -> Path:
    root = settings.conversations_dir.parent / "checkpoints"
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_checkpoint(
    *,
    chat_id: str,
    workspace: str,
    paths: list[str] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Snapshot listed workspace files (or empty snapshot metadata)."""
    s = settings or get_settings()
    cp_id = str(uuid.uuid4())
    dest = _checkpoints_root(s) / chat_id / cp_id
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for rel in paths or []:
        try:
            src = resolve_in_workspace(workspace, rel)
        except ToolError:
            continue
        if not src.is_file():
            continue
        target = dest / rel.replace("\\", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        saved.append(rel.replace("\\", "/"))
    meta = {
        "id": cp_id,
        "chat_id": chat_id,
        "workspace": workspace,
        "created_at": int(time.time() * 1000),
        "paths": saved,
    }
    (dest / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def snapshot_before_write(
    *,
    chat_id: str,
    workspace: str,
    rel_path: str,
    settings: Settings | None = None,
    checkpoint_id: str | None = None,
) -> str:
    """Ensure a checkpoint exists and capture one file before overwrite."""
    from .workspaces import SAFE_CHAT_ID_RE

    s = settings or get_settings()
    if not SAFE_CHAT_ID_RE.match(str(chat_id or "")):
        raise ToolError("Invalid chat id for checkpoint")
    cp_id = checkpoint_id or str(uuid.uuid4())
    if not SAFE_CHAT_ID_RE.match(str(cp_id)):
        raise ToolError("Invalid checkpoint id")
    dest = (_checkpoints_root(s) / chat_id / cp_id).resolve()
    root = (_checkpoints_root(s) / chat_id).resolve()
    if not dest.is_relative_to(root):
        raise ToolError("Invalid checkpoint path")
    dest.mkdir(parents=True, exist_ok=True)
    meta_path = dest / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {"id": cp_id, "chat_id": chat_id, "workspace": workspace, "paths": []}
    else:
        meta = {
            "id": cp_id,
            "chat_id": chat_id,
            "workspace": workspace,
            "created_at": int(time.time() * 1000),
            "paths": [],
            "created_paths": [],
        }
    # Only snapshot after path is proven inside the workspace.
    try:
        src = resolve_in_workspace(workspace, rel_path)
    except ToolError:
        raise
    rel = rel_to_workspace(workspace, src) if src.exists() else str(rel_path).replace("\\", "/").lstrip("/")
    if ".." in Path(rel).parts or rel.startswith("/"):
        raise ToolError("Invalid relative path for checkpoint")
    if rel in meta.get("paths", []):
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return cp_id

    target = (dest / rel).resolve()
    if not target.is_relative_to(dest):
        raise ToolError("Checkpoint path escapes checkpoint dir")
    target.parent.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        shutil.copy2(src, target)
        meta.setdefault("paths", []).append(rel)
    else:
        # New file: empty sentinel so Reject can unlink after create.
        target.write_text("", encoding="utf-8")
        meta.setdefault("paths", []).append(rel)
        meta.setdefault("created_paths", []).append(rel)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return cp_id


def restore_checkpoint(
    checkpoint_id: str,
    *,
    chat_id: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    from .workspaces import SAFE_CHAT_ID_RE, resolve_allowed_workspace

    s = settings or get_settings()
    if not SAFE_CHAT_ID_RE.match(str(chat_id or "")):
        raise ValueError("Invalid chat id")
    if not SAFE_CHAT_ID_RE.match(str(checkpoint_id or "")):
        raise ValueError("Invalid checkpoint id")
    dest = (_checkpoints_root(s) / chat_id / checkpoint_id).resolve()
    root = (_checkpoints_root(s) / chat_id).resolve()
    if not dest.is_relative_to(root):
        raise ValueError("Invalid checkpoint path")
    meta_path = dest / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError("Checkpoint not found")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    workspace = meta.get("workspace") or ""
    # Re-validate workspace is still linked (or was stored for this chat).
    try:
        workspace = resolve_allowed_workspace(workspace, settings=s, allow_stored=workspace) or ""
    except ValueError as e:
        raise ValueError(f"Checkpoint workspace no longer allowed: {e}") from e
    if not workspace:
        raise ValueError("Checkpoint has no workspace")
    restored: list[str] = []
    deleted: list[str] = []
    created = set(meta.get("created_paths") or [])
    for rel in meta.get("paths") or []:
        if not isinstance(rel, str) or not rel.strip() or rel.startswith("/") or ".." in Path(rel).parts:
            continue
        snap = (dest / rel).resolve()
        if not snap.is_relative_to(dest):
            continue
        try:
            target = resolve_in_workspace(workspace, rel)
        except ToolError:
            continue
        if rel in created and (not snap.exists() or snap.stat().st_size == 0):
            if target.exists() and target.is_file():
                target.unlink()
                deleted.append(rel)
            continue
        if snap.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snap, target)
            restored.append(rel)
    return {"id": checkpoint_id, "restored": restored, "deleted": deleted, "workspace": workspace}


def list_checkpoints(chat_id: str, settings: Settings | None = None) -> list[dict[str, Any]]:
    s = settings or get_settings()
    root = _checkpoints_root(s) / chat_id
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        meta_path = child / "meta.json"
        if not meta_path.is_file():
            continue
        try:
            out.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (
    agent_loop,
    agent_tools,
    chats,
    checkpoints,
    importers,
    pending_patches,
    providers,
    skills,
    workspaces,
    writeback,
)
from .config import ROOT, get_settings
from .security import (
    MAX_HISTORY_MESSAGES,
    MAX_IMPORT_BYTES,
    MAX_IMPORT_CHATS,
    LocalhostOnlyMiddleware,
    SecurityHeadersMiddleware,
    clamp_message,
    read_upload_limited,
    validate_foundry_url,
)

app = FastAPI(title="Portage", version="0.4.0")
app.add_middleware(LocalhostOnlyMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

STATIC_DIR = ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ConnectBody(BaseModel):
    provider: str = "foundry"  # foundry | aws
    foundry_messages_url: str | None = None
    foundry_api_key: str | None = None
    foundry_model: str | None = None
    anthropic_version: str = "2023-06-01"
    aws_region: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    aws_model_id: str | None = None
    cursor_link_enabled: bool | None = None
    claude_code_link_enabled: bool | None = None
    writeback_enabled: bool | None = None


class ChatBody(BaseModel):
    chat_id: str
    message: str = Field(min_length=1)
    writeback: bool = True
    source: str | None = None
    workspace: str | None = None
    transcript_path: str | None = None
    effort: str | None = None
    mode: str | None = None
    thinking_mode: str | None = None
    # Conversation mechanics (local chats)
    edit_index: int | None = None  # edit user msg at index, truncate after, resubmit
    regenerate: bool = False  # drop last assistant, resubmit last user
    user_already_saved: bool = False  # don't re-append user on persist
    attachments: list[dict[str, Any]] | None = None


class AttachmentIn(BaseModel):
    name: str = ""
    mime: str = "application/octet-stream"
    text: str | None = None
    data_base64: str | None = None


class TruncateBody(BaseModel):
    keep_until: int
    transcript_path: str | None = None
    source: str | None = None


class EditMessageBody(BaseModel):
    index: int
    text: str = Field(min_length=1)


class ForkChatBody(BaseModel):
    up_to_index: int | None = None


class CompactBody(BaseModel):
    keep_last: int = 6


class SwitchBranchBody(BaseModel):
    message_id: str
    variant_index: int


class WorkspaceBody(BaseModel):
    path: str


class NewChatBody(BaseModel):
    title: str | None = None
    workspace: str | None = None


class CursorLinkBody(BaseModel):
    enabled: bool


class ClaudeCodeLinkBody(BaseModel):
    enabled: bool


class WritebackBody(BaseModel):
    enabled: bool


class PatchApplyBody(BaseModel):
    chat_id: str
    patch_id: str
    workspace: str | None = None


class PatchRejectBody(BaseModel):
    chat_id: str
    patch_id: str


class PatchApplyAllBody(BaseModel):
    chat_id: str
    workspace: str | None = None
    patch_ids: list[str] | None = None


def _settings():
    s = get_settings()
    return providers.apply_saved_config_to_settings(s)


def _overlay_settings(payload: dict[str, Any]):
    s = _settings()
    for key, value in payload.items():
        if hasattr(s, key) and value is not None:
            setattr(s, key, value)
    return s


def _workspace_for_request(
    *,
    body_workspace: str | None,
    settings,
    stored: str | None = None,
    transcript_path: str | None = None,
) -> str | None:
    """Resolve linked workspace; auto-link Cursor project folder when missing."""
    try:
        ws = workspaces.resolve_allowed_workspace(
            body_workspace or stored,
            settings=settings,
            allow_stored=stored,
        )
    except ValueError:
        ws = None
    if ws:
        return ws
    if body_workspace:
        linked = workspaces.ensure_workspace(body_workspace, settings)
        if linked:
            return linked
    if transcript_path:
        suggested = chats.suggest_workspace_from_transcript(transcript_path)
        return workspaces.ensure_workspace(suggested, settings)
    return None


def _load_external_thread(chat_id: str, s, transcript_path: str | None = None):
    """Load a Cursor / Claude Code thread if present."""
    transcript = _safe_transcript_path(transcript_path, s) if transcript_path else None
    if s.cursor_link_enabled and workspaces.cursor_detection(s)["detected"]:
        thread = chats.load_thread(chat_id, s, transcript_path=transcript)
        if thread:
            return thread
    if getattr(s, "claude_code_link_enabled", True) and importers.claude_code_detection(s)["detected"]:
        return importers.load_claude_thread(chat_id, s, transcript_path=transcript)
    return None


def _ensure_mutable_local_chat(
    chat_id: str,
    s,
    *,
    transcript_path: str | None = None,
    preserve_id: bool = True,
):
    """Return a local chat for mutation; materialize external transcripts in place (no Fork · copy)."""
    local = workspaces.load_local_chat(chat_id, s)
    if local:
        return local
    thread = _load_external_thread(chat_id, s, transcript_path=transcript_path)
    if not thread:
        raise FileNotFoundError(f"Chat not found: {chat_id}")
    return importers.fork_thread_to_local(
        thread, settings=s, chat_id=chat_id, preserve_id=preserve_id
    )


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status():
    s = _settings()
    skill_list = skills.list_skills(s)
    cursor = workspaces.cursor_detection(s)
    claude = importers.claude_code_detection(s)
    antigravity = importers.antigravity_detection()
    cursor_link = bool(s.cursor_link_enabled) and cursor["detected"]
    claude_link = bool(getattr(s, "claude_code_link_enabled", True)) and claude["detected"]
    return {
        "connected": providers.provider_connected(s),
        "provider": s.provider,
        "model": s.aws_model_id if s.provider == "aws" else s.foundry_model,
        "messages_url": s.foundry_messages_url,
        "anthropic_version": s.anthropic_version,
        "aws_region": s.aws_region,
        "aws_model_id": s.aws_model_id,
        "has_key": bool(s.foundry_api_key) if s.provider != "aws" else bool(s.aws_access_key_id),
        "writeback_enabled": s.writeback_enabled,
        "cursor_link_enabled": bool(s.cursor_link_enabled),
        "claude_code_link_enabled": bool(getattr(s, "claude_code_link_enabled", True)),
        "cursor": cursor,
        "claude_code": claude,
        "antigravity": antigravity,
        "cursor_active": cursor_link,
        "claude_code_active": claude_link,
        "skills_count": len(skill_list),
        "priority_skills": [x.name for x in skill_list if x.priority][:40],
        "supports_extended_thinking": (
            s.provider == "foundry"
            or "claude" in ((s.aws_model_id if s.provider == "aws" else s.foundry_model) or "").lower()
        ),
        "settings_path": str(s.settings_json),
        "workspaces": workspaces.list_workspaces(s),
        "disk_access": workspaces.probe_disk_access(s),
        "import_sources": [
            {"id": "cursor", "label": "Cursor", "mode": "auto"},
            {"id": "claude-code", "label": "Claude Code", "mode": "auto"},
            {"id": "chatgpt", "label": "ChatGPT", "mode": "file"},
            {"id": "antigravity", "label": "Antigravity", "mode": "file"},
        ],
    }


@app.post("/api/connect")
async def connect(body: ConnectBody):
    provider = (body.provider or "foundry").strip().lower()
    if provider not in ("foundry", "aws"):
        raise HTTPException(status_code=400, detail="provider must be 'foundry' or 'aws'")

    payload: dict[str, Any] = {"provider": provider}
    if body.cursor_link_enabled is not None:
        payload["cursor_link_enabled"] = body.cursor_link_enabled
    if body.claude_code_link_enabled is not None:
        payload["claude_code_link_enabled"] = body.claude_code_link_enabled
    if body.writeback_enabled is not None:
        payload["writeback_enabled"] = body.writeback_enabled

    if provider == "foundry":
        if body.foundry_messages_url:
            try:
                payload["foundry_messages_url"] = validate_foundry_url(body.foundry_messages_url)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        if body.foundry_api_key:
            payload["foundry_api_key"] = body.foundry_api_key.strip()
        if body.foundry_model:
            payload["foundry_model"] = body.foundry_model.strip()
        if body.anthropic_version:
            payload["anthropic_version"] = body.anthropic_version.strip()
    else:
        if body.aws_region:
            payload["aws_region"] = body.aws_region.strip()
        if body.aws_access_key_id:
            payload["aws_access_key_id"] = body.aws_access_key_id.strip()
        if body.aws_secret_access_key:
            payload["aws_secret_access_key"] = body.aws_secret_access_key.strip()
        if body.aws_session_token and body.aws_session_token.strip():
            payload["aws_session_token"] = body.aws_session_token.strip()
        if body.aws_model_id:
            payload["aws_model_id"] = body.aws_model_id.strip()

    probe = _overlay_settings(payload)
    if probe.provider == "foundry":
        try:
            validate_foundry_url(probe.foundry_messages_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        result = await providers.test_connection(probe)
    except providers.ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    providers.save_bridge_config(payload)
    get_settings.cache_clear()
    return {"saved": True, "test": result}


@app.post("/api/cursor-link")
async def set_cursor_link(body: CursorLinkBody):
    providers.save_bridge_config({"cursor_link_enabled": body.enabled})
    get_settings.cache_clear()
    s = _settings()
    return {
        "cursor_link_enabled": bool(s.cursor_link_enabled),
        "cursor": workspaces.cursor_detection(s),
        "cursor_active": bool(s.cursor_link_enabled) and workspaces.cursor_detection(s)["detected"],
    }


@app.post("/api/claude-code-link")
async def set_claude_code_link(body: ClaudeCodeLinkBody):
    providers.save_bridge_config({"claude_code_link_enabled": body.enabled})
    get_settings.cache_clear()
    s = _settings()
    claude = importers.claude_code_detection(s)
    return {
        "claude_code_link_enabled": bool(getattr(s, "claude_code_link_enabled", True)),
        "claude_code": claude,
        "claude_code_active": bool(getattr(s, "claude_code_link_enabled", True)) and claude["detected"],
    }


@app.post("/api/writeback")
async def set_writeback(body: WritebackBody):
    providers.save_bridge_config({"writeback_enabled": body.enabled})
    get_settings.cache_clear()
    s = _settings()
    return {"writeback_enabled": bool(s.writeback_enabled)}


@app.get("/api/skills")
async def list_skills():
    s = _settings()
    return {"skills": [x.to_dict() for x in skills.list_skills(s)]}


@app.get("/api/cursor-settings")
async def cursor_settings():
    return skills.cursor_settings_snapshot(_settings())


@app.get("/api/workspaces")
async def get_workspaces():
    return {"workspaces": workspaces.list_workspaces(_settings())}


class RestoreBody(BaseModel):
    chat_id: str
    checkpoint_id: str


@app.get("/api/workspace/files")
async def workspace_files(path: str | None = None):
    s = _settings()
    try:
        ws = workspaces.resolve_allowed_workspace(path, settings=s)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ws:
        return {"files": [], "workspace": None}
    return {"workspace": ws, "files": agent_tools.index_workspace_files(ws)}


@app.get("/api/chats/{chat_id}/checkpoints")
async def chat_checkpoints(chat_id: str):
    try:
        workspaces.validate_chat_id(chat_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"checkpoints": checkpoints.list_checkpoints(chat_id, _settings())}


@app.post("/api/checkpoints/restore")
async def restore_checkpoint(body: RestoreBody):
    try:
        workspaces.validate_chat_id(body.chat_id)
        result = checkpoints.restore_checkpoint(
            body.checkpoint_id, chat_id=body.chat_id, settings=_settings()
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e
    return result


@app.get("/api/chats/{chat_id}/pending-patches")
async def list_pending_patches(chat_id: str, include_diff: bool = False):
    try:
        workspaces.validate_chat_id(chat_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "patches": pending_patches.list_pending(
            chat_id, _settings(), include_diff=include_diff
        )
    }


@app.post("/api/patches/apply")
async def apply_pending_patch(body: PatchApplyBody):
    s = _settings()
    try:
        workspaces.validate_chat_id(body.chat_id)
        workspaces.validate_chat_id(body.patch_id)
        patch = pending_patches.load_pending(body.chat_id, body.patch_id, s)
        local = workspaces.load_local_chat(body.chat_id, s)
        workspace = workspaces.resolve_allowed_workspace(
            body.workspace or (local.workspace if local else None),
            settings=s,
            allow_stored=local.workspace if local else None,
        )
        if not workspace:
            raise ValueError("Link a workspace before applying patches.")
        # Snapshot current file before accepting soft apply.
        checkpoints.snapshot_before_write(
            chat_id=body.chat_id,
            workspace=workspace,
            rel_path=str(patch.get("path") or ""),
            settings=s,
        )
        result = agent_tools.apply_pending_patch(
            workspace,
            path=str(patch.get("path") or ""),
            content=str(patch.get("content") or ""),
            expected_before_hash=str(patch.get("before_hash") or "") or None,
        )
        pending_patches.discard_pending(body.chat_id, body.patch_id, s)
        fc = result.get("file_change") if isinstance(result.get("file_change"), dict) else {}
        return {
            "ok": True,
            "patch_id": body.patch_id,
            "path": fc.get("path") or patch.get("path"),
            "op": fc.get("op") or patch.get("op"),
            "diff": fc.get("diff") or patch.get("diff"),
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except agent_tools.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except (ValueError, agent_tools.ToolError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/patches/apply-all")
async def apply_all_pending_patches(body: PatchApplyAllBody):
    s = _settings()
    try:
        workspaces.validate_chat_id(body.chat_id)
        local = workspaces.load_local_chat(body.chat_id, s)
        workspace = workspaces.resolve_allowed_workspace(
            body.workspace or (local.workspace if local else None),
            settings=s,
            allow_stored=local.workspace if local else None,
        )
        if not workspace:
            raise ValueError("Link a workspace before applying patches.")
        ids = body.patch_ids
        if not ids:
            ids = [p["id"] for p in pending_patches.list_pending(body.chat_id, s) if p.get("id")]
        applied: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for pid in ids:
            try:
                workspaces.validate_chat_id(pid)
                patch = pending_patches.load_pending(body.chat_id, pid, s)
                checkpoints.snapshot_before_write(
                    chat_id=body.chat_id,
                    workspace=workspace,
                    rel_path=str(patch.get("path") or ""),
                    settings=s,
                )
                result = agent_tools.apply_pending_patch(
                    workspace,
                    path=str(patch.get("path") or ""),
                    content=str(patch.get("content") or ""),
                    expected_before_hash=str(patch.get("before_hash") or "") or None,
                )
                pending_patches.discard_pending(body.chat_id, pid, s)
                fc = result.get("file_change") if isinstance(result.get("file_change"), dict) else {}
                applied.append(
                    {
                        "patch_id": pid,
                        "path": fc.get("path") or patch.get("path"),
                        "op": fc.get("op") or patch.get("op"),
                    }
                )
            except agent_tools.ConflictError as e:
                errors.append({"patch_id": pid, "error": str(e), "code": "conflict"})
            except Exception as e:  # noqa: BLE001
                errors.append({"patch_id": pid, "error": str(e)})
        return {"ok": not errors, "applied": applied, "errors": errors}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/patches/reject")
async def reject_pending_patch(body: PatchRejectBody):
    s = _settings()
    try:
        workspaces.validate_chat_id(body.chat_id)
        workspaces.validate_chat_id(body.patch_id)
        pending_patches.discard_pending(body.chat_id, body.patch_id, s)
        return {"ok": True, "patch_id": body.patch_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/workspaces")
async def post_workspace(body: WorkspaceBody):
    try:
        item = workspaces.add_workspace(body.path, _settings())
    except (FileNotFoundError, NotADirectoryError, OSError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"workspace": item, "workspaces": workspaces.list_workspaces(_settings())}


@app.post("/api/open-privacy-settings")
async def open_privacy_settings():
    """Open macOS Privacy pane (Files & Folders). FDA is manual if still needed."""
    import subprocess
    import sys

    if sys.platform != "darwin":
        return {"opened": False, "reason": "macOS only"}
    # Prefer Files and Folders (scoped, Cursor-like). Full Disk Access is separate.
    targets = [
        [
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders",
        ],
        ["open", "/System/Library/PreferencePanes/Security.prefPane"],
    ]
    for cmd in targets:
        try:
            subprocess.run(cmd, check=False, timeout=5)
            return {"opened": True, "target": cmd[-1]}
        except (OSError, subprocess.TimeoutExpired):
            continue
    return {"opened": False, "reason": "could not launch System Settings"}


@app.delete("/api/workspaces")
async def delete_workspace(path: str):
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail="path query param required")
    workspaces.remove_workspace(path, _settings())
    return {"workspaces": workspaces.list_workspaces(_settings())}


@app.post("/api/chats/{chat_id}/truncate")
async def truncate_chat(chat_id: str, body: TruncateBody):
    """Rewind conversation: keep messages through keep_until, drop the rest.

    External Cursor/Claude transcripts are materialized once into a local chat
    (same title, no Fork · prefix), then truncated in place.
    """
    s = _settings()
    try:
        workspaces.validate_chat_id(chat_id)
        local = _ensure_mutable_local_chat(
            chat_id, s, transcript_path=body.transcript_path
        )
        chat = workspaces.truncate_local_chat(
            local.id, keep_until=body.keep_until, settings=s
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return chat.to_dict()


@app.post("/api/chats/{chat_id}/edit")
async def edit_chat_message(chat_id: str, body: EditMessageBody):
    """Edit a user message and drop everything after it (ChatGPT/Cursor-style)."""
    s = _settings()
    try:
        workspaces.validate_chat_id(chat_id)
        chat = workspaces.edit_local_user_message(
            chat_id, index=body.index, text=body.text, settings=s
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return chat.to_dict()


@app.post("/api/chats/{chat_id}/fork")
async def fork_chat(chat_id: str, body: ForkChatBody):
    """Fork a chat into a new thread up to a message index (explicit Fork action)."""
    s = _settings()
    try:
        workspaces.validate_chat_id(chat_id)
        local = _ensure_mutable_local_chat(chat_id, s)
        forked = workspaces.fork_local_chat(
            local.id, up_to_index=body.up_to_index, settings=s
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return forked.to_dict()


@app.post("/api/chats/{chat_id}/branch")
async def switch_branch(chat_id: str, body: SwitchBranchBody):
    """Switch between stored edit-branch variants for a user message."""
    s = _settings()
    try:
        workspaces.validate_chat_id(chat_id)
        chat = workspaces.switch_message_branch(
            chat_id,
            message_id=body.message_id,
            variant_index=body.variant_index,
            settings=s,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return chat.to_dict()


@app.post("/api/chats/{chat_id}/compact")
async def compact_chat(chat_id: str, body: CompactBody):
    """Summarize older turns (Claude Code–style compact) and keep the recent tail."""
    s = _settings()
    if not providers.provider_connected(s):
        raise HTTPException(status_code=400, detail="Connect Foundry or AWS first.")
    try:
        workspaces.validate_chat_id(chat_id)
        chat = workspaces.load_local_chat(chat_id, s)
        if not chat:
            raise FileNotFoundError(f"Local chat not found: {chat_id}")
        keep_last = max(2, min(int(body.keep_last or 6), 40))
        if len(chat.messages) <= keep_last + 1:
            raise ValueError("Not enough messages to compact")
        older = chat.messages[:-keep_last]
        transcript_lines: list[str] = []
        for m in older:
            role = m.role.upper()
            text = (m.text or "").strip()
            if not text:
                continue
            if len(text) > 1200:
                text = text[:1200] + "…"
            transcript_lines.append(f"{role}: {text}")
        blob = "\n\n".join(transcript_lines)
        if len(blob) > 60_000:
            blob = blob[:60_000] + "\n…"
        prompt = (
            "Summarize the following chat transcript for continuity. "
            "Preserve decisions, file paths, constraints, and open questions. "
            "Be dense and factual — no fluff.\n\n"
            f"{blob}"
        )
        result = await providers.chat_completion(
            [{"role": "user", "content": prompt}],
            system="You write concise conversation summaries for coding agents.",
            settings=s,
            effort="low",
            thinking_mode="off",
            max_tokens=2048,
        )
        summary = str(result.get("text") or "").strip()
        if not summary:
            raise ValueError("Model returned an empty summary")
        compacted = workspaces.compact_local_chat(
            chat_id, summary=summary, keep_last=keep_last, settings=s
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except providers.ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return compacted.to_dict()


@app.get("/api/chats")
async def list_chats():
    s = _settings()
    local = workspaces.list_local_chats(s)
    external: list[dict[str, Any]] = []
    cursor = workspaces.cursor_detection(s)
    if s.cursor_link_enabled and cursor["detected"]:
        for c in chats.discover_transcripts(s):
            d = c.to_dict()
            d["source"] = "cursor"
            d["deletable"] = False
            external.append(d)
    claude = importers.claude_code_detection(s)
    if getattr(s, "claude_code_link_enabled", True) and claude["detected"]:
        for c in importers.discover_claude_code(s):
            d = c.to_dict()
            d["deletable"] = False
            external.append(d)
    merged = local + external
    merged.sort(key=lambda c: c.get("updated_at") or 0, reverse=True)
    return {
        "chats": merged,
        "cursor": cursor,
        "claude_code": claude,
        "antigravity": importers.antigravity_detection(),
        "cursor_active": bool(s.cursor_link_enabled) and cursor["detected"],
        "claude_code_active": bool(getattr(s, "claude_code_link_enabled", True)) and claude["detected"],
    }


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    """Delete a local Portage chat. Linked Cursor / Claude Code chats cannot be deleted here."""
    s = _settings()
    try:
        workspaces.validate_chat_id(chat_id)
        workspaces.delete_local_chat(chat_id, s)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "id": chat_id}


@app.post("/api/chats/new")
async def new_chat(body: NewChatBody):
    try:
        chat = workspaces.create_local_chat(
            title=body.title,
            workspace=body.workspace,
            settings=_settings(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return chat.to_dict()


@app.post("/api/import")
async def import_chats(file: UploadFile = File(...)):
    """Import ChatGPT / Antigravity / generic JSON or Markdown exports into local chats."""
    try:
        raw = await read_upload_limited(file, max_bytes=MAX_IMPORT_BYTES)
        parsed = importers.detect_and_parse_import(raw, filename=file.filename or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    s = _settings()
    created = []
    for item in parsed[:MAX_IMPORT_CHATS]:
        chat = importers.materialize_as_local(
            title=str(item.get("title") or "Imported chat"),
            messages=list(item.get("messages") or []),
            source=str(item.get("source") or "import"),
            settings=s,
        )
        created.append(chat.to_summary())
    return {"imported": len(created), "chats": created}


def _safe_transcript_path(path_str: str | None, settings) -> str | None:
    """Only allow transcript paths under known Cursor / Claude project roots."""
    if not path_str or not str(path_str).strip():
        return None
    try:
        path = Path(path_str).expanduser().resolve()
    except OSError:
        return None
    roots = []
    if settings.projects_dir.is_dir():
        roots.append(settings.projects_dir.resolve())
    claude_root = Path(settings.claude_home).expanduser() / "projects"
    if claude_root.is_dir():
        roots.append(claude_root.resolve())
    for root in roots:
        try:
            if path.is_relative_to(root) and path.is_file():
                return str(path)
        except (OSError, ValueError):
            continue
    return None


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str, path: str | None = None):
    s = _settings()
    local = workspaces.load_local_chat(chat_id, s)
    if local:
        return local.to_dict()
    transcript = _safe_transcript_path(path, s)
    if s.cursor_link_enabled and workspaces.cursor_detection(s)["detected"]:
        thread = chats.load_thread(chat_id, s, transcript_path=transcript)
        if thread:
            data = thread.to_dict()
            data["source"] = "cursor"
            suggested = thread.summary.suggested_workspace or chats.suggest_workspace_from_transcript(
                thread.summary.transcript_path
            )
            if suggested:
                data["suggested_workspace"] = suggested
            return data
    if getattr(s, "claude_code_link_enabled", True) and importers.claude_code_detection(s)["detected"]:
        thread = importers.load_claude_thread(chat_id, s, transcript_path=transcript)
        if thread:
            return thread.to_dict()
    raise HTTPException(status_code=404, detail="Chat not found")


@app.post("/api/chat")
async def send_chat(body: ChatBody):
    ctx = _prepare_chat(body)
    try:
        result = await providers.chat_completion(
            ctx["merged"],
            system=ctx["system"],
            settings=ctx["settings"],
            effort=ctx.get("effort"),
            thinking_mode=ctx.get("thinking_mode"),
        )
    except providers.ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return _finalize_chat(ctx, result["text"], result.get("usage") or {}, result)


@app.post("/api/chat/stream")
async def send_chat_stream(body: ChatBody, request: Request):
    ctx = _prepare_chat(body)
    disconnected = {"v": False}

    async def event_gen():
        yield _sse(
            {
                "type": "meta",
                "chat_id": ctx["chat_id"],
                "source": ctx["source"],
                "forked": ctx["forked"],
                "skill": ctx["skill_name"],
                "agent": bool(ctx.get("workspace")),
                "mode": ctx.get("mode") or "agent",
            }
        )
        full_parts: list[str] = []
        file_changes: list[dict[str, Any]] = []
        blocks: list[dict[str, Any]] = []
        checkpoint_id: str | None = None
        finalized = False
        aborted = False
        try:
            async for event in agent_loop.run_agent_stream(
                ctx["merged"],
                system=ctx["system"],
                settings=ctx["settings"],
                workspace=ctx.get("workspace"),
                chat_id=ctx["chat_id"] if ctx.get("source") == "local" else None,
                cancel_check=lambda: disconnected["v"],
                effort=ctx.get("effort"),
                thinking_mode=ctx.get("thinking_mode"),
                mode=ctx.get("mode"),
            ):
                if await request.is_disconnected():
                    disconnected["v"] = True
                    aborted = True
                    break
                etype = event.get("type")
                if etype == "status":
                    yield _sse(
                        {
                            "type": "status",
                            "phase": event.get("phase") or "model",
                            "detail": event.get("detail"),
                        }
                    )
                elif etype == "delta":
                    text = str(event.get("text") or "")
                    if text:
                        full_parts.append(text)
                        yield _sse({"type": "delta", "text": text})
                elif etype == "thinking":
                    text = str(event.get("text") or "")
                    if text:
                        yield _sse(
                            {
                                "type": "thinking",
                                "text": text,
                                "subagent_id": event.get("subagent_id"),
                            }
                        )
                elif etype in (
                    "tool_start",
                    "tool_result",
                    "file_change",
                    "checkpoint",
                    "subagent_start",
                    "subagent_delta",
                    "subagent_done",
                ):
                    if etype == "checkpoint" and event.get("checkpoint_id"):
                        checkpoint_id = str(event.get("checkpoint_id"))
                    if etype == "file_change":
                        fc_entry: dict[str, Any] = {
                            "path": event.get("path"),
                            "op": event.get("op"),
                            "diff": event.get("diff") or "",
                        }
                        if event.get("pending"):
                            fc_entry["pending"] = True
                        if event.get("patch_id"):
                            fc_entry["patch_id"] = event.get("patch_id")
                        file_changes.append(fc_entry)
                        if event.get("checkpoint_id"):
                            checkpoint_id = str(event.get("checkpoint_id"))
                    if etype != "checkpoint":
                        yield _sse(event)
                elif etype == "done":
                    assistant_text = str(event.get("text") or "".join(full_parts)).strip()
                    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
                    blocks = event.get("blocks") if isinstance(event.get("blocks"), list) else []
                    fcs = event.get("file_changes") if isinstance(event.get("file_changes"), list) else file_changes
                    checkpoint_id = event.get("checkpoint_id") or checkpoint_id
                    payload = _finalize_chat(
                        ctx,
                        assistant_text,
                        usage,
                        event,
                        blocks=blocks,
                        file_changes=fcs,
                        checkpoint_id=checkpoint_id,
                    )
                    finalized = True
                    yield _sse({"type": "done", **payload})
                elif etype == "error":
                    yield _sse({"type": "error", "detail": event.get("detail") or "Stream failed"})
        except providers.ProviderError as e:
            yield _sse({"type": "error", "detail": str(e)})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "detail": str(e)})
        finally:
            if aborted and not finalized and ctx.get("source") == "local":
                try:
                    _finalize_chat(
                        ctx,
                        "".join(full_parts).strip(),
                        {},
                        {"provider": ctx["settings"].provider},
                        blocks=blocks or None,
                        file_changes=file_changes or None,
                        checkpoint_id=checkpoint_id,
                    )
                except Exception:  # noqa: BLE001
                    pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _as_content_blocks(content: Any) -> list[dict[str, Any]]:
    """Normalize Anthropic message content into a list of content blocks."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        out: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict):
                out.append(block)
            elif isinstance(block, str) and block:
                out.append({"type": "text", "text": block})
        return out
    return [{"type": "text", "text": str(content)}]


def _merge_message_content(prev: Any, cur: Any) -> Any:
    """Merge consecutive same-role contents without dropping multimodal blocks."""
    if isinstance(prev, str) and isinstance(cur, str):
        if not prev:
            return cur
        if not cur:
            return prev
        return prev + "\n\n" + cur
    blocks = _as_content_blocks(prev) + _as_content_blocks(cur)
    return blocks


def _prepare_chat(body: ChatBody) -> dict[str, Any]:
    s = _settings()
    if not providers.provider_connected(s):
        raise HTTPException(status_code=400, detail="Connect Foundry or AWS first.")

    try:
        display_message = clamp_message(body.message)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    skill_name, user_message = skills.parse_slash_command(display_message)
    model_message = user_message if skill_name else display_message
    if skill_name and not model_message:
        model_message = (
            f"(User invoked /{skill_name} with no extra text. "
            "Follow that skill and ask what they need if unclear.)"
        )

    forked = False
    source = (body.source or "").strip().lower()
    local = workspaces.load_local_chat(body.chat_id, s)
    cursor_thread = None
    chat_id = body.chat_id
    transcript = _safe_transcript_path(body.transcript_path, s)
    user_already_saved = bool(body.user_already_saved or body.regenerate or body.edit_index is not None)

    # Edit / regenerate require a mutable local chat.
    if (body.edit_index is not None or body.regenerate) and not local:
        # Fork external transcript to local first.
        cursor_thread = None
        claude_thread = None
        if s.cursor_link_enabled and workspaces.cursor_detection(s)["detected"]:
            cursor_thread = chats.load_thread(body.chat_id, s, transcript_path=transcript)
        if (
            not cursor_thread
            and getattr(s, "claude_code_link_enabled", True)
            and importers.claude_code_detection(s)["detected"]
        ):
            claude_thread = importers.load_claude_thread(
                body.chat_id, s, transcript_path=transcript
            )
        thread = cursor_thread or claude_thread
        if not thread:
            raise HTTPException(status_code=404, detail="Chat not found")
        local = importers.fork_thread_to_local(thread, settings=s)
        chat_id = local.id
        source = "local"
        forked = True
        cursor_thread = None

    if local and body.edit_index is not None:
        try:
            local = workspaces.edit_local_user_message(
                local.id, index=int(body.edit_index), text=display_message, settings=s
            )
            chat_id = local.id
            source = "local"
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    elif local and body.regenerate:
        try:
            local, last_user = workspaces.drop_last_assistant(local.id, settings=s)
            chat_id = local.id
            source = "local"
            display_message = last_user
            skill_name, user_message = skills.parse_slash_command(display_message)
            model_message = user_message if skill_name else display_message
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    if local:
        source = "local"
        history = [
            {"role": m.role, "content": m.text}
            for m in local.messages
            if m.role in ("user", "assistant") and m.text.strip()
        ]
        # After edit/regenerate the user message is already the last history item.
        skip_append_user = bool(
            user_already_saved and history and history[-1]["role"] == "user"
        )
        try:
            workspace = _workspace_for_request(
                body_workspace=body.workspace,
                settings=s,
                stored=local.workspace,
                transcript_path=None,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    else:
        skip_append_user = False
        cursor_thread = None
        claude_thread = None
        if s.cursor_link_enabled and workspaces.cursor_detection(s)["detected"]:
            cursor_thread = chats.load_thread(body.chat_id, s, transcript_path=transcript)
        if (
            not cursor_thread
            and getattr(s, "claude_code_link_enabled", True)
            and importers.claude_code_detection(s)["detected"]
        ):
            claude_thread = importers.load_claude_thread(
                body.chat_id, s, transcript_path=transcript
            )

        if cursor_thread and body.writeback and s.writeback_enabled:
            source = "cursor"
            history = chats.messages_for_foundry(cursor_thread, max_messages=MAX_HISTORY_MESSAGES)
            workspace = _workspace_for_request(
                body_workspace=body.workspace,
                settings=s,
                transcript_path=cursor_thread.summary.transcript_path,
            )
        elif cursor_thread or claude_thread:
            thread = cursor_thread or claude_thread
            assert thread is not None
            local = importers.fork_thread_to_local(thread, settings=s)
            chat_id = local.id
            source = "local"
            forked = True
            history = [
                {"role": m.role, "content": m.text}
                for m in local.messages
                if m.role in ("user", "assistant") and m.text.strip()
            ]
            tp = thread.summary.transcript_path if cursor_thread else None
            workspace = _workspace_for_request(
                body_workspace=body.workspace or local.workspace,
                settings=s,
                stored=local.workspace,
                transcript_path=tp,
            )
            if workspace and not local.workspace:
                local.workspace = workspace
                from .workspaces import _save as _save_local_chat

                _save_local_chat(local, s)
            cursor_thread = None
        else:
            raise HTTPException(status_code=404, detail="Chat not found")

    history = history[-MAX_HISTORY_MESSAGES:]
    model_for_llm = agent_tools.expand_mentions(model_message, workspace)

    # Attachments: expand text into the prompt; images as multimodal content (Foundry).
    att_meta: list[dict[str, Any]] = []
    text_extra: list[str] = []
    image_blocks: list[dict[str, Any]] = []
    raw_atts = body.attachments or []
    if len(raw_atts) > 6:
        raise HTTPException(status_code=400, detail="Max 6 attachments per message.")
    for raw in raw_atts:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "file")[:200]
        mime = str(raw.get("mime") or "application/octet-stream")[:120]
        text_body = raw.get("text")
        data_b64 = raw.get("data_base64")
        if isinstance(text_body, str) and text_body.strip():
            if len(text_body.encode("utf-8")) > 200_000:
                raise HTTPException(
                    status_code=400, detail=f"{name} is too large (max 200KB for text)"
                )
            clipped = text_body[:80_000]
            text_extra.append(f"\n\n--- attached: {name} ---\n{clipped}")
            att_meta.append({"name": name, "mime": mime, "kind": "text"})
        elif isinstance(data_b64, str) and data_b64 and mime.startswith("image/"):
            if s.provider == "aws":
                raise HTTPException(
                    status_code=400,
                    detail="Image attachments are not supported on AWS Bedrock yet. Use Foundry, or attach text files.",
                )
            # ~4MB decoded ≈ 5.5M base64 chars
            if len(data_b64) > 5_500_000:
                raise HTTPException(
                    status_code=400, detail=f"{name} is too large (max 4MB for images)"
                )
            media = (
                mime
                if mime in ("image/jpeg", "image/png", "image/gif", "image/webp")
                else "image/png"
            )
            image_blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media,
                        "data": data_b64,
                    },
                }
            )
            att_meta.append({"name": name, "mime": mime, "kind": "image"})
    if text_extra:
        model_for_llm = model_for_llm + "".join(text_extra)
    model_content: Any = (
        [{"type": "text", "text": model_for_llm}, *image_blocks]
        if image_blocks
        else model_for_llm
    )

    if not skip_append_user:
        history.append({"role": "user", "content": model_content})
    else:
        if history and history[-1]["role"] == "user":
            history[-1]["content"] = model_content
    merged: list[dict[str, Any]] = []
    for item in history:
        if merged and merged[-1]["role"] == item["role"]:
            merged[-1]["content"] = _merge_message_content(
                merged[-1]["content"], item["content"]
            )
        else:
            merged.append(dict(item))
    while merged and merged[0]["role"] != "user":
        merged.pop(0)

    extras = [workspaces.workspace_context_block(workspace)]
    mode = agent_loop.normalize_mode(body.mode)
    if workspace:
        if mode == "plan":
            extras.append(
                "# Plan mode\n\n"
                "You are in plan mode. Explore with read-only tools "
                "(list_dir, read_file, grep, and inspection-only run_command). "
                "Do NOT edit or create files. "
                "run_command is limited to rg/ls/git status/diff/log and similar — "
                "no interpreters or package managers. "
                "You may spawn_subagent for parallel research. "
                "Deliver a clear implementation plan: goals, steps, files to touch, "
                "risks, and open questions. Wait for the user to switch to Agent mode "
                "before making changes."
            )
        elif mode == "soft":
            extras.append(
                "# Propose mode (soft apply)\n\n"
                "You can explore and propose file edits with apply_patch, but writes "
                "are held as proposals until the user Accepts them in the UI. "
                f"Stay within {agent_tools.MAX_PATCHES_PER_TURN} apply_patch calls per turn. "
                "Prefer apply_patch for file changes. "
                "run_command is inspection-only (rg, ls, git status/diff/log, …) — "
                "no interpreters or package managers. Keep proposals minimal and coherent."
            )
        else:
            extras.append(
                "# Agent tools\n\n"
                "You can use tools to list, read, search, edit files, and run allowlisted "
                "commands inside the linked workspace only. Prefer apply_patch for file "
                "changes. "
                f"Limit yourself to {agent_tools.MAX_PATCHES_PER_TURN} apply_patch calls "
                "per turn. For commands use run_command with an argv array (no shell). "
                "Use spawn_subagent for focused parallel research when helpful. "
                "Keep edits minimal."
            )
    system = skills.build_system_preamble(
        s,
        include_cursor_settings=bool(s.cursor_link_enabled),
        active_skill=skill_name,
        extra_blocks=extras,
    )

    if mode == "soft" and source != "local":
        raise HTTPException(
            status_code=400,
            detail="Propose mode needs a local chat. Fork this conversation first, then use Propose.",
        )

    return {
        "settings": s,
        "body": body,
        "display_message": display_message,
        "skill_name": skill_name,
        "merged": merged,
        "system": system,
        "chat_id": chat_id,
        "source": source,
        "forked": forked,
        "cursor_thread": cursor_thread,
        "workspace": workspace,
        "effort": providers.normalize_effort(body.effort),
        "thinking_mode": providers.normalize_thinking_mode(body.thinking_mode),
        "mode": mode,
        "user_already_saved": user_already_saved,
        "attachments_meta": att_meta,
    }


def _finalize_chat(
    ctx: dict[str, Any],
    assistant_text: str,
    usage: dict[str, Any],
    result: dict[str, Any],
    *,
    blocks: list[dict[str, Any]] | None = None,
    file_changes: list[dict[str, Any]] | None = None,
    checkpoint_id: str | None = None,
) -> dict[str, Any]:
    s = ctx["settings"]
    body: ChatBody = ctx["body"]
    source = ctx["source"]
    chat_id = ctx["chat_id"]
    cursor_thread = ctx["cursor_thread"]
    wb: dict[str, Any] | None = None
    usage = usage if isinstance(usage, dict) else {}
    assistant_text = chats.strip_fake_tool_markers(assistant_text)

    if source == "local":
        if ctx.get("user_already_saved"):
            workspaces.append_local_assistant(
                chat_id,
                assistant_text=assistant_text,
                settings=s,
                usage=usage,
                blocks=blocks,
                file_changes=file_changes,
                checkpoint_id=checkpoint_id,
            )
        else:
            workspaces.append_local_exchange(
                chat_id,
                user_text=ctx["display_message"],
                assistant_text=assistant_text,
                settings=s,
                usage=usage,
                blocks=blocks,
                file_changes=file_changes,
                checkpoint_id=checkpoint_id,
            )
        local_after = workspaces.load_local_chat(chat_id, s)
        usage_total = (local_after.usage_total if local_after else {}) or {}
        # Materialized Cursor chats keep origin_* so Write-back still updates Agent UI.
        if (
            body.writeback
            and s.writeback_enabled
            and local_after
            and local_after.origin_chat_id
            and local_after.origin_transcript_path
        ):
            try:
                wb = writeback.write_back(
                    local_after.origin_chat_id,
                    local_after.origin_transcript_path,
                    user_text=ctx["display_message"],
                    assistant_text=assistant_text,
                    settings=s,
                )
            except FileNotFoundError as e:
                wb = {"enabled": True, "error": str(e)}
    elif body.writeback and s.writeback_enabled and cursor_thread:
        wb = writeback.write_back(
            body.chat_id,
            cursor_thread.summary.transcript_path,
            user_text=ctx["display_message"],
            assistant_text=assistant_text,
            settings=s,
        )
        usage_total = usage
    else:
        usage_total = usage

    return {
        "reply": assistant_text,
        "model": result.get("model"),
        "provider": result.get("provider") or s.provider,
        "usage": usage,
        "usage_total": usage_total,
        "writeback": wb,
        "chat_id": chat_id,
        "source": source,
        "skill": ctx["skill_name"],
        "forked": ctx["forked"],
        "file_changes": file_changes or [],
        "blocks": blocks or [],
        "checkpoint_id": checkpoint_id,
    }


def main() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host=s.app_host,
        port=s.app_port,
        reload=False,
    )


if __name__ == "__main__":
    main()

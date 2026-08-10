from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent_loop, agent_tools, chats, checkpoints, importers, providers, skills, workspaces, writeback
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

app = FastAPI(title="Portage", version="0.3.0")
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


def _settings():
    s = get_settings()
    return providers.apply_saved_config_to_settings(s)


def _overlay_settings(payload: dict[str, Any]):
    s = _settings()
    for key, value in payload.items():
        if hasattr(s, key) and value is not None:
            setattr(s, key, value)
    return s


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
        "settings_path": str(s.settings_json),
        "workspaces": workspaces.list_workspaces(s),
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


@app.post("/api/workspaces")
async def post_workspace(body: WorkspaceBody):
    try:
        item = workspaces.add_workspace(body.path, _settings())
    except (FileNotFoundError, NotADirectoryError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"workspace": item, "workspaces": workspaces.list_workspaces(_settings())}


@app.delete("/api/workspaces")
async def delete_workspace(path: str):
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail="path query param required")
    workspaces.remove_workspace(path, _settings())
    return {"workspaces": workspaces.list_workspaces(_settings())}


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
            external.append(d)
    claude = importers.claude_code_detection(s)
    if getattr(s, "claude_code_link_enabled", True) and claude["detected"]:
        for c in importers.discover_claude_code(s):
            external.append(c.to_dict())
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
            ):
                if await request.is_disconnected():
                    disconnected["v"] = True
                    aborted = True
                    break
                etype = event.get("type")
                if etype == "delta":
                    text = str(event.get("text") or "")
                    if text:
                        full_parts.append(text)
                        yield _sse({"type": "delta", "text": text})
                elif etype in ("tool_start", "tool_result", "file_change", "checkpoint"):
                    if etype == "checkpoint" and event.get("checkpoint_id"):
                        checkpoint_id = str(event.get("checkpoint_id"))
                    if etype == "file_change":
                        file_changes.append(
                            {
                                "path": event.get("path"),
                                "op": event.get("op"),
                                "diff": event.get("diff") or "",
                            }
                        )
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

    if local:
        source = "local"
        history = [
            {"role": m.role, "content": m.text}
            for m in local.messages
            if m.role in ("user", "assistant") and m.text.strip()
        ]
        try:
            workspace = workspaces.resolve_allowed_workspace(
                body.workspace or local.workspace,
                settings=s,
                allow_stored=local.workspace,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    else:
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
            try:
                workspace = workspaces.resolve_allowed_workspace(body.workspace, settings=s)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
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
            try:
                workspace = workspaces.resolve_allowed_workspace(
                    body.workspace or local.workspace,
                    settings=s,
                    allow_stored=local.workspace,
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            cursor_thread = None
        else:
            raise HTTPException(status_code=404, detail="Chat not found")

    history = history[-MAX_HISTORY_MESSAGES:]
    model_for_llm = agent_tools.expand_mentions(model_message, workspace)
    history.append({"role": "user", "content": model_for_llm})
    merged: list[dict[str, str]] = []
    for item in history:
        if merged and merged[-1]["role"] == item["role"]:
            merged[-1]["content"] += "\n\n" + item["content"]
        else:
            merged.append(dict(item))
    while merged and merged[0]["role"] != "user":
        merged.pop(0)

    extras = [workspaces.workspace_context_block(workspace)]
    if workspace:
        extras.append(
            "# Agent tools\n\n"
            "You can use tools to list, read, search, edit files, and run allowlisted commands "
            "inside the linked workspace only. Prefer apply_patch for file changes. "
            "For commands use run_command with an argv array (no shell). Keep edits minimal."
        )
    system = skills.build_system_preamble(
        s,
        include_cursor_settings=bool(s.cursor_link_enabled),
        active_skill=skill_name,
        extra_blocks=extras,
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

    if source == "local":
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

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import chats, foundry, skills, writeback
from .config import ROOT, get_settings

app = FastAPI(title="Cursor Foundry Chat", version="0.1.0")

STATIC_DIR = ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ConnectBody(BaseModel):
    foundry_messages_url: str
    foundry_api_key: str
    foundry_model: str = "claude-opus-5"
    anthropic_version: str = "2023-06-01"


class ChatBody(BaseModel):
    chat_id: str
    message: str = Field(min_length=1)
    writeback: bool = True


def _settings():
    s = get_settings()
    return foundry.apply_saved_config_to_settings(s)


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status():
    s = _settings()
    skill_list = skills.list_skills(s)
    return {
        "connected": bool(s.foundry_api_key) and "YOUR-RESOURCE" not in s.foundry_messages_url,
        "model": s.foundry_model,
        "messages_url": s.foundry_messages_url,
        "anthropic_version": s.anthropic_version,
        "writeback_enabled": s.writeback_enabled,
        "skills_count": len(skill_list),
        "priority_skills": [x.name for x in skill_list if x.priority][:40],
        "settings_path": str(s.settings_json),
        "has_key": bool(s.foundry_api_key),
    }


@app.post("/api/connect")
async def connect(body: ConnectBody):
    foundry.save_bridge_config(
        {
            "foundry_messages_url": body.foundry_messages_url.strip(),
            "foundry_api_key": body.foundry_api_key.strip(),
            "foundry_model": body.foundry_model.strip() or "claude-opus-5",
            "anthropic_version": body.anthropic_version.strip() or "2023-06-01",
        }
    )
    get_settings.cache_clear()
    s = _settings()
    try:
        result = await foundry.test_connection(s)
    except foundry.FoundryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"saved": True, "test": result}


@app.get("/api/skills")
async def list_skills():
    s = _settings()
    return {"skills": [x.to_dict() for x in skills.list_skills(s)]}


@app.get("/api/cursor-settings")
async def cursor_settings():
    return skills.cursor_settings_snapshot(_settings())


@app.get("/api/chats")
async def list_chats():
    return {"chats": [c.to_dict() for c in chats.discover_transcripts(_settings())]}


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str):
    thread = chats.load_thread(chat_id, _settings())
    if not thread:
        raise HTTPException(status_code=404, detail="Chat not found")
    return thread.to_dict()


@app.post("/api/chat")
async def send_chat(body: ChatBody):
    s = _settings()
    thread = chats.load_thread(body.chat_id, s)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat not found")

    history = chats.messages_for_foundry(thread)
    history.append({"role": "user", "content": body.message.strip()})

    system = skills.build_system_preamble(s)
    try:
        result = await foundry.chat_completion(history, system=system, settings=s)
    except foundry.FoundryError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    assistant_text = result["text"]
    wb: dict[str, Any] | None = None
    if body.writeback and s.writeback_enabled:
        wb = writeback.write_back(
            body.chat_id,
            thread.summary.transcript_path,
            user_text=body.message.strip(),
            assistant_text=assistant_text,
            settings=s,
        )

    return {
        "reply": assistant_text,
        "model": result.get("model"),
        "usage": result.get("usage"),
        "writeback": wb,
        "chat_id": body.chat_id,
    }


@app.post("/api/writeback-test")
async def writeback_test(chat_id: str, message: str = "Bridge write-back probe."):
    """Dev helper: append a synthetic turn without calling Foundry."""
    s = _settings()
    summary = chats.find_summary(chat_id, s)
    if not summary:
        raise HTTPException(status_code=404, detail="Chat not found")
    reply = (
        "This is a foundry-bridge write-back probe. "
        "If you see this in Cursor's agent transcript, sync is working."
    )
    return writeback.write_back(
        chat_id,
        summary.transcript_path,
        user_text=message,
        assistant_text=reply,
        settings=s,
    )


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

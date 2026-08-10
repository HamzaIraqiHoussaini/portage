from __future__ import annotations

import json
from typing import Any

import httpx

from .config import Settings, get_settings


class FoundryError(RuntimeError):
    pass


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-api-key": settings.foundry_api_key,
        "api-key": settings.foundry_api_key,
        "anthropic-version": settings.anthropic_version,
    }


async def test_connection(settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    if not s.foundry_api_key:
        raise FoundryError("FOUNDRY_API_KEY is empty. Save your Azure/Foundry key first.")
    if "YOUR-RESOURCE" in s.foundry_messages_url:
        raise FoundryError("FOUNDRY_MESSAGES_URL still has a placeholder resource.")

    payload = {
        "model": s.foundry_model,
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(s.foundry_messages_url, headers=_headers(s), json=payload)
    if resp.status_code >= 400:
        raise FoundryError(f"Foundry HTTP {resp.status_code}: {resp.text[:800]}")
    data = resp.json()
    text = _response_text(data)
    return {"ok": True, "model": s.foundry_model, "reply": text, "raw_type": data.get("type")}


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    system: str,
    settings: Settings | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    if not s.foundry_api_key:
        raise FoundryError("FOUNDRY_API_KEY is empty.")

    payload: dict[str, Any] = {
        "model": s.foundry_model,
        "max_tokens": max_tokens or s.foundry_max_tokens,
        "system": system,
        "messages": messages,
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(s.foundry_messages_url, headers=_headers(s), json=payload)
    if resp.status_code >= 400:
        raise FoundryError(f"Foundry HTTP {resp.status_code}: {resp.text[:1200]}")
    data = resp.json()
    text = _response_text(data)
    return {"text": text, "raw": data, "model": s.foundry_model, "usage": data.get("usage")}


def _response_text(data: dict[str, Any]) -> str:
    content = data.get("content") or []
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
    elif isinstance(content, str):
        parts.append(content)
    return "\n".join(parts).strip()


def save_bridge_config(data: dict[str, Any], settings: Settings | None = None) -> None:
    s = settings or get_settings()
    path = s.config_path
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(data)
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def load_bridge_config(settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    if not s.config_path.exists():
        return {}
    try:
        return json.loads(s.config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def apply_saved_config_to_settings(settings: Settings | None = None) -> Settings:
    """Overlay data/bridge-config.json onto settings (in-memory)."""
    s = settings or get_settings()
    cfg = load_bridge_config(s)
    if not cfg:
        return s
    if cfg.get("foundry_messages_url"):
        s.foundry_messages_url = str(cfg["foundry_messages_url"])
    if cfg.get("foundry_api_key"):
        s.foundry_api_key = str(cfg["foundry_api_key"])
    if cfg.get("foundry_model"):
        s.foundry_model = str(cfg["foundry_model"])
    if cfg.get("anthropic_version"):
        s.anthropic_version = str(cfg["anthropic_version"])
    return s

from __future__ import annotations

import json
from typing import Any

import httpx

from .config import Settings, get_settings


class ProviderError(RuntimeError):
    pass


# Back-compat alias
FoundryError = ProviderError


def save_bridge_config(data: dict[str, Any], settings: Settings | None = None) -> None:
    import os
    import tempfile

    s = settings or get_settings()
    path = s.config_path
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(existing, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".bridge-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_bridge_config(settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    if not s.config_path.exists():
        return {}
    try:
        return json.loads(s.config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def apply_saved_config_to_settings(settings: Settings | None = None) -> Settings:
    s = settings or get_settings()
    cfg = load_bridge_config(s)
    if not cfg:
        return s

    mapping = {
        "provider": "provider",
        "foundry_messages_url": "foundry_messages_url",
        "foundry_api_key": "foundry_api_key",
        "foundry_model": "foundry_model",
        "anthropic_version": "anthropic_version",
        "aws_region": "aws_region",
        "aws_access_key_id": "aws_access_key_id",
        "aws_secret_access_key": "aws_secret_access_key",
        "aws_session_token": "aws_session_token",
        "aws_model_id": "aws_model_id",
        "cursor_link_enabled": "cursor_link_enabled",
        "claude_code_link_enabled": "claude_code_link_enabled",
        "writeback_enabled": "writeback_enabled",
    }
    for key, attr in mapping.items():
        if key in cfg and cfg[key] is not None:
            setattr(s, attr, cfg[key])
    return s


def provider_connected(settings: Settings) -> bool:
    if settings.provider == "aws":
        return bool(settings.aws_access_key_id and settings.aws_secret_access_key and settings.aws_model_id)
    return bool(settings.foundry_api_key) and "YOUR-RESOURCE" not in settings.foundry_messages_url


def _foundry_headers(settings: Settings) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-api-key": settings.foundry_api_key,
        "api-key": settings.foundry_api_key,
        "anthropic-version": settings.anthropic_version,
    }


def _response_text_anthropic(data: dict[str, Any]) -> str:
    content = data.get("content") or []
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
    elif isinstance(content, str):
        parts.append(content)
    return "\n".join(parts).strip()


async def _foundry_chat(
    messages: list[dict[str, str]],
    *,
    system: str,
    settings: Settings,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    from .security import normalize_usage, sanitize_provider_error, validate_foundry_url

    if not settings.foundry_api_key:
        raise ProviderError("Foundry API key is empty.")
    if "YOUR-RESOURCE" in settings.foundry_messages_url:
        raise ProviderError("Foundry messages URL still has a placeholder.")
    try:
        url = validate_foundry_url(settings.foundry_messages_url)
    except ValueError as e:
        raise ProviderError(str(e)) from e

    payload: dict[str, Any] = {
        "model": settings.foundry_model,
        "max_tokens": max_tokens or settings.foundry_max_tokens,
        "system": system,
        "messages": messages,
    }
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=False) as client:
        resp = await client.post(
            url,
            headers=_foundry_headers(settings),
            json=payload,
        )
    if resp.status_code >= 400:
        raise ProviderError(sanitize_provider_error(resp.status_code, resp.text))
    data = resp.json()
    return {
        "text": _response_text_anthropic(data),
        "raw": data,
        "model": settings.foundry_model,
        "provider": "foundry",
        "usage": normalize_usage(
            data.get("usage") if isinstance(data.get("usage"), dict) else {},
            provider="foundry",
        ),
    }


def _aws_client(settings: Settings):
    try:
        import boto3
    except ImportError as e:
        raise ProviderError("boto3 is required for AWS Bedrock. Run: pip install boto3") from e

    kwargs: dict[str, Any] = {"region_name": settings.aws_region or "us-east-1"}
    if settings.aws_access_key_id:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
    if settings.aws_secret_access_key:
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    if settings.aws_session_token:
        kwargs["aws_session_token"] = settings.aws_session_token
    return boto3.client("bedrock-runtime", **kwargs)


def _aws_messages(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        out.append({"role": role, "content": [{"text": m.get("content") or ""}]})
    return out


def _aws_chat_sync(
    messages: list[dict[str, str]],
    *,
    system: str,
    settings: Settings,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    if not settings.aws_access_key_id or not settings.aws_secret_access_key:
        raise ProviderError("AWS access key and secret are required.")
    if not settings.aws_model_id:
        raise ProviderError("AWS model id is empty.")

    client = _aws_client(settings)
    kwargs: dict[str, Any] = {
        "modelId": settings.aws_model_id,
        "messages": _aws_messages(messages),
        "inferenceConfig": {"maxTokens": max_tokens or settings.foundry_max_tokens},
    }
    if system.strip():
        kwargs["system"] = [{"text": system}]

    try:
        data = client.converse(**kwargs)
    except Exception as e:  # noqa: BLE001 — surface Bedrock errors cleanly
        raise ProviderError(f"AWS Bedrock error: {e}") from e

    from .security import normalize_usage

    parts: list[str] = []
    for block in (data.get("output") or {}).get("message", {}).get("content") or []:
        if isinstance(block, dict) and "text" in block:
            parts.append(str(block["text"]))
    usage_raw = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {
        "text": "\n".join(parts).strip(),
        "raw": data,
        "model": settings.aws_model_id,
        "provider": "aws",
        "usage": normalize_usage(usage_raw, provider="aws"),
    }


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    system: str,
    settings: Settings | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    if s.provider == "aws":
        import asyncio

        return await asyncio.to_thread(
            _aws_chat_sync, messages, system=system, settings=s, max_tokens=max_tokens
        )
    return await _foundry_chat(messages, system=system, settings=s, max_tokens=max_tokens)


async def test_connection(settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    result = await chat_completion(
        [{"role": "user", "content": "Reply with exactly: ok"}],
        system="You are a connection probe. Reply with exactly: ok",
        settings=s,
        max_tokens=32,
    )
    return {
        "ok": True,
        "provider": result.get("provider") or s.provider,
        "model": result.get("model"),
        "reply": result.get("text"),
    }


# Keep module name foundry.py working for older imports
async def foundry_chat_completion(*args, **kwargs):
    return await chat_completion(*args, **kwargs)

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


# UI effort → API effort. "extra_high" → xhigh; "ultracode" → max + larger token room.
EFFORT_LEVELS = ("none", "low", "medium", "high", "extra_high", "max", "ultracode")
EFFORT_API = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "extra_high": "xhigh",
    "max": "max",
    "ultracode": "max",
}
THINKING_MODES = ("adaptive", "extended", "off")
EXTENDED_BUDGET = {
    "low": 4_096,
    "medium": 8_192,
    "high": 16_384,
    "extra_high": 32_000,
    "max": 64_000,
    "ultracode": 100_000,
}


def normalize_effort(raw: str | None) -> str:
    value = (raw or "high").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "xhigh": "extra_high",
        "extra": "extra_high",
        "extrahigh": "extra_high",
        "ultra": "ultracode",
        "off": "none",
        "disabled": "none",
    }
    value = aliases.get(value, value)
    return value if value in EFFORT_LEVELS else "high"


def normalize_thinking_mode(raw: str | None) -> str:
    value = (raw or "adaptive").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "none": "off",
        "disabled": "off",
        "default": "adaptive",
        "auto": "adaptive",
        "budget": "extended",
        "enabled": "extended",
        "classic": "extended",
    }
    value = aliases.get(value, value)
    return value if value in THINKING_MODES else "adaptive"


def apply_effort_to_payload(
    payload: dict[str, Any],
    *,
    effort: str | None,
    settings: Settings,
    thinking_mode: str | None = None,
    include_thinking_display: bool = True,
) -> dict[str, Any]:
    """Mutate Foundry/Anthropic Messages payload for effort + thinking."""
    level = normalize_effort(effort)
    mode = normalize_thinking_mode(thinking_mode)
    if level == "none" or mode == "off":
        payload["thinking"] = {"type": "disabled"}
        payload.pop("output_config", None)
        return payload

    base = int(payload.get("max_tokens") or settings.foundry_max_tokens or 8192)

    if mode == "extended":
        budget = EXTENDED_BUDGET.get(level, 16_384)
        # Extended thinking needs max_tokens > budget_tokens.
        payload["max_tokens"] = max(base, budget + 8_192)
        thinking: dict[str, Any] = {"type": "enabled", "budget_tokens": budget}
        if include_thinking_display:
            # Older extended-thinking APIs ignore display; newer ones may honor it.
            thinking["display"] = "summarized"
        payload["thinking"] = thinking
        payload.pop("output_config", None)
        return payload

    api_effort = EFFORT_API[level]
    # Newer models default display to "omitted" (empty thinking text) — ask for summaries.
    thinking = {"type": "adaptive"}
    if include_thinking_display:
        thinking["display"] = "summarized"
    payload["thinking"] = thinking
    payload["output_config"] = {"effort": api_effort}

    if level == "ultracode":
        payload["max_tokens"] = max(base, 65536)
    elif level in ("extra_high", "max"):
        payload["max_tokens"] = max(base, 32000)
    elif level == "high":
        payload["max_tokens"] = max(base, 16384)
    return payload


def _thinking_display_unsupported(status: int, body: str) -> bool:
    if status < 400:
        return False
    lower = (body or "").lower()
    return "display" in lower and ("thinking" in lower or "unknown" in lower or "invalid" in lower)


def _thinking_mode_unsupported(status: int, body: str) -> bool:
    if status < 400:
        return False
    lower = (body or "").lower()
    return any(
        needle in lower
        for needle in (
            "budget_tokens",
            "thinking.type",
            "adaptive",
            "extended thinking",
            "output_config",
            "effort",
        )
    ) and ("thinking" in lower or "invalid" in lower or "unknown" in lower or "not support" in lower)


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
    effort: str | None = None,
    thinking_mode: str | None = None,
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
    apply_effort_to_payload(
        payload, effort=effort, settings=settings, thinking_mode=thinking_mode
    )
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=False) as client:
        resp = await client.post(
            url,
            headers=_foundry_headers(settings),
            json=payload,
        )
        if _thinking_display_unsupported(resp.status_code, resp.text):
            apply_effort_to_payload(
                payload,
                effort=effort,
                settings=settings,
                thinking_mode=thinking_mode,
                include_thinking_display=False,
            )
            resp = await client.post(
                url,
                headers=_foundry_headers(settings),
                json=payload,
            )
        elif (
            normalize_thinking_mode(thinking_mode) == "extended"
            and _thinking_mode_unsupported(resp.status_code, resp.text)
        ):
            # Fall back to adaptive if classic extended thinking isn't available.
            apply_effort_to_payload(
                payload,
                effort=effort,
                settings=settings,
                thinking_mode="adaptive",
            )
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


async def _foundry_chat_stream(
    messages: list[dict[str, str]],
    *,
    system: str,
    settings: Settings,
    max_tokens: int | None = None,
    effort: str | None = None,
    thinking_mode: str | None = None,
):
    """Yield dict events: thinking/delta, then done {text, usage, model, provider, blocks}."""
    from .security import sanitize_provider_error, validate_foundry_url

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
        "stream": True,
    }
    mode = normalize_thinking_mode(thinking_mode)
    apply_effort_to_payload(
        payload, effort=effort, settings=settings, thinking_mode=mode
    )
    parts: list[str] = []
    thinking_parts: list[str] = []
    usage_acc: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    headers = {**_foundry_headers(settings), "Accept": "text/event-stream"}
    yield {"type": "status", "phase": "model", "detail": "streaming"}

    async with httpx.AsyncClient(timeout=300.0, follow_redirects=False) as client:
        for attempt in range(3):
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    if attempt == 0 and _thinking_display_unsupported(resp.status_code, body):
                        apply_effort_to_payload(
                            payload,
                            effort=effort,
                            settings=settings,
                            thinking_mode=mode,
                            include_thinking_display=False,
                        )
                        continue
                    if (
                        attempt < 2
                        and mode == "extended"
                        and _thinking_mode_unsupported(resp.status_code, body)
                    ):
                        mode = "adaptive"
                        apply_effort_to_payload(
                            payload,
                            effort=effort,
                            settings=settings,
                            thinking_mode="adaptive",
                        )
                        continue
                    raise ProviderError(sanitize_provider_error(resp.status_code, body))
                async for event in _iter_foundry_sse(
                    resp, parts=parts, thinking_parts=thinking_parts, usage_acc=usage_acc
                ):
                    yield event
                break

    text = "".join(parts).strip()
    if not usage_acc.get("total_tokens"):
        usage_acc["total_tokens"] = usage_acc.get("input_tokens", 0) + usage_acc.get(
            "output_tokens", 0
        )
    blocks: list[dict[str, Any]] = []
    think_full = "".join(thinking_parts).strip()
    if think_full:
        blocks.append({"type": "thinking", "text": think_full})
    if text:
        blocks.append({"type": "text", "text": text})
    yield {
        "type": "done",
        "text": text,
        "usage": usage_acc,
        "model": settings.foundry_model,
        "provider": "foundry",
        "blocks": blocks,
    }


async def _iter_foundry_sse(resp, *, parts: list[str], thinking_parts: list[str], usage_acc: dict[str, int]):
    from .security import normalize_usage

    event_name = ""
    async for line in resp.aiter_lines():
        if not line:
            event_name = ""
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            event_name = ""
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            event_name = ""
            continue
        dtype = data.get("type") or event_name
        event_name = ""
        if dtype == "content_block_delta":
            delta = data.get("delta") or {}
            if not isinstance(delta, dict):
                continue
            d_type = str(delta.get("type") or "")
            if d_type == "thinking_delta" or (not d_type and delta.get("thinking")):
                think = delta.get("thinking")
                if think:
                    thinking_parts.append(str(think))
                    yield {"type": "status", "phase": "thinking"}
                    yield {"type": "thinking", "text": str(think)}
            else:
                text = delta.get("text")
                if text:
                    parts.append(str(text))
                    yield {"type": "status", "phase": "writing"}
                    yield {"type": "delta", "text": str(text)}
        elif dtype == "message_start":
            msg = data.get("message") if isinstance(data.get("message"), dict) else {}
            u = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
            if u:
                usage_acc.clear()
                usage_acc.update(normalize_usage(u, provider="foundry"))
        elif dtype == "message_delta":
            u = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            if u:
                partial = normalize_usage(u, provider="foundry")
                for key in ("input_tokens", "output_tokens", "total_tokens"):
                    if partial.get(key):
                        usage_acc[key] = partial[key]
                if not usage_acc.get("total_tokens"):
                    usage_acc["total_tokens"] = usage_acc.get("input_tokens", 0) + usage_acc.get(
                        "output_tokens", 0
                    )


def _aws_chat_stream_sync(
    messages: list[dict[str, str]],
    *,
    system: str,
    settings: Settings,
    max_tokens: int | None = None,
    cancel_event=None,
):
    from .security import normalize_usage

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
        response = client.converse_stream(**kwargs)
    except Exception as e:  # noqa: BLE001
        raise ProviderError(f"AWS Bedrock error: {e}") from e

    parts: list[str] = []
    usage_acc: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    stream = response.get("stream")
    if stream is None:
        raise ProviderError("AWS Bedrock returned no stream.")
    try:
        for event in stream:
            if cancel_event is not None and cancel_event.is_set():
                break
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta") or {}
                text = delta.get("text")
                if text:
                    parts.append(str(text))
                    yield {"type": "delta", "text": str(text)}
            elif "metadata" in event:
                u = event["metadata"].get("usage") if isinstance(event["metadata"], dict) else None
                if isinstance(u, dict):
                    usage_acc = normalize_usage(u, provider="aws")
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass

    if cancel_event is not None and cancel_event.is_set():
        return

    text = "".join(parts).strip()
    yield {
        "type": "done",
        "text": text,
        "usage": usage_acc,
        "model": settings.aws_model_id,
        "provider": "aws",
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
    effort: str | None = None,
    thinking_mode: str | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    if s.provider == "aws":
        import asyncio

        return await asyncio.to_thread(
            _aws_chat_sync, messages, system=system, settings=s, max_tokens=max_tokens
        )
    return await _foundry_chat(
        messages,
        system=system,
        settings=s,
        max_tokens=max_tokens,
        effort=effort,
        thinking_mode=thinking_mode,
    )


async def chat_completion_with_tools(
    messages: list[dict[str, Any]],
    *,
    system: str,
    settings: Settings | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    effort: str | None = None,
    thinking_mode: str | None = None,
    mode: str | None = None,
    allow_subagents: bool = True,
) -> dict[str, Any]:
    """One model step that may return tool_use content blocks (Anthropic-shaped)."""
    s = settings or get_settings()
    tool_defs = tools or []
    if s.provider == "aws":
        import asyncio

        return await asyncio.to_thread(
            _aws_chat_with_tools_sync,
            messages,
            system=system,
            settings=s,
            tools=tool_defs,
            max_tokens=max_tokens,
            mode=mode or "agent",
            allow_subagents=allow_subagents,
        )
    return await _foundry_chat_with_tools(
        messages,
        system=system,
        settings=s,
        tools=tool_defs,
        max_tokens=max_tokens,
        effort=effort,
        thinking_mode=thinking_mode,
    )


async def _foundry_chat_with_tools(
    messages: list[dict[str, Any]],
    *,
    system: str,
    settings: Settings,
    tools: list[dict[str, Any]],
    max_tokens: int | None = None,
    effort: str | None = None,
    thinking_mode: str | None = None,
) -> dict[str, Any]:
    from .security import normalize_usage, sanitize_provider_error, validate_foundry_url

    if not settings.foundry_api_key:
        raise ProviderError("Foundry API key is empty.")
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
    if tools:
        payload["tools"] = tools
    mode = normalize_thinking_mode(thinking_mode)
    apply_effort_to_payload(
        payload, effort=effort, settings=settings, thinking_mode=mode
    )
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=False) as client:
        resp = await client.post(url, headers=_foundry_headers(settings), json=payload)
        if _thinking_display_unsupported(resp.status_code, resp.text):
            apply_effort_to_payload(
                payload,
                effort=effort,
                settings=settings,
                thinking_mode=mode,
                include_thinking_display=False,
            )
            resp = await client.post(url, headers=_foundry_headers(settings), json=payload)
        elif mode == "extended" and _thinking_mode_unsupported(resp.status_code, resp.text):
            apply_effort_to_payload(
                payload,
                effort=effort,
                settings=settings,
                thinking_mode="adaptive",
            )
            resp = await client.post(url, headers=_foundry_headers(settings), json=payload)
    if resp.status_code >= 400:
        raise ProviderError(sanitize_provider_error(resp.status_code, resp.text))
    data = resp.json()
    content = data.get("content") if isinstance(data.get("content"), list) else []
    return {
        "text": _response_text_anthropic(data),
        "content": content,
        "stop_reason": data.get("stop_reason"),
        "raw": data,
        "model": settings.foundry_model,
        "provider": "foundry",
        "usage": normalize_usage(
            data.get("usage") if isinstance(data.get("usage"), dict) else {},
            provider="foundry",
        ),
    }


def _aws_normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic-ish messages (string or block list) to Bedrock converse shape."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        blocks: list[dict[str, Any]] = []
        if isinstance(content, str):
            blocks.append({"text": content})
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "text" or "text" in b and btype is None:
                    blocks.append({"text": str(b.get("text") or "")})
                elif btype == "tool_use":
                    blocks.append(
                        {
                            "toolUse": {
                                "toolUseId": b.get("id") or b.get("toolUseId") or "tool",
                                "name": b.get("name") or "tool",
                                "input": b.get("input") or {},
                            }
                        }
                    )
                elif btype == "tool_result":
                    blocks.append(
                        {
                            "toolResult": {
                                "toolUseId": b.get("tool_use_id") or b.get("toolUseId") or "tool",
                                "content": [{"text": str(b.get("content") or "")}],
                                "status": "error" if b.get("is_error") else "success",
                            }
                        }
                    )
                elif "toolUse" in b or "toolResult" in b or "text" in b:
                    blocks.append(b)
        if blocks:
            out.append({"role": role, "content": blocks})
    return out


def _aws_chat_with_tools_sync(
    messages: list[dict[str, Any]],
    *,
    system: str,
    settings: Settings,
    tools: list[dict[str, Any]],
    max_tokens: int | None = None,
    mode: str = "agent",
    allow_subagents: bool = True,
) -> dict[str, Any]:
    from .security import normalize_usage
    from . import agent_tools

    if not settings.aws_access_key_id or not settings.aws_secret_access_key:
        raise ProviderError("AWS access key and secret are required.")
    if not settings.aws_model_id:
        raise ProviderError("AWS model id is empty.")

    client = _aws_client(settings)
    kwargs: dict[str, Any] = {
        "modelId": settings.aws_model_id,
        "messages": _aws_normalize_messages(messages),
        "inferenceConfig": {"maxTokens": max_tokens or settings.foundry_max_tokens},
    }
    if system.strip():
        kwargs["system"] = [{"text": system}]
    if tools:
        kwargs["toolConfig"] = agent_tools.bedrock_tool_config(
            mode=mode, allow_subagents=allow_subagents
        )

    try:
        data = client.converse(**kwargs)
    except Exception as e:  # noqa: BLE001
        raise ProviderError(f"AWS Bedrock error: {e}") from e

    msg = (data.get("output") or {}).get("message") or {}
    raw_blocks = msg.get("content") or []
    content: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for block in raw_blocks:
        if not isinstance(block, dict):
            continue
        if "text" in block:
            t = str(block.get("text") or "")
            text_parts.append(t)
            content.append({"type": "text", "text": t})
        elif "toolUse" in block:
            tu = block["toolUse"] or {}
            content.append(
                {
                    "type": "tool_use",
                    "id": tu.get("toolUseId"),
                    "name": tu.get("name"),
                    "input": tu.get("input") or {},
                }
            )
    stop = data.get("stopReason")
    has_tools = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
    if has_tools or stop in ("tool_use", "toolUse"):
        mapped_stop = "tool_use"
    else:
        mapped_stop = str(stop or "end_turn")
    usage_raw = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {
        "text": "\n".join(text_parts).strip(),
        "content": content,
        "stop_reason": mapped_stop,
        "raw": data,
        "model": settings.aws_model_id,
        "provider": "aws",
        "usage": normalize_usage(usage_raw, provider="aws"),
    }


async def stream_chat_completion(
    messages: list[dict[str, str]],
    *,
    system: str,
    settings: Settings | None = None,
    max_tokens: int | None = None,
    effort: str | None = None,
    thinking_mode: str | None = None,
):
    """Async generator of stream events (delta / done)."""
    s = settings or get_settings()
    if s.provider == "aws":
        import asyncio
        import threading

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        sentinel = object()
        cancel_event = threading.Event()

        def _run() -> None:
            try:
                for event in _aws_chat_stream_sync(
                    messages,
                    system=system,
                    settings=s,
                    max_tokens=max_tokens,
                    cancel_event=cancel_event,
                ):
                    if cancel_event.is_set():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as exc:  # noqa: BLE001
                if not cancel_event.is_set():
                    loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, sentinel)

        threading.Thread(target=_run, daemon=True).start()
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            cancel_event.set()
        return

    async for event in _foundry_chat_stream(
        messages,
        system=system,
        settings=s,
        max_tokens=max_tokens,
        effort=effort,
        thinking_mode=thinking_mode,
    ):
        yield event


async def test_connection(settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    result = await chat_completion(
        [{"role": "user", "content": "Reply with exactly: ok"}],
        system="You are a connection probe. Reply with exactly: ok",
        settings=s,
        max_tokens=32,
        effort="none",
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

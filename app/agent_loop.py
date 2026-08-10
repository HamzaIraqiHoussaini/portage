"""Multi-step agent turn: model ↔ tools, yielding UI stream events."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from . import agent_tools, checkpoints, providers
from .config import Settings
from .providers import ProviderError

MAX_TOOL_ROUNDS = 12


async def run_agent_stream(
    messages: list[dict[str, Any]],
    *,
    system: str,
    settings: Settings,
    workspace: str | None = None,
    chat_id: str | None = None,
    cancel_check=None,
    effort: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """
    Yield events:
      delta, tool_start, tool_result, file_change, done, error
    When workspace is set, tools are enabled; otherwise text-only stream.
    """
    if not workspace:
        async for event in providers.stream_chat_completion(
            _as_text_messages(messages),
            system=system,
            settings=settings,
            effort=effort,
        ):
            if cancel_check and cancel_check():
                return
            yield event
        return

    working: list[dict[str, Any]] = [dict(m) for m in messages]
    usage_acc = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    text_parts: list[str] = []
    file_changes: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    checkpoint_id: str | None = None
    model_name = settings.foundry_model if settings.provider != "aws" else settings.aws_model_id
    cancelled = False

    for _round in range(MAX_TOOL_ROUNDS):
        if cancel_check and cancel_check():
            cancelled = True
            break
        try:
            result = await providers.chat_completion_with_tools(
                working,
                system=system,
                settings=settings,
                tools=agent_tools.anthropic_tools_payload(),
                effort=effort,
            )
        except ProviderError as e:
            yield {"type": "error", "detail": str(e)}
            return

        if cancel_check and cancel_check():
            cancelled = True
            break

        usage = result.get("usage") or {}
        for k in ("input_tokens", "output_tokens", "total_tokens"):
            usage_acc[k] = usage_acc.get(k, 0) + int(usage.get(k) or 0)
        model_name = result.get("model") or model_name

        content_blocks = result.get("content") or []
        tool_uses: list[dict[str, Any]] = []

        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                t = str(block.get("text") or "")
                if t:
                    text_parts.append(t)
                    blocks.append({"type": "text", "text": t})
                    yield {"type": "delta", "text": t}
            elif btype in ("thinking", "reasoning"):
                think = str(block.get("thinking") or block.get("text") or "")
                if think.strip():
                    blocks.append({"type": "thinking", "text": think})
            elif btype == "tool_use":
                tool_uses.append(block)
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": block.get("input") or {},
                    }
                )

        stop = result.get("stop_reason") or "end_turn"
        if not tool_uses or stop == "end_turn":
            break

        working.append({"role": "assistant", "content": content_blocks})

        tool_results: list[dict[str, Any]] = []
        for tu in tool_uses:
            if cancel_check and cancel_check():
                cancelled = True
                break
            name = str(tu.get("name") or "tool")
            tool_id = str(tu.get("id") or name)
            raw_input = tu.get("input") if isinstance(tu.get("input"), dict) else {}
            safe_input: dict[str, Any] = {}
            for key in ("path", "pattern"):
                if key in raw_input:
                    safe_input[key] = raw_input.get(key)
            if name == "run_command" and isinstance(raw_input.get("command"), list):
                safe_input["command"] = [str(x) for x in raw_input["command"][:20]]
            yield {
                "type": "tool_start",
                "id": tool_id,
                "name": name,
                "input": safe_input,
            }

            if name == "apply_patch" and chat_id and raw_input.get("path"):
                checkpoint_id = checkpoints.snapshot_before_write(
                    chat_id=chat_id,
                    workspace=workspace,
                    rel_path=str(raw_input.get("path")),
                    settings=settings,
                    checkpoint_id=checkpoint_id,
                )
                yield {"type": "checkpoint", "checkpoint_id": checkpoint_id}

            try:
                out = agent_tools.execute_tool(name, raw_input, workspace=workspace)
                content = str(out.get("content") or "")
                is_err = False
                fc = out.get("file_change")
                if isinstance(fc, dict):
                    file_changes.append(fc)
                    blocks.append({"type": "file_change", **fc})
                    yield {"type": "file_change", **fc, "checkpoint_id": checkpoint_id}
            except agent_tools.ToolError as e:
                content = str(e)
                is_err = True

            # Don't push full apply_patch bodies on the wire.
            yield {
                "type": "tool_result",
                "id": tool_id,
                "name": name,
                "content": content[:8000],
                "is_error": is_err,
            }
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "name": name,
                    "content": content[:8000],
                    "is_error": is_err,
                }
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": content[:8000],
                    "is_error": is_err,
                }
            )

        if cancelled:
            break
        # Incomplete tool batch after cancel shouldn't continue the model loop.
        if len(tool_results) != len(tool_uses):
            cancelled = True
            break
        working.append({"role": "user", "content": tool_results})

    if cancelled:
        # Let /api/chat/stream persist partial via disconnect/abort path — no fake done.
        return

    final_text = "".join(text_parts).strip()
    if not final_text and file_changes:
        final_text = f"Updated {len(file_changes)} file(s)."
    yield {
        "type": "done",
        "text": final_text,
        "usage": usage_acc,
        "model": model_name,
        "provider": settings.provider,
        "file_changes": file_changes,
        "blocks": blocks,
        "checkpoint_id": checkpoint_id,
    }


def _as_text_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(str(b.get("text") or ""))
                elif isinstance(b, str):
                    parts.append(b)
            text = "\n".join(parts)
        else:
            text = str(content or "")
        if text.strip():
            out.append({"role": role, "content": text})
    return out

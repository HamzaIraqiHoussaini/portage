"""Multi-step agent turn: model ↔ tools, yielding UI stream events."""

from __future__ import annotations

from typing import Any, AsyncIterator

from . import agent_tools, checkpoints, providers
from .config import Settings
from .providers import ProviderError

MAX_TOOL_ROUNDS = 12
MAX_SUBAGENT_ROUNDS = 6
MAX_SUBAGENT_DEPTH = 1


def normalize_mode(raw: str | None) -> str:
    value = (raw or "agent").strip().lower()
    return "plan" if value == "plan" else "agent"


async def run_agent_stream(
    messages: list[dict[str, Any]],
    *,
    system: str,
    settings: Settings,
    workspace: str | None = None,
    chat_id: str | None = None,
    cancel_check=None,
    effort: str | None = None,
    thinking_mode: str | None = None,
    mode: str | None = None,
    depth: int = 0,
) -> AsyncIterator[dict[str, Any]]:
    """
    Yield events:
      status, delta, thinking, tool_start, tool_result, file_change,
      subagent_start, subagent_delta, subagent_done, done, error
    When workspace is set, tools are enabled; otherwise text-only stream.
    """
    agent_mode = normalize_mode(mode)
    think_mode = providers.normalize_thinking_mode(thinking_mode)
    if not workspace:
        yield {"type": "status", "phase": "model", "detail": "streaming"}
        async for event in providers.stream_chat_completion(
            _as_text_messages(messages),
            system=system,
            settings=settings,
            effort=effort,
            thinking_mode=think_mode,
        ):
            if cancel_check and cancel_check():
                return
            yield event
        return

    allow_subagents = depth < MAX_SUBAGENT_DEPTH
    tools = agent_tools.anthropic_tools_payload(
        mode=agent_mode,
        allow_subagents=allow_subagents,
    )
    working: list[dict[str, Any]] = [dict(m) for m in messages]
    usage_acc = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    text_parts: list[str] = []
    file_changes: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    checkpoint_id: str | None = None
    model_name = settings.foundry_model if settings.provider != "aws" else settings.aws_model_id
    cancelled = False
    max_rounds = MAX_SUBAGENT_ROUNDS if depth > 0 else MAX_TOOL_ROUNDS

    for _round in range(max_rounds):
        if cancel_check and cancel_check():
            cancelled = True
            break
        yield {
            "type": "status",
            "phase": "model",
            "detail": "waiting" if _round == 0 else f"round {_round + 1}",
        }
        try:
            result = await providers.chat_completion_with_tools(
                working,
                system=system,
                settings=settings,
                tools=tools,
                effort=effort,
                thinking_mode=thinking_mode,
                mode=agent_mode,
                allow_subagents=allow_subagents,
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
                    yield {"type": "status", "phase": "writing"}
                    yield {"type": "delta", "text": t}
            elif btype in ("thinking", "reasoning"):
                think = str(block.get("thinking") or block.get("text") or "")
                if think.strip():
                    blocks.append({"type": "thinking", "text": think})
                    yield {"type": "status", "phase": "thinking"}
                    yield {"type": "thinking", "text": think}
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

        stop = str(result.get("stop_reason") or "")
        # Always run tools when the model requested them. Some gateways mis-label
        # stop_reason as end_turn even when tool_use blocks are present.
        if tool_uses:
            yield {"type": "status", "phase": "tool", "detail": f"{len(tool_uses)} tool(s)"}
        else:
            if stop in ("max_tokens", "max_tokens_reached"):
                note = (
                    "\n\n_(Stopped early: hit max tokens while thinking/responding. "
                    "Raise Effort or max tokens and continue.)_"
                )
                text_parts.append(note)
                blocks.append({"type": "text", "text": note})
                yield {"type": "delta", "text": note}
                yield {"type": "status", "phase": "truncated", "detail": stop}
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
            for key in ("path", "pattern", "label"):
                if key in raw_input:
                    safe_input[key] = raw_input.get(key)
            if name == "run_command" and isinstance(raw_input.get("command"), list):
                safe_input["command"] = [str(x) for x in raw_input["command"][:20]]
            if name == "spawn_subagent":
                prompt_preview = str(raw_input.get("prompt") or "")[:120]
                if prompt_preview:
                    safe_input["prompt"] = prompt_preview
            yield {
                "type": "tool_start",
                "id": tool_id,
                "name": name,
                "input": safe_input,
            }

            content = ""
            is_err = False

            if name == "spawn_subagent":
                async for ev in _run_spawned_subagent_events(
                    raw_input,
                    tool_id=tool_id,
                    system=system,
                    settings=settings,
                    workspace=workspace,
                    cancel_check=cancel_check,
                    effort=effort,
                    thinking_mode=think_mode,
                    mode=agent_mode,
                    depth=depth,
                    usage_acc=usage_acc,
                    blocks=blocks,
                ):
                    if ev.get("_result"):
                        content = str(ev.get("content") or "")
                        is_err = bool(ev.get("is_error"))
                    else:
                        yield ev
                    if cancel_check and cancel_check():
                        cancelled = True
                        break
                if cancelled:
                    break
            else:
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
                    out = agent_tools.execute_tool(
                        name, raw_input, workspace=workspace, mode=agent_mode
                    )
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
        "mode": agent_mode,
    }


async def _run_spawned_subagent_events(
    raw_input: dict[str, Any],
    *,
    tool_id: str,
    system: str,
    settings: Settings,
    workspace: str,
    cancel_check,
    effort: str | None,
    thinking_mode: str | None,
    mode: str,
    depth: int,
    usage_acc: dict[str, int],
    blocks: list[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    prompt = str(raw_input.get("prompt") or "").strip()
    label = str(raw_input.get("label") or "subagent").strip()[:80] or "subagent"
    if not prompt:
        yield {"_result": True, "content": "spawn_subagent requires a prompt", "is_error": True}
        return
    if depth >= MAX_SUBAGENT_DEPTH:
        yield {
            "_result": True,
            "content": "Nested subagents are not allowed (max depth reached).",
            "is_error": True,
        }
        return

    yield {
        "type": "subagent_start",
        "id": tool_id,
        "label": label,
        "prompt": prompt[:200],
    }
    blocks.append({"type": "subagent_start", "id": tool_id, "label": label, "prompt": prompt[:200]})

    sub_system = (
        f"{system}\n\n# Subagent\n\n"
        f"You are a focused subagent labeled `{label}`. "
        "Complete only the assigned research task. Be concise. "
        "Do not spawn further subagents. Return a clear summary of findings."
    )
    sub_messages = [{"role": "user", "content": prompt}]
    summary_parts: list[str] = []
    thinking_parts: list[str] = []
    err_detail: str | None = None

    async for event in run_agent_stream(
        sub_messages,
        system=sub_system,
        settings=settings,
        workspace=workspace,
        chat_id=None,
        cancel_check=cancel_check,
        effort=effort,
        thinking_mode=thinking_mode,
        mode=mode,
        depth=depth + 1,
    ):
        etype = event.get("type")
        if etype == "delta":
            t = str(event.get("text") or "")
            if t:
                summary_parts.append(t)
                yield {"type": "subagent_delta", "id": tool_id, "text": t}
        elif etype == "thinking":
            t = str(event.get("text") or "")
            if t:
                thinking_parts.append(t)
                yield {"type": "thinking", "text": t, "subagent_id": tool_id}
        elif etype in ("tool_start", "tool_result"):
            yield {**event, "subagent_id": tool_id}
        elif etype == "error":
            err_detail = str(event.get("detail") or "Subagent failed")
            break
        elif etype == "done":
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            for k in ("input_tokens", "output_tokens", "total_tokens"):
                usage_acc[k] = usage_acc.get(k, 0) + int(usage.get(k) or 0)
            done_text = str(event.get("text") or "").strip()
            if done_text:
                summary_parts = [done_text]
            break

    if err_detail:
        yield {
            "type": "subagent_done",
            "id": tool_id,
            "label": label,
            "is_error": True,
            "summary": err_detail,
        }
        blocks.append(
            {
                "type": "subagent_done",
                "id": tool_id,
                "label": label,
                "is_error": True,
                "summary": err_detail,
            }
        )
        yield {"_result": True, "content": err_detail, "is_error": True}
        return

    summary = "".join(summary_parts).strip() or "(Subagent returned no text.)"
    if thinking_parts:
        blocks.append(
            {"type": "thinking", "text": "".join(thinking_parts), "subagent_id": tool_id}
        )
    yield {
        "type": "subagent_done",
        "id": tool_id,
        "label": label,
        "is_error": False,
        "summary": summary[:8000],
    }
    blocks.append(
        {
            "type": "subagent_done",
            "id": tool_id,
            "label": label,
            "is_error": False,
            "summary": summary[:8000],
        }
    )
    yield {"_result": True, "content": summary[:8000], "is_error": False}


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

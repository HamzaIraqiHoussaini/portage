"""Import conversations from Cursor (existing), Claude Code, ChatGPT, and Antigravity exports."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .chats import ChatMessage, ChatSummary, ChatThread, _extract_text, _plain_preview, _mtime_ms
from .config import Settings, get_settings
from .security import (
    MAX_DISCOVERY_FILES,
    MAX_IMPORT_CHATS,
    MAX_IMPORT_MESSAGES_PER_CHAT,
    MAX_MESSAGE_CHARS,
    assert_json_depth,
)
from .workspaces import LocalChat, LocalMessage, _save, create_local_chat, load_local_chat

SAFE_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _human_project(slug: str) -> str:
    name = slug
    for prefix in ("Users-hamza-", "Users-", "home-", "-Users-", "-home-"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    return name.replace("-", " ").strip() or slug


def _claude_projects_root(settings: Settings) -> Path:
    return Path(settings.claude_home).expanduser() / "projects"


def claude_code_detection(settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    root = _claude_projects_root(s)
    count = 0
    if root.is_dir():
        for _ in root.rglob("*.jsonl"):
            count += 1
            if count >= MAX_DISCOVERY_FILES:
                break
    return {
        "detected": root.is_dir() and count > 0,
        "projects_dir": str(root),
        "transcript_hint_count": count,
    }


def antigravity_detection() -> dict[str, Any]:
    roots = [
        Path.home() / ".gemini" / "antigravity" / "conversations",
        Path.home() / ".gemini" / "antigravity-cli" / "conversations",
    ]
    pb = 0
    for root in roots:
        if root.is_dir():
            pb += sum(1 for _ in root.glob("*.pb"))
    return {
        "detected": pb > 0,
        "encrypted": True,
        "transcript_hint_count": pb,
        "note": "Antigravity stores encrypted .pb files — import a JSON/Markdown export from aghistory / agy-reader.",
    }


def _read_claude_jsonl(path: Path, *, limit_tail: int | None = None) -> tuple[str, list[ChatMessage]]:
    title = path.stem
    messages: list[ChatMessage] = []
    max_bytes = 8 * 1024 * 1024
    read = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            read += len(line.encode("utf-8", errors="replace"))
            if read > max_bytes:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            typ = obj.get("type")
            if typ == "ai-title" and obj.get("aiTitle"):
                title = str(obj["aiTitle"]).strip() or title
                continue
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else None
            role = None
            content = None
            if typ in ("user", "assistant") and msg:
                role = msg.get("role") or typ
                content = msg.get("content")
            elif msg and msg.get("role") in ("user", "assistant"):
                role = msg["role"]
                content = msg.get("content")
            if role not in ("user", "assistant"):
                continue
            text = _extract_text(content)
            if isinstance(content, list):
                texts = [
                    str(b.get("text") or "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                text = "\n".join(t for t in texts if t).strip() or text
            if not text.strip():
                continue
            if role == "user" and text.lstrip().startswith("<local-command-caveat>"):
                continue
            if role == "user" and "<command-name>" in text and len(text) < 500:
                continue
            if len(text) > MAX_MESSAGE_CHARS:
                text = text[:MAX_MESSAGE_CHARS]
            messages.append(ChatMessage(role=role, text=text.strip(), raw=None))
            if len(messages) >= MAX_IMPORT_MESSAGES_PER_CHAT:
                break
    if limit_tail is not None and len(messages) > limit_tail:
        messages = messages[-limit_tail:]
    return title, messages


def discover_claude_code(settings: Settings | None = None, *, limit: int = 200) -> list[ChatSummary]:
    s = settings or get_settings()
    root = _claude_projects_root(s)
    out: list[ChatSummary] = []
    if not root.is_dir():
        return out
    files: list[Path] = []
    for path in root.rglob("*.jsonl"):
        if path.name.startswith("."):
            continue
        files.append(path)
        if len(files) >= MAX_DISCOVERY_FILES:
            break
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for path in files[: min(limit, MAX_DISCOVERY_FILES)]:
        try:
            title, msgs = _read_claude_jsonl(path, limit_tail=8)
        except OSError:
            continue
        if not msgs and title == path.stem:
            continue
        project = _human_project(path.parent.name)
        preview = ""
        for m in reversed(msgs):
            cleaned = _plain_preview(m.text)
            if cleaned:
                preview = cleaned
                break
        cid = path.stem
        if not SAFE_ID_RE.fullmatch(cid):
            continue
        out.append(
            ChatSummary(
                id=cid,
                title=title or cid,
                updated_at=_mtime_ms(path),
                project=project,
                transcript_path=str(path),
                source="claude-code",
                preview=preview,
            )
        )
    return out


def load_claude_thread(
    chat_id: str,
    settings: Settings | None = None,
    *,
    transcript_path: str | None = None,
) -> ChatThread | None:
    wanted = None
    if transcript_path:
        try:
            wanted = str(Path(transcript_path).expanduser().resolve())
        except OSError:
            wanted = transcript_path
    for summary in discover_claude_code(settings, limit=MAX_DISCOVERY_FILES):
        if summary.id != chat_id:
            continue
        if wanted:
            try:
                if str(Path(summary.transcript_path).resolve()) != wanted:
                    continue
            except OSError:
                if summary.transcript_path != transcript_path:
                    continue
        title, messages = _read_claude_jsonl(Path(summary.transcript_path))
        summary.title = title or summary.title
        return ChatThread(summary=summary, messages=messages)
    return None


def _chatgpt_parts(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            return "\n".join(str(p) for p in parts if isinstance(p, str) and p.strip())
        return str(content.get("text") or "")
    if isinstance(content, list):
        return "\n".join(_chatgpt_parts(p) for p in content)
    return str(content)


def _clamp_imported_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in messages[:MAX_IMPORT_MESSAGES_PER_CHAT]:
        role = m.get("role")
        text = str(m.get("text") or "")
        if role not in ("user", "assistant") or not text.strip():
            continue
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[:MAX_MESSAGE_CHARS]
        out.append({"role": role, "text": text})
    return out


def parse_chatgpt_export(data: Any) -> list[dict[str, Any]]:
    """Parse ChatGPT data export conversations.json (list) or a single conversation."""
    assert_json_depth(data)
    conversations: list[Any]
    if isinstance(data, list):
        conversations = data[:MAX_IMPORT_CHATS]
    elif isinstance(data, dict) and "mapping" in data:
        conversations = [data]
    elif isinstance(data, dict) and isinstance(data.get("conversations"), list):
        conversations = data["conversations"][:MAX_IMPORT_CHATS]
    else:
        raise ValueError("Unrecognized ChatGPT export shape — expect conversations.json")

    out: list[dict[str, Any]] = []
    for convo in conversations:
        if not isinstance(convo, dict):
            continue
        title = str(convo.get("title") or "ChatGPT chat").strip() or "ChatGPT chat"
        mapping = convo.get("mapping") or {}
        if not isinstance(mapping, dict):
            continue
        nodes: list[tuple[float, str, str]] = []
        for node in mapping.values():
            if not isinstance(node, dict):
                continue
            msg = node.get("message")
            if not isinstance(msg, dict):
                continue
            author = msg.get("author") or {}
            role = (author.get("role") if isinstance(author, dict) else None) or msg.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _chatgpt_parts(msg.get("content")).strip()
            if not text:
                continue
            create_time = msg.get("create_time") or node.get("create_time") or 0
            try:
                ts = float(create_time)
            except (TypeError, ValueError):
                ts = 0.0
            nodes.append((ts, role, text))
        nodes.sort(key=lambda x: x[0])
        messages = _clamp_imported_messages([{"role": r, "text": t} for _, r, t in nodes])
        if not messages:
            continue
        out.append({"title": title[:200], "messages": messages, "source": "chatgpt"})
        if len(out) >= MAX_IMPORT_CHATS:
            break
    return out


def parse_antigravity_export(data: Any) -> list[dict[str, Any]]:
    """Best-effort parse of community Antigravity JSON exports."""
    assert_json_depth(data)
    items: list[Any]
    if isinstance(data, list):
        items = data[:MAX_IMPORT_CHATS]
    else:
        items = [data]

    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = (
            str(
                (item.get("session") or {}).get("title")
                if isinstance(item.get("session"), dict)
                else item.get("title")
                or "Antigravity chat"
            ).strip()
            or "Antigravity chat"
        )
        messages: list[dict[str, str]] = []

        steps = item.get("steps")
        if isinstance(steps, list):
            for step in steps[:MAX_IMPORT_MESSAGES_PER_CHAT]:
                if not isinstance(step, dict):
                    continue
                role = (step.get("role") or step.get("type") or "").lower()
                if role in ("human", "user", "user_message"):
                    role = "user"
                elif role in ("assistant", "model", "ai", "agent"):
                    role = "assistant"
                else:
                    continue
                text = (
                    step.get("text")
                    or step.get("content")
                    or step.get("message")
                    or step.get("markdown")
                    or ""
                )
                if isinstance(text, list):
                    text = _extract_text(text)
                text = str(text).strip()
                if text:
                    messages.append({"role": role, "text": text})

        if not messages and isinstance(item.get("messages"), list):
            for m in item["messages"][:MAX_IMPORT_MESSAGES_PER_CHAT]:
                if not isinstance(m, dict):
                    continue
                role = (m.get("role") or "").lower()
                if role in ("human", "user"):
                    role = "user"
                elif role in ("assistant", "model", "ai"):
                    role = "assistant"
                else:
                    continue
                text = str(m.get("text") or m.get("content") or "").strip()
                if text:
                    messages.append({"role": role, "text": text})

        if not messages and isinstance(item.get("transcript"), str) and item["transcript"].strip():
            messages = [{"role": "assistant", "text": item["transcript"].strip()[:MAX_MESSAGE_CHARS]}]

        messages = _clamp_imported_messages(messages)
        if not messages:
            continue
        out.append({"title": title[:200], "messages": messages, "source": "antigravity"})
        if len(out) >= MAX_IMPORT_CHATS:
            break
    if not out:
        raise ValueError(
            "Unrecognized Antigravity export — use JSON from aghistory / agy-reader / trajectory extractors"
        )
    return out


def detect_and_parse_import(raw: bytes, filename: str = "") -> list[dict[str, Any]]:
    name = (filename or "").lower()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError("Import file must be UTF-8 text/JSON") from e
    text_stripped = text.strip()
    if not text_stripped:
        raise ValueError("Import file is empty")

    if not text_stripped.startswith("{") and not text_stripped.startswith("["):
        if name.endswith(".md") or name.endswith(".txt"):
            title = Path(filename).stem if filename else "Imported transcript"
            return [
                {
                    "title": title[:200],
                    "messages": [
                        {
                            "role": "user",
                            "text": text_stripped[:MAX_MESSAGE_CHARS],
                        }
                    ],
                    "source": "antigravity" if "anti" in name else "import",
                }
            ]
        raise ValueError("Expected JSON (.json) or a Markdown transcript (.md)")

    try:
        data = json.loads(text_stripped)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e
    assert_json_depth(data)

    if isinstance(data, list) and data and isinstance(data[0], dict) and "mapping" in data[0]:
        return parse_chatgpt_export(data)
    if isinstance(data, dict) and "mapping" in data:
        return parse_chatgpt_export(data)
    if isinstance(data, dict) and (
        "steps" in data or "trajectory" in data or "cascade_id" in data or "transcript" in data
    ):
        return parse_antigravity_export(data)
    if isinstance(data, list) and data and isinstance(data[0], dict) and (
        "steps" in data[0] or "trajectory" in data[0] or "cascade_id" in data[0]
    ):
        return parse_antigravity_export(data)
    try:
        return parse_chatgpt_export(data)
    except ValueError:
        return parse_antigravity_export(data)


def materialize_as_local(
    *,
    title: str,
    messages: list[dict[str, str]],
    source: str,
    settings: Settings | None = None,
    workspace: str | None = None,
) -> LocalChat:
    s = settings or get_settings()
    chat = create_local_chat(title=(title or "Imported chat")[:200], workspace=workspace, settings=s)
    chat.source = source
    chat.messages = [
        LocalMessage(role=m["role"], text=m["text"])
        for m in _clamp_imported_messages(messages)
    ]
    chat.updated_at = int(time.time() * 1000)
    _save(chat, s)
    return chat


def fork_thread_to_local(
    thread: ChatThread,
    *,
    settings: Settings | None = None,
    chat_id: str | None = None,
    preserve_id: bool = False,
) -> LocalChat:
    """Materialize an external thread into a local Portage chat.

    When ``preserve_id`` is True (Rewind / truncate), keep the original chat id so
    the UI does not jump to a new conversation. Cursor origin fields are stored so
    write-back can still update the Agent UI after materialization.
    """
    s = settings or get_settings()
    preferred = (chat_id or "").strip() if preserve_id else ""
    if preferred:
        existing = load_local_chat(preferred, s)
        if existing:
            return existing
        now = int(time.time() * 1000)
        chat = LocalChat(
            id=preferred,
            title=(thread.summary.title or "Conversation")[:200],
            created_at=now,
            updated_at=now,
            workspace=None,
            source="local",
            messages=[],
        )
    else:
        chat = create_local_chat(title=thread.summary.title, settings=s)
        chat.source = "local"

    src = (thread.summary.source or "import").strip().lower()
    transcript_path = (thread.summary.transcript_path or "").strip() or None
    if src == "cursor" or (transcript_path and "agent-transcripts" in transcript_path):
        chat.origin_chat_id = thread.summary.id
        chat.origin_transcript_path = transcript_path

    chat.messages = []
    for m in thread.messages:
        if m.role not in ("user", "assistant"):
            continue
        file_changes = [
            b
            for b in (m.blocks or [])
            if isinstance(b, dict) and b.get("type") == "file_change"
        ]
        chat.messages.append(
            LocalMessage(
                role=m.role,
                text=m.text,
                blocks=list(m.blocks) if m.blocks else None,
                file_changes=file_changes or None,
            )
        )
    chat.updated_at = int(time.time() * 1000)
    _save(chat, s)
    return chat

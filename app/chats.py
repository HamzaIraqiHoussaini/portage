from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings, get_settings

def _infer_file_changes(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best-effort file_change cards from tool_use inputs in imported transcripts."""
    out: list[dict[str, Any]] = []
    for b in blocks:
        if b.get("type") != "tool_use":
            continue
        name = str(b.get("name") or "").lower()
        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
        path = inp.get("path") or inp.get("file_path") or inp.get("target_file")
        if not path:
            continue
        diff = ""
        op = "update"
        if "apply_patch" in name or name in ("write", "write_file", "search_replace", "strreplace"):
            content = inp.get("content") or inp.get("new_string") or inp.get("new_str")
            old = inp.get("old_string") or inp.get("old_str") or ""
            if isinstance(content, str) and content and isinstance(old, str):
                from .agent_tools import unified_diff

                diff = unified_diff(str(path), old, content if "new" in str(inp.keys()) or name == "write" else content)
                if name.startswith("write") and not old:
                    op = "create"
            elif isinstance(content, str):
                diff = f"--- a/{path}\n+++ b/{path}\n@@\n" + "\n".join(
                    f"+{line}" for line in content.splitlines()[:80]
                )
                op = "create"
        if path:
            out.append(
                {
                    "type": "file_change",
                    "path": str(path),
                    "op": op,
                    "diff": diff,
                    "tool": b.get("name"),
                }
            )
    return out


TAG_RE = re.compile(r"</?(?:user_query|timestamp|think)[^>]*>", re.IGNORECASE)
USER_QUERY_RE = re.compile(
    r"<user_query>\s*([\s\S]*?)\s*</user_query>", re.IGNORECASE
)
TIMESTAMP_BLOCK_RE = re.compile(
    r"<timestamp>\s*[\s\S]*?\s*</timestamp>", re.IGNORECASE
)


@dataclass
class ChatMessage:
    role: str
    text: str
    raw: dict[str, Any] | None = None
    blocks: list[dict[str, Any]] | None = None


@dataclass
class ChatSummary:
    id: str
    title: str
    updated_at: int
    project: str
    transcript_path: str
    source: str = "agent-transcript"
    preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "updated_at": self.updated_at,
            "project": self.project,
            "transcript_path": self.transcript_path,
            "source": self.source,
            "preview": self.preview,
        }


@dataclass
class ChatThread:
    summary: ChatSummary
    messages: list[ChatMessage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        msgs = []
        for m in self.messages:
            if m.role not in ("user", "assistant"):
                continue
            item: dict[str, Any] = {"role": m.role, "text": m.text}
            if m.blocks:
                item["blocks"] = m.blocks
            msgs.append(item)
        return {
            **self.summary.to_dict(),
            "messages": msgs,
        }


def _extract_blocks(content: Any) -> list[dict[str, Any]]:
    """Structured content blocks for rich UI (tool cards, thinking, text)."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, str):
                if block.strip():
                    blocks.append({"type": "text", "text": block})
                continue
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = str(block.get("text") or "")
                if text.strip():
                    blocks.append({"type": "text", "text": text})
            elif btype == "tool_use":
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": block.get("id"),
                        "name": block.get("name") or "tool",
                        "input": block.get("input") or {},
                    }
                )
            elif btype == "tool_result":
                content_val = block.get("content")
                if isinstance(content_val, list):
                    bits = []
                    for c in content_val:
                        if isinstance(c, dict) and c.get("text"):
                            bits.append(str(c["text"]))
                        elif isinstance(c, str):
                            bits.append(c)
                    content_val = "\n".join(bits)
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.get("tool_use_id") or block.get("toolUseId"),
                        "content": str(content_val or "")[:8000],
                        "is_error": bool(block.get("is_error")),
                    }
                )
            elif btype in ("thinking", "reasoning"):
                think = str(block.get("thinking") or block.get("text") or "")
                if think.strip():
                    blocks.append({"type": "thinking", "text": think})
            elif btype == "file_change" or block.get("path") and block.get("diff"):
                blocks.append(
                    {
                        "type": "file_change",
                        "path": block.get("path"),
                        "op": block.get("op") or "update",
                        "diff": block.get("diff") or "",
                    }
                )
        return blocks
    if isinstance(content, dict):
        return _extract_blocks([content])
    return [{"type": "text", "text": str(content)}]


def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "tool_use":
                    name = block.get("name") or "tool"
                    parts.append(f"[tool_use:{name}]")
                elif block.get("type") == "tool_result":
                    parts.append("[tool_result]")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "")
    return str(content)


def _clean_user_text(text: str) -> str:
    raw = text or ""
    m = USER_QUERY_RE.search(raw)
    if m:
        t = m.group(1)
    else:
        t = TIMESTAMP_BLOCK_RE.sub("", raw)
        t = TAG_RE.sub("", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


def _plain_preview(text: str, limit: int = 160) -> str:
    """Strip tools / markdown / math for chat-list snippets."""
    t = TAG_RE.sub("", text or "")
    t = re.sub(r"\[tool_use:[^\]]+\]", " ", t)
    t = re.sub(r"\[tool_result\]", " ", t)
    t = re.sub(r"\$\$[\s\S]+?\$\$", " ", t)
    t = re.sub(r"\\\[[\s\S]+?\\\]", " ", t)
    t = re.sub(r"\\\([\s\S]+?\\\)", " ", t)
    t = re.sub(r"(?<!\$)\$(?!\$)(?:\\\$|[^$\n])+?\$(?!\$)", " ", t)
    t = re.sub(r"`{1,3}[^`]*`{1,3}", " ", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"[*_~>|]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        return t[: limit - 1] + "…"
    return t


def _human_project_name(slug: str) -> str:
    name = slug
    for prefix in ("Users-hamza-", "Users-", "home-"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    return name.replace("-", " ")


def _title_from_messages(messages: list[ChatMessage], fallback: str) -> str:
    for m in messages:
        if m.role == "user" and m.text.strip():
            line = _plain_preview(m.text.strip().splitlines()[0], limit=80)
            return line or fallback
    return fallback


def _mtime_ms(path: Path) -> int:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return 0


def discover_transcripts(settings: Settings | None = None) -> list[ChatSummary]:
    s = settings or get_settings()
    out: list[ChatSummary] = []
    projects = s.projects_dir
    if not projects.is_dir():
        return out

    # Prefer titles from conversation-search.db
    titles = _load_search_titles(s)

    for project_dir in projects.iterdir():
        if not project_dir.is_dir():
            continue
        transcripts_root = project_dir / "agent-transcripts"
        if not transcripts_root.is_dir():
            continue
        for convo_dir in transcripts_root.iterdir():
            if not convo_dir.is_dir():
                continue
            jsonl = convo_dir / f"{convo_dir.name}.jsonl"
            if not jsonl.exists():
                # any jsonl inside
                candidates = list(convo_dir.glob("*.jsonl"))
                if not candidates:
                    continue
                jsonl = candidates[0]
            cid = convo_dir.name
            title = titles.get(cid) or cid
            preview = ""
            try:
                # cheap peek: last user/assistant text
                msgs = read_transcript_messages(jsonl, limit_tail=6)
                if not titles.get(cid):
                    title = _title_from_messages(msgs, cid)
                for m in reversed(msgs):
                    cleaned = _plain_preview(m.text)
                    if cleaned:
                        preview = cleaned
                        break
            except OSError:
                pass
            out.append(
                ChatSummary(
                    id=cid,
                    title=title,
                    updated_at=_mtime_ms(jsonl),
                    project=_human_project_name(project_dir.name),
                    transcript_path=str(jsonl),
                    preview=preview,
                )
            )

    out.sort(key=lambda c: c.updated_at, reverse=True)
    return out


def _load_search_titles(settings: Settings) -> dict[str, str]:
    db = settings.conversation_search_db
    if not db.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT id, title FROM conversations WHERE title IS NOT NULL AND title != ''"
        ).fetchall()
        con.close()
        return {r[0]: r[1] for r in rows}
    except sqlite3.Error:
        return {}


def read_transcript_messages(path: Path | str, limit_tail: int | None = None) -> list[ChatMessage]:
    path = Path(path)
    messages: list[ChatMessage] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "turn_ended":
                continue
            role = obj.get("role")
            if role not in ("user", "assistant", "system"):
                continue
            msg = obj.get("message") or {}
            raw_content = msg.get("content")
            blocks = _extract_blocks(raw_content)
            text = _extract_text(raw_content)
            if role == "user":
                text = _clean_user_text(text)
                # clean blocks text too for user_query
                for b in blocks:
                    if b.get("type") == "text":
                        b["text"] = _clean_user_text(str(b.get("text") or ""))
            if not text.strip() and role == "assistant":
                if isinstance(raw_content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_use" for b in raw_content
                ):
                    text = _extract_text(raw_content)
            # Recover simple file_changes from Write/StrReplace-like tool inputs
            file_changes = _infer_file_changes(blocks)
            if file_changes:
                blocks = list(blocks) + file_changes
            messages.append(ChatMessage(role=role, text=text, raw=obj, blocks=blocks or None))

    if limit_tail is not None and len(messages) > limit_tail:
        return messages[-limit_tail:]
    return messages


def load_thread(
    chat_id: str,
    settings: Settings | None = None,
    *,
    transcript_path: str | None = None,
) -> ChatThread | None:
    s = settings or get_settings()
    wanted = None
    if transcript_path:
        try:
            wanted = str(Path(transcript_path).expanduser().resolve())
        except OSError:
            wanted = transcript_path
    for summary in discover_transcripts(s):
        if summary.id != chat_id:
            continue
        if wanted:
            try:
                if str(Path(summary.transcript_path).resolve()) != wanted:
                    continue
            except OSError:
                if summary.transcript_path != transcript_path:
                    continue
        messages = read_transcript_messages(summary.transcript_path)
        if summary.title == summary.id:
            summary.title = _title_from_messages(messages, summary.id)
        return ChatThread(summary=summary, messages=messages)
    return None


def find_summary(
    chat_id: str,
    settings: Settings | None = None,
    *,
    transcript_path: str | None = None,
) -> ChatSummary | None:
    wanted = None
    if transcript_path:
        try:
            wanted = str(Path(transcript_path).expanduser().resolve())
        except OSError:
            wanted = transcript_path
    for summary in discover_transcripts(settings):
        if summary.id != chat_id:
            continue
        if wanted:
            try:
                if str(Path(summary.transcript_path).resolve()) != wanted:
                    continue
            except OSError:
                if summary.transcript_path != transcript_path:
                    continue
        return summary
    return None


def messages_for_foundry(thread: ChatThread, *, max_messages: int = 80) -> list[dict[str, str]]:
    """Convert transcript to Anthropic-style messages (user/assistant only, text)."""
    cleaned: list[dict[str, str]] = []
    for m in thread.messages:
        if m.role not in ("user", "assistant"):
            continue
        text = m.text.strip()
        if not text:
            continue
        # Drop pure tool chatter for model context size
        if text.startswith("[tool_use:") and "\n" not in text and len(text) < 80:
            continue
        cleaned.append({"role": m.role, "content": text})

    # Merge consecutive same-role messages (Anthropic requirement)
    merged: list[dict[str, str]] = []
    for item in cleaned:
        if merged and merged[-1]["role"] == item["role"]:
            merged[-1]["content"] += "\n\n" + item["content"]
        else:
            merged.append(dict(item))

    if len(merged) > max_messages:
        merged = merged[-max_messages:]
    # Must start with user
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged


def now_ms() -> int:
    return int(time.time() * 1000)

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings, get_settings


def _timestamp_tag(tz_name: str = "UTC") -> str:
    try:
        now = datetime.now().astimezone()
    except Exception:
        now = datetime.now().astimezone()
    # Portable format close to Cursor's transcript stamps
    return now.strftime("%A, %b %d, %Y, %I:%M %p (%Z)").replace(" 0", " ")


def format_user_query(text: str) -> list[dict[str, str]]:
    stamped = (
        f"<timestamp>{_timestamp_tag()}</timestamp>\n"
        f"<user_query>\n{text.strip()}\n</user_query>"
    )
    return [{"type": "text", "text": stamped}]


def format_assistant_text(text: str) -> list[dict[str, str]]:
    body = text.strip()
    # Mark bridge origin so it's visible in Cursor
    if not body.startswith("[foundry-bridge]"):
        body = f"[foundry-bridge]\n{body}"
    return [{"type": "text", "text": body}]


def append_transcript_exchange(
    transcript_path: str | Path,
    *,
    user_text: str,
    assistant_text: str,
) -> dict[str, Any]:
    path = Path(transcript_path)
    if not path.exists():
        raise FileNotFoundError(f"Transcript not found: {path}")

    user_line = {"role": "user", "message": {"content": format_user_query(user_text)}}
    assistant_line = {
        "role": "assistant",
        "message": {"content": format_assistant_text(assistant_text)},
    }
    ended = {"type": "turn_ended", "status": "success"}

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(user_line, ensure_ascii=False) + "\n")
        f.write(json.dumps(assistant_line, ensure_ascii=False) + "\n")
        f.write(json.dumps(ended, ensure_ascii=False) + "\n")

    # Touch mtime
    now = time.time()
    try:
        path.touch()
    except OSError:
        pass

    return {
        "transcript_path": str(path),
        "appended": 3,
        "mtime": now,
    }


def update_conversation_search(
    chat_id: str,
    *,
    user_text: str,
    assistant_text: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Append new text into Cursor's conversation FTS index so search stays current."""
    s = settings or get_settings()
    db = s.conversation_search_db
    if not db.exists():
        return {"updated": False, "reason": "conversation-search.db missing"}

    snippet = f"\n\n[foundry-bridge]\nUser: {user_text.strip()}\nAssistant: {assistant_text.strip()}\n"
    updated_at = int(time.time() * 1000)

    try:
        con = sqlite3.connect(str(db), timeout=5)
        con.execute("PRAGMA busy_timeout=5000")
        row = con.execute(
            "SELECT fts_rowid, title FROM conversations WHERE id = ?",
            (chat_id,),
        ).fetchone()
        if not row:
            con.close()
            return {"updated": False, "reason": "conversation id not in search index"}

        fts_rowid, title = row
        # Read existing FTS body
        existing = con.execute(
            "SELECT title, body FROM conversation_fts WHERE rowid = ?",
            (fts_rowid,),
        ).fetchone()
        old_title = (existing[0] if existing else title) or title or chat_id
        old_body = (existing[1] if existing else "") or ""
        new_body = old_body + snippet

        con.execute(
            "UPDATE conversation_fts SET title = ?, body = ? WHERE rowid = ?",
            (old_title, new_body, fts_rowid),
        )
        con.execute(
            "UPDATE conversations SET updated_at = ?, title = ? WHERE fts_rowid = ?",
            (updated_at, old_title, fts_rowid),
        )
        con.commit()
        con.close()
        return {"updated": True, "updated_at": updated_at, "title": old_title}
    except sqlite3.Error as e:
        return {"updated": False, "reason": str(e)}


def write_back(
    chat_id: str,
    transcript_path: str | Path,
    *,
    user_text: str,
    assistant_text: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    if not s.writeback_enabled:
        return {"enabled": False, "transcript": None, "search": None}

    transcript_result = append_transcript_exchange(
        transcript_path, user_text=user_text, assistant_text=assistant_text
    )
    search_result = update_conversation_search(
        chat_id, user_text=user_text, assistant_text=assistant_text, settings=s
    )
    return {
        "enabled": True,
        "transcript": transcript_result,
        "search": search_result,
        "note": (
            "Appended to Cursor agent-transcripts JSONL and refreshed conversation search. "
            "Re-open or refresh the chat in Cursor to see the bridged turn. "
            "Composer live bubble UI may lag until Cursor reloads the transcript."
        ),
    }

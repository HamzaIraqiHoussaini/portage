from __future__ import annotations

import json
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timezone
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
    from .chats import strip_fake_tool_markers

    body = strip_fake_tool_markers(text)
    # Mark bridge origin so it's visible in Cursor
    if not body.startswith("[foundry-bridge]"):
        body = f"[foundry-bridge]\n{body}"
    return [{"type": "text", "text": body}]


def cursor_app_running() -> bool:
    """Best-effort check: Cursor process present (macOS/Linux)."""
    try:
        r = subprocess.run(
            ["pgrep", "-x", "Cursor"],
            capture_output=True,
            timeout=2,
            check=False,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


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


def _rich_text_doc(plain: str) -> str:
    return json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": plain}],
                }
            ],
        },
        ensure_ascii=False,
    )


def _empty_lists_bubble() -> dict[str, Any]:
    """Minimal bubble shell matching Cursor agent `_v: 3` list fields."""
    return {
        "approximateLintErrors": [],
        "lints": [],
        "codebaseContextChunks": [],
        "commits": [],
        "pullRequests": [],
        "attachedCodeChunks": [],
        "assistantSuggestedDiffs": [],
        "gitDiffs": [],
        "interpreterResults": [],
        "images": [],
        "attachedFolders": [],
        "attachedFoldersNew": [],
        "userResponsesToSuggestedCodeBlocks": [],
        "suggestedCodeBlocks": [],
        "diffsForCompressingFiles": [],
        "relevantFiles": [],
        "toolResults": [],
        "notepads": [],
        "capabilities": [],
        "multiFileLinterErrors": [],
        "diffHistories": [],
        "recentLocationsHistory": [],
        "recentlyViewedFiles": [],
        "fileDiffTrajectories": [],
        "docsReferences": [],
        "webReferences": [],
        "aiWebSearchResults": [],
        "attachedFoldersListDirResults": [],
        "humanChanges": [],
        "summarizedComposers": [],
        "cursorRules": [],
        "cursorCommands": [],
        "pastChats": [],
        "contextPieces": [],
        "editTrailContexts": [],
        "allThinkingBlocks": [],
        "diffsSinceLastApply": [],
        "deletedFiles": [],
        "supportedTools": [],
        "attachedFileCodeChunksMetadataOnly": [],
        "consoleLogs": [],
        "uiElementPicked": [],
        "knowledgeItems": [],
        "documentationSelections": [],
        "externalLinks": [],
        "projectLayouts": [],
        "capabilityContexts": [],
        "todos": [],
        "mcpDescriptors": [],
        "workspaceUris": [],
    }


def _make_bubble(
    *,
    bubble_id: str,
    type_: int,
    text: str,
    created_at: str,
) -> dict[str, Any]:
    bubble = _empty_lists_bubble()
    bubble.update(
        {
            "_v": 3,
            "type": type_,
            "bubbleId": bubble_id,
            "text": text,
            "createdAt": created_at,
            "unifiedMode": 2,
            "isAgentic": type_ == 2,
            "existedSubsequentTerminalCommand": False,
            "existedPreviousTerminalCommand": False,
            "attachedHumanChanges": False,
            "cursorCommandsExplicitlySet": False,
            "pastChatsExplicitlySet": False,
            "isRefunded": False,
            "isPlanExecution": False,
            "tokenCount": {"inputTokens": 0, "outputTokens": 0},
            "requestId": "",
            "serverBubbleId": bubble_id if type_ == 1 else "",
            "conversationState": "",
            "conversationTurnIndex": 0,
        }
    )
    if type_ == 1:
        bubble["richText"] = _rich_text_doc(text)
        bubble["context"] = {
            "composers": [],
            "selectedCommits": [],
            "selectedPullRequests": [],
            "selectedImages": [],
            "selectedDocuments": [],
            "selectedVideos": [],
            "folderSelections": [],
            "fileSelections": [],
            "terminalFiles": [],
            "selections": [],
            "terminalSelections": [],
            "selectedDocs": [],
            "externalLinks": [],
            "cursorRules": [],
            "cursorCommands": [],
            "gitPRDiffSelections": [],
            "subagentSelections": [],
            "browserSelections": [],
            "extraContext": [],
            "mentions": {},
        }
    return bubble


def _header_for(bubble: dict[str, Any]) -> dict[str, Any]:
    text = str(bubble.get("text") or "")
    preview = text.replace("\n", " ").strip()[:160]
    header: dict[str, Any] = {
        "bubbleId": bubble["bubbleId"],
        "type": bubble["type"],
        "createdAt": bubble["createdAt"],
        "grouping": {
            "isRenderable": True,
            "hasText": True,
            "textPreview": preview,
            "toolDisplayComputed": True,
        },
    }
    if bubble.get("type") == 1:
        header["serverBubbleId"] = bubble["bubbleId"]
        header["grouping"]["isShortPlainText"] = len(text) < 120 and "\n" not in text
    else:
        header["grouping"]["isKeptFinalAiVisibleOutsideWorkedForGroup"] = True
        # Rough height hint so the list can scroll; Cursor recomputes on open.
        header["contentHeightHint"] = min(800, max(40, 20 + text.count("\n") * 18 + len(text) // 4))
    return header


def append_composer_bubbles(
    chat_id: str,
    *,
    user_text: str,
    assistant_text: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Append user+assistant bubbles into Cursor's Agent UI store (state.vscdb).

    Cursor's sidebar/Agent panel reads `composerData:{id}` + `bubbleId:{id}:{bubble}`
    from cursorDiskKV — not the agent-transcripts JSONL. JSONL-only writeback is why
    quit/refresh never showed Portage turns.

    Safest when Cursor is fully quit; if Cursor is running it may overwrite on exit.
    """
    s = settings or get_settings()
    db = s.state_vscdb
    if not db.exists():
        return {"updated": False, "reason": "state.vscdb missing"}

    running = cursor_app_running()
    user_plain = user_text.strip()
    asst_plain = assistant_text.strip()
    if not asst_plain.startswith("[foundry-bridge]"):
        asst_plain = f"[foundry-bridge]\n{asst_plain}"

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    user_id = str(uuid.uuid4())
    asst_id = str(uuid.uuid4())
    user_bubble = _make_bubble(bubble_id=user_id, type_=1, text=user_plain, created_at=created_at)
    asst_bubble = _make_bubble(bubble_id=asst_id, type_=2, text=asst_plain, created_at=created_at)
    user_header = _header_for(user_bubble)
    asst_header = _header_for(asst_bubble)
    now_ms = int(time.time() * 1000)

    composer_key = f"composerData:{chat_id}"
    try:
        con = sqlite3.connect(str(db), timeout=8)
        con.execute("PRAGMA busy_timeout=8000")
        row = con.execute(
            "SELECT value FROM cursorDiskKV WHERE key = ?",
            (composer_key,),
        ).fetchone()
        if not row:
            con.close()
            return {
                "updated": False,
                "reason": "composerData missing for this chat (Agent UI store)",
                "cursor_running": running,
            }

        raw = row[0]
        if isinstance(raw, memoryview):
            raw = raw.tobytes()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        composer = json.loads(raw)
        headers = composer.get("fullConversationHeadersOnly")
        if not isinstance(headers, list):
            headers = []
            composer["fullConversationHeadersOnly"] = headers
        headers.append(user_header)
        headers.append(asst_header)
        composer["lastUpdatedAt"] = now_ms
        composer["status"] = composer.get("status") or "completed"
        composer["hasUnreadMessages"] = True
        # Clear any in-progress generation markers from a prior Cursor session.
        if isinstance(composer.get("generatingBubbleIds"), list):
            composer["generatingBubbleIds"] = []
        composer["isContinuationInProgress"] = False

        con.execute(
            "INSERT OR REPLACE INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (f"bubbleId:{chat_id}:{user_id}", json.dumps(user_bubble, ensure_ascii=False)),
        )
        con.execute(
            "INSERT OR REPLACE INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (f"bubbleId:{chat_id}:{asst_id}", json.dumps(asst_bubble, ensure_ascii=False)),
        )
        con.execute(
            "INSERT OR REPLACE INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (composer_key, json.dumps(composer, ensure_ascii=False)),
        )
        con.commit()
        con.close()
        return {
            "updated": True,
            "user_bubble_id": user_id,
            "assistant_bubble_id": asst_id,
            "headers_len": len(headers),
            "lastUpdatedAt": now_ms,
            "cursor_running": running,
        }
    except sqlite3.Error as e:
        return {"updated": False, "reason": str(e), "cursor_running": running}
    except (TypeError, ValueError, json.JSONDecodeError) as e:
        return {"updated": False, "reason": str(e), "cursor_running": running}


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
        return {"enabled": False, "transcript": None, "search": None, "composer": None}

    running = cursor_app_running()
    transcript_result = append_transcript_exchange(
        transcript_path, user_text=user_text, assistant_text=assistant_text
    )
    search_result = update_conversation_search(
        chat_id, user_text=user_text, assistant_text=assistant_text, settings=s
    )
    composer_result = append_composer_bubbles(
        chat_id, user_text=user_text, assistant_text=assistant_text, settings=s
    )

    if composer_result.get("updated"):
        if running:
            note = (
                "Wrote into Cursor’s Agent chat store and transcript. "
                "Cursor is still running — fully quit it (not just reload), then reopen "
                "so it doesn’t overwrite this turn from memory."
            )
        else:
            note = (
                "Wrote into Cursor’s Agent chat store and transcript. "
                "Reopen the chat in Cursor to see the bridged turn."
            )
    else:
        reason = composer_result.get("reason") or "Agent UI store not updated"
        note = (
            f"Appended to the transcript file, but Cursor’s Agent panel was not updated ({reason}). "
            "Quit Cursor completely and send again with Write-back on, or keep using Portage for this thread."
        )

    return {
        "enabled": True,
        "transcript": transcript_result,
        "search": search_result,
        "composer": composer_result,
        "cursor_running": running,
        "note": note,
    }

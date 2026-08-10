from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resolve_root() -> Path:
    """Project / bundle root (read-only assets when frozen)."""
    if _is_frozen():
        # PyInstaller onefile extracts to _MEIPASS; onedir uses exe dir.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resolve_data_dir(root: Path) -> Path:
    """Writable data dir — user home when frozen so the app bundle stays read-only."""
    if _is_frozen():
        base = Path.home() / ".portage"
        legacy = Path.home() / ".cursor-foundry-chat"
        # One-time soft migration from the pre-rename data folder
        if not base.exists() and legacy.is_dir():
            base = legacy
    else:
        base = root / "data"
    base.mkdir(parents=True, exist_ok=True)
    (base / "conversations").mkdir(parents=True, exist_ok=True)
    return base


ROOT = resolve_root()
DATA_DIR = resolve_data_dir(ROOT)
CONVERSATIONS_DIR = DATA_DIR / "conversations"


def _cursor_support_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Cursor"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Cursor"
    # Linux / other
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "Cursor"
    return Path.home() / ".config/Cursor"


def _env_files() -> tuple[str, ...]:
    candidates = [
        DATA_DIR / ".env",
        Path.home() / ".portage" / ".env",
        Path.home() / ".cursor-foundry-chat" / ".env",
        ROOT / ".env",
    ]
    # de-dupe while preserving order
    seen: set[str] = set()
    existing: list[str] = []
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            existing.append(key)
    return tuple(existing)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    provider: str = Field(default="foundry", alias="PROVIDER")  # foundry | aws

    foundry_messages_url: str = Field(
        default="https://YOUR-RESOURCE.services.ai.azure.com/anthropic/v1/messages",
        alias="FOUNDRY_MESSAGES_URL",
    )
    foundry_api_key: str = Field(default="", alias="FOUNDRY_API_KEY")
    foundry_model: str = Field(default="claude-opus-5", alias="FOUNDRY_MODEL")
    anthropic_version: str = Field(default="2023-06-01", alias="ANTHROPIC_VERSION")
    foundry_max_tokens: int = Field(default=8192, alias="FOUNDRY_MAX_TOKENS")

    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_access_key_id: str = Field(default="", alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str = Field(default="", alias="AWS_SECRET_ACCESS_KEY")
    aws_session_token: str = Field(default="", alias="AWS_SESSION_TOKEN")
    aws_model_id: str = Field(
        default="anthropic.claude-opus-4-20250514-v1:0",
        alias="AWS_MODEL_ID",
    )

    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8765, alias="APP_PORT")
    writeback_enabled: bool = Field(default=True, alias="WRITEBACK_ENABLED")
    cursor_link_enabled: bool = Field(default=True, alias="CURSOR_LINK_ENABLED")
    claude_code_link_enabled: bool = Field(default=True, alias="CLAUDE_CODE_LINK_ENABLED")
    skills_max_chars: int = Field(default=120_000, alias="SKILLS_MAX_CHARS")

    cursor_support_dir: Path = Field(default_factory=_cursor_support_dir)
    cursor_user_dir: Path = Field(default_factory=lambda: _cursor_support_dir() / "User")
    cursor_home: Path = Field(default=Path.home() / ".cursor")
    agents_home: Path = Field(default=Path.home() / ".agents")
    claude_home: Path = Field(default=Path.home() / ".claude")

    @property
    def config_path(self) -> Path:
        return DATA_DIR / "bridge-config.json"

    @property
    def state_vscdb(self) -> Path:
        return self.cursor_user_dir / "globalStorage" / "state.vscdb"

    @property
    def conversation_search_db(self) -> Path:
        return self.cursor_user_dir / "globalStorage" / "conversation-search.db"

    @property
    def settings_json(self) -> Path:
        return self.cursor_user_dir / "settings.json"

    @property
    def projects_dir(self) -> Path:
        return self.cursor_home / "projects"

    @property
    def conversations_dir(self) -> Path:
        return CONVERSATIONS_DIR


@lru_cache
def get_settings() -> Settings:
    return Settings()

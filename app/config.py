from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    foundry_messages_url: str = Field(
        default="https://YOUR-RESOURCE.services.ai.azure.com/anthropic/v1/messages",
        alias="FOUNDRY_MESSAGES_URL",
    )
    foundry_api_key: str = Field(default="", alias="FOUNDRY_API_KEY")
    foundry_model: str = Field(default="claude-opus-5", alias="FOUNDRY_MODEL")
    anthropic_version: str = Field(default="2023-06-01", alias="ANTHROPIC_VERSION")
    foundry_max_tokens: int = Field(default=8192, alias="FOUNDRY_MAX_TOKENS")

    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8765, alias="APP_PORT")
    writeback_enabled: bool = Field(default=True, alias="WRITEBACK_ENABLED")
    skills_max_chars: int = Field(default=120_000, alias="SKILLS_MAX_CHARS")

    cursor_support_dir: Path = Field(
        default=Path.home() / "Library/Application Support/Cursor"
    )
    cursor_user_dir: Path = Field(
        default=Path.home() / "Library/Application Support/Cursor/User"
    )
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


@lru_cache
def get_settings() -> Settings:
    return Settings()

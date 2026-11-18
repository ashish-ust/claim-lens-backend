"""Application settings, loaded from .env and environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """Typed view of the environment. Never log api_key."""

    anthropic_api_key: str
    claude_model: str
    mock_mode: bool
    data_dir: Path
    cors_origins: list[str]
    dup_phash_near: int
    dup_phash_possible: int
    max_upload_mb: int

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def db_path(self) -> Path:
        return self.data_dir / "claimlens.db"


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load() -> Settings:
    # Values already in the real environment win over .env (load_dotenv default).
    load_dotenv(PROJECT_DIR / ".env")

    # Relative DATA_DIR is resolved against the project root, not the current directory,
    # so `uvicorn` works the same from any folder.
    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    if not data_dir.is_absolute():
        data_dir = (PROJECT_DIR / data_dir).resolve()

    origins = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5").strip(),
        mock_mode=_bool(os.getenv("MOCK_MODE", "false")),
        data_dir=data_dir,
        cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
        dup_phash_near=int(os.getenv("DUP_PHASH_NEAR", "6")),
        dup_phash_possible=int(os.getenv("DUP_PHASH_POSSIBLE", "12")),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "10")),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings. Tests call get_settings.cache_clear() after changing env."""
    return _load()

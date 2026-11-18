"""SQLite schema and small helpers. Metadata only; image bytes live on disk under DATA_DIR."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    analysis_id   TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    status        TEXT NOT NULL,
    narrative     TEXT NOT NULL,
    risk_level    TEXT NOT NULL,
    risk_score    INTEGER NOT NULL,
    result_json   TEXT NOT NULL,
    claim_id      TEXT
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id      TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    narrative     TEXT NOT NULL,
    risk_level    TEXT NOT NULL,
    risk_score    INTEGER NOT NULL,
    result_json   TEXT NOT NULL,
    reviewer_note TEXT
);

CREATE TABLE IF NOT EXISTS claim_images (
    claim_id      TEXT NOT NULL REFERENCES claims(claim_id),
    image_index   INTEGER NOT NULL,
    file_path     TEXT NOT NULL,      -- relative to DATA_DIR
    sha256        TEXT NOT NULL,
    phash         TEXT NOT NULL,      -- 64-bit hex
    dhash         TEXT NOT NULL,      -- 64-bit hex
    PRIMARY KEY (claim_id, image_index)
);

CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id   TEXT NOT NULL,
    decision      TEXT NOT NULL,
    reviewer_note TEXT,
    claim_id      TEXT,
    decided_at    TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    """Open a connection to the app database, creating DATA_DIR if needed."""
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Yield a connection inside a transaction: commit on success, roll back on error, always close."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they do not exist. Safe to call on every startup."""
    with transaction() as conn:
        conn.executescript(SCHEMA)

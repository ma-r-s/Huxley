"""SQLite persistence layer for bookmarks, summaries, and settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite
import structlog

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger()

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS audiobook_progress (
    book_id    TEXT PRIMARY KEY,
    position   REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    summary    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Storage:
    """Async SQLite wrapper for AbuelOS persistent state."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Open database and create tables if needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        await logger.ainfo("storage_initialized", path=str(self._db_path))

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            msg = "Storage not initialized — call init() first"
            raise RuntimeError(msg)
        return self._db

    # --- Audiobook progress ---

    async def save_audiobook_position(self, book_id: str, position: float) -> None:
        await self._conn.execute(
            "INSERT INTO audiobook_progress (book_id, position, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(book_id) DO UPDATE SET position=?, updated_at=datetime('now')",
            (book_id, position, position),
        )
        await self._conn.commit()

    async def get_audiobook_position(self, book_id: str) -> float:
        cursor = await self._conn.execute(
            "SELECT position FROM audiobook_progress WHERE book_id = ?",
            (book_id,),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0

    # --- Conversation summaries ---

    async def save_summary(self, summary: str) -> None:
        await self._conn.execute(
            "INSERT INTO conversation_summaries (summary) VALUES (?)",
            (summary,),
        )
        await self._conn.commit()

    async def get_latest_summary(self) -> str | None:
        cursor = await self._conn.execute(
            "SELECT summary FROM conversation_summaries ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return str(row[0]) if row else None

    # --- Settings ---

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        cursor = await self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return str(row[0]) if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=?",
            (key, value, value),
        )
        await self._conn.commit()

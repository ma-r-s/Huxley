"""SQLite persistence layer for bookmarks, summaries, and settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite
import structlog

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger()

SCHEMA_VERSION = 1
"""Bump when an _SCHEMA change requires a migration. Today's startup check
just records drift in the log; migration runner lands when first migration
is needed (see docs/triage.md T2.1)."""

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

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Storage:
    """Async SQLite wrapper for Huxley persistent state."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    @property
    def db_path(self) -> Path:
        """Filesystem path of the SQLite database (read-only)."""
        return self._db_path

    async def init(self) -> None:
        """Open database, enable WAL, create tables, record schema version."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)

        # WAL mode protects against partial-write corruption on crash and
        # allows concurrent reads while a write is in progress. The -wal
        # sidecar file is acceptable since we already own the data dir.
        # NORMAL synchronous is safe under WAL with the small extra risk of
        # losing the last few transactions on power loss — fine for
        # audiobook positions and conversation summaries.
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")

        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        await self._init_schema_version()
        await logger.ainfo(
            "storage_initialized",
            path=str(self._db_path),
            schema_version=SCHEMA_VERSION,
        )

    async def _init_schema_version(self) -> None:
        """Record the schema version, or warn on drift.

        Fresh DB or pre-versioning DB: insert current version.
        Matching version: no-op.
        Mismatch: log a warning and proceed — no migrations defined yet, so
        the best we can do is record drift. Migration runner (see triage
        T2.1 follow-up) lands when first migration is needed.
        """
        cursor = await self._conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        )
        row = await cursor.fetchone()
        if row is None:
            await self._conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            await self._conn.commit()
            await logger.ainfo("storage_schema_version_set", version=SCHEMA_VERSION)
            return

        current = int(row[0])
        if current != SCHEMA_VERSION:
            await logger.awarning(
                "storage_schema_version_mismatch",
                db=current,
                code=SCHEMA_VERSION,
                note="no migrations defined yet — proceeding with newer code",
            )

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

    async def clear_summaries(self) -> None:
        await self._conn.execute("DELETE FROM conversation_summaries")
        await self._conn.commit()

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

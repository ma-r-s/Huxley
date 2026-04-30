"""SQLite persistence layer for bookmarks, sessions, and settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiosqlite
import structlog

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger()

SCHEMA_VERSION = 2
"""Bump when an _SCHEMA change requires a migration. v2 (2026-04-29) introduces
the `sessions` + `session_turns` tables for browsable conversation history
(T1.12) and retires the single-row `conversation_summaries` table; old DBs
migrate inline on init. The full migration runner (T2.1 follow-up) still
lands when the second migration arrives."""

# Schema for FRESH DBs at SCHEMA_VERSION. Legacy tables (e.g.
# conversation_summaries from v1) are NOT recreated here — the migration
# step in `_init_schema_version` handles their data and drops them.
_SCHEMA = """\
CREATE TABLE IF NOT EXISTS audiobook_progress (
    book_id    TEXT PRIMARY KEY,
    position   REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at     TEXT,
    last_turn_at TEXT,
    turn_count   INTEGER NOT NULL DEFAULT 0,
    preview      TEXT,
    summary      TEXT
);

CREATE TABLE IF NOT EXISTS session_turns (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    role       TEXT NOT NULL CHECK(role IN ('user','assistant')),
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_session_turns_session
    ON session_turns(session_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# How long a previous turn keeps the same logical conversation alive across
# WS reconnects. See T1.12 critic notes — the boundary fix that prevents
# auto-reconnect / language-switch / cost-kill from fragmenting one
# user-visible conversation into many tiny rows.
_DEFAULT_IDLE_WINDOW_MIN = 30

# Cap on the `preview` snippet stored on the sessions row.
_PREVIEW_CHAR_CAP = 200


@dataclass(frozen=True, slots=True)
class SessionMeta:
    """Row in the `sessions` table — one logical conversation."""

    id: int
    started_at: str
    ended_at: str | None
    last_turn_at: str | None
    turn_count: int
    preview: str | None
    summary: str | None


@dataclass(frozen=True, slots=True)
class Turn:
    """One turn within a session — either a user utterance or an
    assistant response, in transcript form."""

    idx: int
    role: str
    text: str
    created_at: str


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
        """Open database, enable WAL, create tables, run pending migrations."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)

        # WAL mode protects against partial-write corruption on crash and
        # allows concurrent reads while a write is in progress. The -wal
        # sidecar file is acceptable since we already own the data dir.
        # NORMAL synchronous is safe under WAL with the small extra risk of
        # losing the last few transactions on power loss — fine for
        # audiobook positions and session turns.
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
        """Record the schema version or run pending migrations.

        Fresh DB: insert current version, no migration needed.
        v1 DB: run the v1 → v2 migration, then bump the recorded version.
        Future-version DB: warn and proceed (older code, newer DB).
        """
        cursor = await self._conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        )
        row = await cursor.fetchone()
        if row is None:
            # Pre-versioning DB or fresh init. Detect the difference by
            # presence of the legacy v1 table — if it exists, migrate.
            if await self._table_exists("conversation_summaries"):
                await self._migrate_v1_to_v2()
            await self._conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            await self._conn.commit()
            await logger.ainfo("storage_schema_version_set", version=SCHEMA_VERSION)
            return

        current = int(row[0])
        if current == SCHEMA_VERSION:
            return

        if current < SCHEMA_VERSION:
            if current == 1:
                await self._migrate_v1_to_v2()
            await self._conn.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )
            await self._conn.commit()
            await logger.ainfo(
                "storage_schema_migrated",
                from_version=current,
                to_version=SCHEMA_VERSION,
            )
            return

        # current > SCHEMA_VERSION: newer DB, older code. No-op + warn.
        await logger.awarning(
            "storage_schema_version_mismatch",
            db=current,
            code=SCHEMA_VERSION,
            note="DB is newer than running code — proceeding without migration",
        )

    async def _table_exists(self, name: str) -> bool:
        cursor = await self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        )
        return (await cursor.fetchone()) is not None

    async def _migrate_v1_to_v2(self) -> None:
        """Move legacy `conversation_summaries` rows into synthetic
        `sessions` rows, then drop the old table.

        Each legacy summary becomes a sessions row with its `summary` set,
        `started_at` and `ended_at` both set to the legacy `created_at`,
        zero turns, no preview. The warm-reconnect path
        (`get_latest_summary`) still finds it and the user's history shows
        the prior conversations as best we can reconstruct from the
        summary-only data.
        """
        if not await self._table_exists("conversation_summaries"):
            return
        cursor = await self._conn.execute(
            "SELECT summary, created_at FROM conversation_summaries ORDER BY id"
        )
        # `fetchall()` is typed as `Iterable[Row]` by aiosqlite even though
        # the runtime always returns a list — materialize so `len(rows)`
        # below is type-clean and we don't iterate twice on a generator.
        rows = list(await cursor.fetchall())
        for summary, created_at in rows:
            await self._conn.execute(
                "INSERT INTO sessions "
                "(started_at, ended_at, summary, turn_count) "
                "VALUES (?, ?, ?, 0)",
                (created_at, created_at, summary),
            )
        await self._conn.execute("DROP TABLE conversation_summaries")
        await self._conn.commit()
        await logger.ainfo("storage_v1_to_v2_migrated", legacy_summaries=len(rows))

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

    # --- Sessions (T1.12) ---

    async def start_or_resume_session(
        self, idle_window_min: int = _DEFAULT_IDLE_WINDOW_MIN
    ) -> int:
        """Return the id of the active session, resuming the most recent
        one if its last turn was within `idle_window_min` minutes;
        otherwise create a new row.

        Resuming clears `ended_at` so the row reads as "live again" until
        the next `end_session`. The user-visible conversation grouping is
        the resume window; WS-connect/disconnect remains the technical
        lifecycle but does not, on its own, end a conversation.
        """
        cursor = await self._conn.execute(
            "SELECT id FROM sessions "
            "WHERE last_turn_at IS NOT NULL "
            "AND last_turn_at >= datetime('now', ?) "
            "ORDER BY id DESC LIMIT 1",
            (f"-{idle_window_min} minutes",),
        )
        row = await cursor.fetchone()
        if row is not None:
            sid = int(row[0])
            await self._conn.execute(
                "UPDATE sessions SET ended_at = NULL WHERE id = ?",
                (sid,),
            )
            await self._conn.commit()
            return sid

        cursor = await self._conn.execute("INSERT INTO sessions DEFAULT VALUES RETURNING id")
        row = await cursor.fetchone()
        if row is None:
            msg = "INSERT ... RETURNING returned no row"
            raise RuntimeError(msg)
        await self._conn.commit()
        return int(row[0])

    async def record_turn(self, session_id: int, role: str, text: str) -> None:
        """Append a turn to a session. Updates `last_turn_at`,
        increments `turn_count`, and lazily sets `preview` on the first
        user-role turn (so proactive assistant turns don't poison the
        snippet)."""
        cursor = await self._conn.execute(
            "SELECT COALESCE(MAX(idx) + 1, 0) FROM session_turns WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        next_idx = int(row[0]) if row else 0

        await self._conn.execute(
            "INSERT INTO session_turns (session_id, idx, role, text) VALUES (?, ?, ?, ?)",
            (session_id, next_idx, role, text),
        )
        await self._conn.execute(
            "UPDATE sessions SET "
            "  turn_count = turn_count + 1, "
            "  last_turn_at = datetime('now'), "
            "  preview = CASE "
            "    WHEN preview IS NULL AND ? = 'user' THEN ? "
            "    ELSE preview "
            "  END "
            "WHERE id = ?",
            (role, text[:_PREVIEW_CHAR_CAP], session_id),
        )
        await self._conn.commit()

    async def end_session(self, session_id: int, summary: str | None) -> None:
        """Finalize a session: stamp `ended_at` and set its summary.
        Idempotent across reconnects — each end overwrites the previous
        ended_at + summary."""
        await self._conn.execute(
            "UPDATE sessions SET ended_at = datetime('now'), summary = ? WHERE id = ?",
            (summary, session_id),
        )
        await self._conn.commit()

    async def list_sessions(self, limit: int = 50) -> list[SessionMeta]:
        """Return up to `limit` sessions, most-recent first."""
        cursor = await self._conn.execute(
            "SELECT id, started_at, ended_at, last_turn_at, turn_count, "
            "       preview, summary "
            "FROM sessions ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            SessionMeta(
                id=int(r[0]),
                started_at=str(r[1]),
                ended_at=str(r[2]) if r[2] is not None else None,
                last_turn_at=str(r[3]) if r[3] is not None else None,
                turn_count=int(r[4]),
                preview=str(r[5]) if r[5] is not None else None,
                summary=str(r[6]) if r[6] is not None else None,
            )
            for r in rows
        ]

    async def get_session_turns(self, session_id: int) -> list[Turn]:
        """Return the turns of a session in idx order."""
        cursor = await self._conn.execute(
            "SELECT idx, role, text, created_at FROM session_turns "
            "WHERE session_id = ? ORDER BY idx",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [
            Turn(
                idx=int(r[0]),
                role=str(r[1]),
                text=str(r[2]),
                created_at=str(r[3]),
            )
            for r in rows
        ]

    async def delete_session(self, session_id: int) -> None:
        """Remove a session and its turns. Privacy floor for T1.12 —
        the PWA's SessionDetailSheet wires the user-visible button.

        Explicitly deletes turns first because per-connection
        `PRAGMA foreign_keys=ON` isn't guaranteed and the schema's
        ON DELETE CASCADE only fires when foreign keys are enforced."""
        await self._conn.execute("DELETE FROM session_turns WHERE session_id = ?", (session_id,))
        await self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await self._conn.commit()

    # --- Conversation summaries (back-compat shims over `sessions`) ---

    async def save_summary(self, summary: str) -> None:
        """Legacy path used by the OpenAI Realtime provider — synthesizes
        a sessions row with no turns. Callers will move to
        `end_session(session_id, summary)` once T1.12 step 3 lands; this
        shim keeps the existing tests + provider working in the meantime.
        """
        await self._conn.execute(
            "INSERT INTO sessions (ended_at, summary, turn_count) VALUES (datetime('now'), ?, 0)",
            (summary,),
        )
        await self._conn.commit()

    async def get_latest_summary(self) -> str | None:
        """Return the most recent non-null summary across all sessions
        (used by the provider to inject warm-reconnect context).
        Skips rows whose summary was nulled by `clear_summaries`."""
        cursor = await self._conn.execute(
            "SELECT summary FROM sessions WHERE summary IS NOT NULL ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return str(row[0]) if row else None

    async def clear_summaries(self) -> None:
        """Nullify every session's summary so the warm-reconnect chain
        breaks. Session metadata (turns, started_at, etc.) is preserved
        — the user's history view is not wiped by a `_on_reset`."""
        await self._conn.execute("UPDATE sessions SET summary = NULL")
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

    async def list_settings(self, prefix: str = "") -> list[tuple[str, str]]:
        """Return every (key, value) whose key starts with `prefix`.

        SQLite LIKE with `ESCAPE '\\'` so callers that pass a prefix
        containing `%` or `_` don't accidentally glob. Empty prefix
        returns everything — use sparingly.
        """
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cursor = await self._conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
            (escaped + "%",),
        )
        rows = await cursor.fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    async def delete_setting(self, key: str) -> None:
        await self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        await self._conn.commit()

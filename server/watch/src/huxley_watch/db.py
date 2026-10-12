"""The persona database, read only (`mode=ro`): never a write, never a
lock held. Skill storage lives in the `settings` table, namespaced
`<skill>:<key>`; the Spotify skill keeps the month's book seconds under
`spotify:book:seconds:<YYYY-MM>` (UTC month, books.py `add_seconds`)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path


@dataclass(frozen=True)
class DbRead:
    book_seconds: float | None  # None: unknown (see problem) or nothing yet
    problem: str | None = None  # why it couldn't be read; None if fine


def book_seconds_key(now: datetime) -> str:
    return f"spotify:book:seconds:{now.astimezone(UTC):%Y-%m}"


def read_db(path: Path, now: datetime, timeout_s: float = 2.0) -> DbRead:
    """This month's audiobook seconds. A missing file, a lock that
    outlasts the timeout, or a missing table is a `problem`, never a
    raise: the page and the other rules carry on without it."""
    if not path.exists():
        return DbRead(None, f"no database at {path}")
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=timeout_s)
    except sqlite3.Error as exc:
        return DbRead(None, f"can't open {path}: {exc}")
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (book_seconds_key(now),)
        ).fetchone()
    except sqlite3.Error as exc:
        return DbRead(None, f"can't read {path}: {exc}")
    finally:
        conn.close()
    if row is None:
        return DbRead(0.0)
    try:
        return DbRead(float(row[0]))
    except (TypeError, ValueError):
        return DbRead(None, f"unexpected book seconds value {row[0]!r}")

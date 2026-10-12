"""The persona DB, read only: missing or locked is a problem, never a crash."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from huxley_watch.db import book_seconds_key, read_db

from .logs import NOW

if TYPE_CHECKING:
    from pathlib import Path


def make_db(path: Path, rows: dict[str, str], wal: bool = True) -> None:
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.executemany("INSERT INTO settings VALUES (?, ?)", rows.items())
    conn.commit()
    conn.close()


def test_reads_this_months_book_seconds(tmp_path: Path) -> None:
    db = tmp_path / "abuelos.db"
    make_db(db, {"spotify:book:seconds:2026-10": "301.4", "spotify:book:seconds:2026-09": "9999"})
    assert book_seconds_key(NOW) == "spotify:book:seconds:2026-10"
    assert read_db(db, NOW).book_seconds == 301.4
    assert read_db(db, NOW).problem is None


def test_nothing_yet_this_month_is_zero(tmp_path: Path) -> None:
    db = tmp_path / "abuelos.db"
    make_db(db, {})
    assert read_db(db, NOW).book_seconds == 0.0


def test_never_writes(tmp_path: Path) -> None:
    db = tmp_path / "abuelos.db"
    make_db(db, {"spotify:book:seconds:2026-10": "10"})
    before = db.read_bytes()
    read_db(db, NOW)
    assert db.read_bytes() == before


def test_never_writes_while_the_server_holds_it_open(tmp_path: Path) -> None:
    """The live case: the server has the DB open in WAL mode, with writes
    still in the -wal file. The read sees them and changes neither file."""
    db = tmp_path / "abuelos.db"
    make_db(db, {"spotify:book:seconds:2026-10": "10"})
    server = sqlite3.connect(db)
    server.execute("PRAGMA wal_autocheckpoint=0")
    server.execute("UPDATE settings SET value = '42.5' WHERE key = 'spotify:book:seconds:2026-10'")
    server.commit()
    wal = tmp_path / "abuelos.db-wal"
    try:
        assert wal.exists()
        before = (db.read_bytes(), wal.read_bytes())
        assert read_db(db, NOW).book_seconds == 42.5  # read through the -wal
        assert (db.read_bytes(), wal.read_bytes()) == before
    finally:
        server.close()


def test_missing_db(tmp_path: Path) -> None:
    result = read_db(tmp_path / "nope.db", NOW)
    assert result.book_seconds is None
    assert result.problem is not None
    assert "no database" in result.problem


def test_locked_db(tmp_path: Path) -> None:
    db = tmp_path / "abuelos.db"
    make_db(db, {"spotify:book:seconds:2026-10": "10"}, wal=False)
    holder = sqlite3.connect(db)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        result = read_db(db, NOW, timeout_s=0.05)
    finally:
        holder.rollback()
        holder.close()
    assert result.book_seconds is None
    assert result.problem is not None
    assert "locked" in result.problem


def test_not_a_huxley_db(tmp_path: Path) -> None:
    db = tmp_path / "other.db"
    sqlite3.connect(db).close()
    result = read_db(db, NOW)
    assert result.book_seconds is None
    assert result.problem is not None

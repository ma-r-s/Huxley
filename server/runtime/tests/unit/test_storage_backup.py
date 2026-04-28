"""Tests for the daily SQLite snapshot helper.

See docs/triage.md T2.1.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from huxley.storage.backup import ensure_daily_snapshot

if TYPE_CHECKING:
    from pathlib import Path


def _make_db(path: Path, value: str = "hello") -> None:
    """Create a small SQLite DB with a known row, for snapshot verification."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _read_value(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        cursor = conn.execute("SELECT v FROM t LIMIT 1")
        row = cursor.fetchone()
        assert row is not None
        return str(row[0])
    finally:
        conn.close()


class TestEnsureDailySnapshot:
    def test_returns_none_when_source_db_missing(self, tmp_path: Path) -> None:
        result = ensure_daily_snapshot(tmp_path / "nope.db")
        assert result is None

    def test_creates_snapshot_with_dated_filename(self, tmp_path: Path) -> None:
        db = tmp_path / "abuelos.db"
        _make_db(db, "first")
        today = datetime(2026, 4, 18, tzinfo=UTC)

        snap = ensure_daily_snapshot(db, today=today)

        assert snap is not None
        assert snap.name == "abuelos-2026-04-18.db"
        assert snap.exists()
        assert _read_value(snap) == "first"

    def test_default_backup_dir_is_sibling_backups_folder(self, tmp_path: Path) -> None:
        db = tmp_path / "abuelos.db"
        _make_db(db)
        today = datetime(2026, 4, 18, tzinfo=UTC)

        snap = ensure_daily_snapshot(db, today=today)

        assert snap is not None
        assert snap.parent == tmp_path / "backups"
        assert snap.parent.exists()

    def test_custom_backup_dir(self, tmp_path: Path) -> None:
        db = tmp_path / "abuelos.db"
        _make_db(db)
        backups = tmp_path / "elsewhere"
        today = datetime(2026, 4, 18, tzinfo=UTC)

        snap = ensure_daily_snapshot(db, backup_dir=backups, today=today)

        assert snap is not None
        assert snap.parent == backups

    def test_idempotent_returns_none_when_today_snapshot_exists(self, tmp_path: Path) -> None:
        db = tmp_path / "abuelos.db"
        _make_db(db, "first")
        today = datetime(2026, 4, 18, tzinfo=UTC)

        first = ensure_daily_snapshot(db, today=today)
        # Mutate the source so we can prove the snapshot was NOT regenerated.
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("UPDATE t SET v = 'second'")
            conn.commit()
        finally:
            conn.close()

        second = ensure_daily_snapshot(db, today=today)

        assert first is not None
        assert second is None
        # The existing snapshot still has the original value.
        assert _read_value(first) == "first"

    def test_prunes_snapshots_older_than_retention(self, tmp_path: Path) -> None:
        db = tmp_path / "abuelos.db"
        _make_db(db)
        backups = tmp_path / "backups"
        backups.mkdir()
        today = datetime(2026, 4, 18, tzinfo=UTC)

        # Lay down old snapshot files (no real DB content needed for the
        # prune logic — the helper only inspects filenames).
        for delta in (1, 5, 8, 30):
            old_date = today - timedelta(days=delta)
            old_path = backups / f"abuelos-{old_date.strftime('%Y-%m-%d')}.db"
            old_path.touch()

        ensure_daily_snapshot(db, retention_days=7, today=today)

        remaining = sorted(p.name for p in backups.glob("*.db"))
        # Cutoff = today - 7 days = 2026-04-11. Snapshots strictly older
        # than that are pruned: deltas 8 (→ 04-10) and 30 (→ 03-19) go.
        # Survivors: 1-day-old (04-17), 5-day-old (04-13), today's new (04-18).
        assert remaining == [
            "abuelos-2026-04-13.db",
            "abuelos-2026-04-17.db",
            "abuelos-2026-04-18.db",
        ]

    def test_prune_runs_even_when_no_new_snapshot_created(self, tmp_path: Path) -> None:
        # Today's snapshot already exists, but old ones should still be
        # pruned on the call.
        db = tmp_path / "abuelos.db"
        _make_db(db)
        backups = tmp_path / "backups"
        backups.mkdir()
        today = datetime(2026, 4, 18, tzinfo=UTC)

        # Pre-existing today's snapshot.
        (backups / "abuelos-2026-04-18.db").touch()
        # An old snapshot that should be pruned.
        (backups / "abuelos-2026-04-01.db").touch()

        result = ensure_daily_snapshot(db, retention_days=7, today=today)

        assert result is None  # today's already there
        assert not (backups / "abuelos-2026-04-01.db").exists()

    def test_prune_ignores_files_that_dont_match_naming(self, tmp_path: Path) -> None:
        db = tmp_path / "abuelos.db"
        _make_db(db)
        backups = tmp_path / "backups"
        backups.mkdir()
        today = datetime(2026, 4, 18, tzinfo=UTC)

        # File matches the glob `abuelos-*.db` but the date suffix is
        # malformed — must be left alone, not crashed on.
        weird = backups / "abuelos-not-a-date.db"
        weird.touch()

        ensure_daily_snapshot(db, retention_days=7, today=today)

        assert weird.exists()

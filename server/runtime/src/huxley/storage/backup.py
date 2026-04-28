"""Daily SQLite snapshots with retention pruning.

Idempotent: if today's snapshot already exists, no-op. Designed to be
called from `Application.start()` so the daily-driver path (launchd
auto-start at login) gets backups for free without a separate cron.

Uses SQLite's online backup API (via stdlib `sqlite3.Connection.backup`)
which is safe to run while another process holds the DB open — important
because Huxley's main process keeps the connection open for the whole
session.

See docs/triage.md T2.1.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger()

DEFAULT_RETENTION_DAYS = 7


def ensure_daily_snapshot(
    db_path: Path,
    *,
    backup_dir: Path | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    today: datetime | None = None,
) -> Path | None:
    """Create today's snapshot if missing, prune snapshots beyond retention.

    Args:
        db_path: source SQLite database to snapshot.
        backup_dir: where to write snapshots. Defaults to `db_path.parent / "backups"`.
        retention_days: snapshots older than this many days are deleted.
        today: clock injection point for tests; defaults to now in UTC.

    Returns:
        The snapshot path if a new snapshot was created, `None` if today's
        already exists or the source DB does not exist yet.
    """
    if not db_path.exists():
        return None

    if backup_dir is None:
        backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    if today is None:
        today = datetime.now(UTC)
    today_str = today.strftime("%Y-%m-%d")
    snapshot_path = backup_dir / f"{db_path.stem}-{today_str}.db"

    if snapshot_path.exists():
        _prune_old_snapshots(db_path, backup_dir, retention_days, today)
        return None

    # SQLite online backup API. Safe to run while the main app process holds
    # the source DB open — the API uses SQLite's locking, not file copy.
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(snapshot_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    pruned = _prune_old_snapshots(db_path, backup_dir, retention_days, today)
    logger.info(
        "storage_snapshot_created",
        path=str(snapshot_path),
        pruned=pruned,
        retention_days=retention_days,
    )
    return snapshot_path


def _prune_old_snapshots(
    db_path: Path,
    backup_dir: Path,
    retention_days: int,
    today: datetime,
) -> int:
    """Delete snapshots older than `retention_days`. Returns count pruned.

    Filename convention: `<db_stem>-YYYY-MM-DD.db`. Date is the last 10
    characters before the `.db` extension — robust against db_stem
    containing hyphens.
    """
    cutoff = today - timedelta(days=retention_days)
    pruned = 0
    for snap in backup_dir.glob(f"{db_path.stem}-*.db"):
        date_str = snap.stem[-10:]
        try:
            snap_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            # Doesn't match our naming; leave it alone.
            continue
        if snap_date < cutoff:
            snap.unlink()
            pruned += 1
    return pruned

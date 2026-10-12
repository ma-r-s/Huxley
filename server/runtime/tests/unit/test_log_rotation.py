"""Tests for the per-run log archive.

Archives are the deployment's history, so nothing is deleted unless an
age limit is set. See docs/triage.md T1.43 (ten restarts in one day used
to erase the day before).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING

from huxley.logging import _rotate_per_run

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 10, 12, 12, 0, 0)


def _archive(logs: Path, stamp: str) -> Path:
    path = logs / f"huxley_{stamp}.log"
    path.write_text("{}\n")
    return path


def _names(logs: Path) -> set[str]:
    return {p.name for p in logs.iterdir()}


class TestKeepEverythingByDefault:
    def test_more_than_ten_archives_survive_a_restart(self, tmp_path: Path) -> None:
        # The old count limit (10) deleted the oldest here.
        stamps = [f"20261012T{h:02d}0000" for h in range(12)]
        for stamp in stamps:
            _archive(tmp_path, stamp)
        live = tmp_path / "huxley.log"
        live.write_text("{}\n")

        _rotate_per_run(live, now=NOW)

        archives = sorted(tmp_path.glob("huxley_*.log"))
        assert len(archives) == 13
        assert not live.exists()

    def test_old_archives_survive_without_a_limit(self, tmp_path: Path) -> None:
        _archive(tmp_path, "20250101T000000")

        _rotate_per_run(tmp_path / "huxley.log", now=NOW)

        assert "huxley_20250101T000000.log" in _names(tmp_path)


class TestAgeLimit:
    def test_deletes_only_archives_older_than_the_limit(self, tmp_path: Path) -> None:
        _archive(tmp_path, "20261001T120000")  # 11 days: goes
        _archive(tmp_path, "20261002T130000")  # just inside 10 days: stays
        _archive(tmp_path, "20261011T080000")  # yesterday: stays

        _rotate_per_run(tmp_path / "huxley.log", keep_days=10, now=NOW)

        assert _names(tmp_path) == {
            "huxley_20261002T130000.log",
            "huxley_20261011T080000.log",
        }

    def test_age_comes_from_the_name_not_the_mtime(self, tmp_path: Path) -> None:
        # A restored backup has a fresh mtime; its name still says when it ran.
        old = _archive(tmp_path, "20260101T000000")
        fresh = NOW.timestamp()
        os.utime(old, (fresh, fresh))

        _rotate_per_run(tmp_path / "huxley.log", keep_days=30, now=NOW)

        assert not old.exists()

    def test_same_second_suffix_is_aged_too(self, tmp_path: Path) -> None:
        _archive(tmp_path, "20260101T000000_1")

        _rotate_per_run(tmp_path / "huxley.log", keep_days=30, now=NOW)

        assert _names(tmp_path) == set()

    def test_files_that_are_not_archives_are_left_alone(self, tmp_path: Path) -> None:
        (tmp_path / "huxley_notes.log").write_text("mine\n")
        (tmp_path / "huxley-8765.lock").write_text("")

        _rotate_per_run(tmp_path / "huxley.log", keep_days=1, now=NOW)

        assert _names(tmp_path) == {"huxley_notes.log", "huxley-8765.lock"}

    def test_the_run_just_archived_is_kept(self, tmp_path: Path) -> None:
        live = tmp_path / "huxley.log"
        live.write_text("{}\n")
        written = datetime(2026, 10, 12, 11, 59, 0).timestamp()
        os.utime(live, (written, written))

        _rotate_per_run(live, keep_days=1, now=NOW)

        assert _names(tmp_path) == {"huxley_20261012T115900.log"}

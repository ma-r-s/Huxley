"""Reading the server's log files: damage is skipped, never fatal."""

from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from huxley_watch.events import LogReader, make_events, parse_line
from huxley_watch.facts import half_hours
from huxley_watch.wlog import WatchLog

if TYPE_CHECKING:
    import pytest

from .logs import NOW, TZ, ago, alive, connected, device, fw, line, starting, underrun


def reader(tmp_path: Path) -> LogReader:
    return LogReader(tmp_path / "logs", timedelta(days=40), WatchLog(None))


def write(path: Path, lines: list[str], tail: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(x + "\n" for x in lines) + tail)


def test_parse_keeps_relevant_events_and_drops_the_rest() -> None:
    assert parse_line(starting(NOW)) is not None
    assert parse_line(line("server.tx.audio_stats", NOW, window_ms=1134)) is None
    assert parse_line(fw(NOW, "w1", "mic.tx.stats enq=3 enq_drop=0")) is None
    assert parse_line(underrun(NOW, "w1", 60)) is not None


def test_parse_never_raises_on_damage() -> None:
    for bad in (
        "",
        "{",
        "not json",
        "[1, 2]",
        '{"event": "huxley_starting"}',
        '{"event": 3}',
        '{"timestamp": "x"}',
    ):
        assert parse_line(bad) is None


def test_traceback_keeps_only_its_last_line() -> None:
    e = parse_line(
        line(
            "connection_failed",
            NOW,
            level="error",
            exception="Traceback (most recent call last):\n  File x\nTimeoutError: timed out",
        )
    )
    assert e is not None
    assert e.get("exception") == "TimeoutError: timed out"


def test_malformed_and_truncated_lines_are_skipped_not_fatal(tmp_path: Path) -> None:
    live = tmp_path / "logs" / "huxley.log"
    write(
        live,
        [starting(ago(hours=1)), "{broken json", connected(ago(minutes=30), "w1")],
        tail='{"event": "client.dev',
    )
    r = reader(tmp_path)
    result = r.read(NOW)
    assert [e.name for e in result.events] == ["huxley_starting", "client_connected"]
    assert result.skipped == 1  # the broken line; the unfinished one waits
    # The writer finishes the line: it is read on the next cycle.
    with live.open("a") as f:
        f.write(
            'ice", "shell": "firmware", "conn_id": "w1", "timestamp": "2026-10-12T19:31:00Z"}\n'
        )
    result = r.read(NOW)
    assert [e.name for e in result.events][-1] == "client.device"
    assert result.skipped == 1


def test_archives_live_and_keep_copies_count_each_line_once(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    archived = [starting(ago(hours=5)), connected(ago(hours=5), "w1")]
    write(logs / "huxley_20261012T100000.log", archived)
    write(logs / "keep" / "real_use.log", archived)  # a hand-kept copy
    write(logs / "huxley.log", [starting(ago(hours=1))])
    result = reader(tmp_path).read(NOW)
    assert [e.name for e in result.events] == [
        "huxley_starting",
        "client_connected",
        "huxley_starting",
    ]
    assert result.files == 3


def test_rotation_starts_the_live_file_over(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    live = logs / "huxley.log"
    write(live, [starting(ago(hours=2))])
    r = reader(tmp_path)
    assert len(r.read(NOW).events) == 1
    live.rename(logs / "huxley_20261012T130000.log")  # the server restarts
    write(live, [starting(ago(minutes=1))])
    assert len(r.read(NOW).events) == 2


def test_old_archives_and_old_firmware_lines_are_not_kept(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    write(logs / "huxley_20250101T000000.log", [starting(NOW - timedelta(days=500))])
    write(
        logs / "huxley.log",
        [alive(ago(hours=30), "w1", 10), alive(ago(hours=1), "w1", 20), starting(ago(days=3))],
    )
    result = reader(tmp_path).read(NOW)
    assert result.files == 1
    names = sorted(e.name for e in result.events)
    assert names == ["client.huxley.firmware_log", "huxley_starting"]


def test_missing_logs_dir_is_logged_once(tmp_path: Path) -> None:
    log = WatchLog(None)
    r = LogReader(tmp_path / "nope", timedelta(days=40), log)
    assert r.read(NOW).events == []
    assert r.read(NOW).events == []
    assert len(log.events("watch.logs_missing")) == 1


def test_half_hours_is_dev_py_links_arithmetic() -> None:
    lines = [
        connected(ago(minutes=50), "w1"),
        device(ago(minutes=50), "w1"),
        alive(ago(minutes=45), "w1", 1000),  # seeds the counter
        alive(ago(minutes=44), "w1", 1500),  # 500 chunks = 10 s
        underrun(ago(minutes=44), "w1", 780),
        alive(ago(minutes=43), "w1", 200),  # reboot: skipped
        alive(ago(minutes=42), "w1", 450),  # 250 chunks = 5 s
        alive(ago(minutes=10), "w1", 950),  # next half hour: 10 s
    ]
    buckets = half_hours(make_events(lines), ago(minutes=44, seconds=30), TZ)
    assert [
        (b.start.strftime("%H:%M"), round(b.played_s, 2), b.gaps, b.gap_s) for b in buckets
    ] == [
        ("14:00", 15.0, 1, 0.78),
        ("14:30", 10.0, 0, 0.0),
    ]


def test_reads_line_by_line_never_the_whole_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critic 3: the reader slurped the rest of the file (about 3x its size
    in memory); a production log can be hundreds of MB."""
    live = tmp_path / "logs" / "huxley.log"
    write(live, [starting(ago(hours=1)), connected(ago(minutes=30), "w1")])
    real_open = Path.open

    class NoSlurp(io.BufferedReader):
        def read(self, size: int | None = -1) -> bytes:
            if size is None or size < 0:
                raise AssertionError("read the whole file")
            return super().read(size)

    def guarded(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if self == live and "b" in mode:
            return NoSlurp(io.FileIO(str(self), "r"))
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    assert len(reader(tmp_path).read(NOW).events) == 2


def test_recorder_lines_are_ignored_not_fatal(tmp_path: Path) -> None:
    seg = line(
        "recorder.segment",
        ago(minutes=5),
        source="model",
        label=None,
        start="2026-10-12T19:55:00Z",
        end="2026-10-12T19:55:03Z",
        duration_ms=3000,
        path="recordings/2026/10/12/195500-ab12cd34-003-model.ogg",
        conn_id="w1",
    )
    write(tmp_path / "logs" / "huxley.log", [starting(ago(hours=1)), seg])
    result = reader(tmp_path).read(NOW)
    assert [e.name for e in result.events] == ["huxley_starting"]
    assert result.skipped == 0

"""Spans of activity, connection stretches and bench windows."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from huxley_watch.activity import presses, segments
from huxley_watch.bench import BenchReader
from huxley_watch.facts import walkie_state
from huxley_watch.wlog import WatchLog

from .logs import (
    NOW,
    ago,
    audiobook_started,
    bench,
    bench_line,
    claim_ended,
    claim_started,
    connected,
    cost,
    device,
    disconnected,
    events,
    flat,
    play_started,
    press,
    ptt_stop,
    radio_started,
    speaking,
    starting,
    stream_ended,
    stream_started,
)

if TYPE_CHECKING:
    from pathlib import Path


def kinds(lines: list[str]) -> list[tuple[str, str]]:
    return [(s.kind, s.label) for s in segments(events(lines), NOW)]


def test_stream_kind_comes_from_the_skill_line_logged_after_its_start() -> None:
    # Real order: server.tx.stream_started, then the skill's line ms later.
    lines = [
        stream_started(ago(minutes=50), "s1", "Joropo"),
        play_started(ago(minutes=50) + timedelta(milliseconds=16), "spotify:track:05fS"),
        stream_ended(ago(minutes=45), "s1"),
        stream_started(ago(minutes=40), "s2", "Sherlock Holmes"),
        play_started(ago(minutes=40) + timedelta(seconds=1.2), "spotify:show:5YDI"),
        stream_ended(ago(minutes=30), "s2"),
        stream_started(ago(minutes=29), "s3", "Caracol Radio"),
        radio_started(ago(minutes=29)),
        stream_ended(ago(minutes=20), "s3"),
        stream_started(ago(minutes=19), "s4", "100 Anos de soledad"),
        audiobook_started(ago(minutes=19)),
        stream_ended(ago(minutes=10), "s4"),
    ]
    assert kinds(lines) == [
        ("music", "Joropo"),
        ("book", "Sherlock Holmes"),
        ("radio", "Caracol Radio"),
        ("book", "100 Anos de soledad"),
    ]


def test_streams_pair_by_id_even_interleaved() -> None:
    lines = [
        stream_started(ago(minutes=50), "a", "A"),
        play_started(ago(minutes=50), "spotify:track:x"),
        stream_started(ago(minutes=40), "b", "B"),
        radio_started(ago(minutes=40)),
        stream_ended(ago(minutes=35), "a"),
        stream_ended(ago(minutes=30), "b"),
    ]
    segs = segments(events(lines), NOW)
    by_label = {s.label: s for s in segs}
    assert by_label["A"].kind == "music"
    assert by_label["A"].end == ago(minutes=35)
    assert by_label["B"].kind == "radio"
    assert by_label["B"].end == ago(minutes=30)


def test_talk_answer_and_calls() -> None:
    lines = [
        press(ago(minutes=30)),
        ptt_stop(ago(minutes=30) + timedelta(seconds=4)),
        speaking(ago(minutes=30) + timedelta(seconds=5), True),
        speaking(ago(minutes=30) + timedelta(seconds=9), False),
        claim_started(ago(minutes=20), "mario"),
        claim_ended(ago(minutes=17)),
    ]
    segs = segments(events(lines), NOW)
    assert [(s.kind, round(s.seconds)) for s in segs] == [
        ("talk", 4),
        ("answer", 4),
        ("call", 180),
    ]
    assert segs[-1].label == "mario"


def test_something_playing_when_the_server_restarts_ends_at_its_last_line() -> None:
    lines = [
        stream_started(ago(minutes=50), "s1", "Joropo"),
        play_started(ago(minutes=50), "spotify:track:x"),
        speaking(ago(minutes=40), True),
        cost(ago(minutes=35), 0.01, 0.5),  # the old process's last line
        starting(ago(minutes=10)),
    ]
    segs = segments(events(lines), NOW)
    assert {s.kind: s.end for s in segs} == {"music": ago(minutes=35), "answer": ago(minutes=35)}


def test_bench_windows_cut_activity_and_presses() -> None:
    lines = [
        press(ago(minutes=50)),
        ptt_stop(ago(minutes=50) + timedelta(seconds=3)),
        press(ago(minutes=20)),
        ptt_stop(ago(minutes=20) + timedelta(seconds=3)),
        stream_started(ago(minutes=40), "s1", "Joropo"),
        stream_ended(ago(minutes=10), "s1"),
    ]
    w = bench((ago(minutes=25), ago(minutes=15), "bench.py --steps turn"))
    segs = segments(events(lines), NOW, w)
    assert [s.kind for s in segs if s.kind == "talk"] == ["talk"]  # the bench press is gone
    music = [(s.start, s.end) for s in segs if s.kind == "music"]
    assert music == [(ago(minutes=40), ago(minutes=25)), (ago(minutes=15), ago(minutes=10))]
    assert presses(events(lines), w) == [ago(minutes=50)]
    # A link test keeps his activity: only bench windows cut it.
    test = bench((ago(minutes=25), ago(minutes=15), "link test: router off"))
    assert len(presses(events(lines), test)) == 2


def test_overlapping_connections_merge_into_one_stretch() -> None:
    """The walkie reconnecting before the old socket closed: two connections
    overlap. extract.py summed them; the strip must show their union."""
    lines = [
        connected(ago(hours=2), "w1"),
        device(ago(hours=2), "w1"),
        connected(ago(hours=1), "w2"),  # before w1 closes
        device(ago(hours=1), "w2"),
        disconnected(ago(minutes=50), "w1"),
        disconnected(ago(minutes=30), "w2"),
        connected(ago(minutes=29), "w3"),  # a 1 min blip: same stretch
        device(ago(minutes=29), "w3"),
        disconnected(ago(minutes=20), "w3"),
    ]
    w = walkie_state(events(lines), NOW)
    assert w.spans == ((ago(hours=2), ago(minutes=20)),)
    total = sum((e - s).total_seconds() for s, e in w.spans)
    assert total == 100 * 60  # not 120 + 30 + 9 summed
    assert w.online is False
    assert w.since == ago(minutes=20)


# --- bench.jsonl ---------------------------------------------------------------


def test_bench_file_groups_start_and_end_lines(tmp_path: Path) -> None:
    path = tmp_path / "bench.jsonl"
    path.write_text(
        "\n".join(
            [
                bench_line(ago(minutes=50), None, "bench.py --wifi --steps turn"),
                bench_line(ago(minutes=50), ago(minutes=45), "bench.py --wifi --steps turn"),
                bench_line(ago(minutes=30), ago(minutes=28), "dev.py cmd talk"),
                bench_line(ago(minutes=20), None, "link test: router off"),  # never ended
            ]
        )
        + "\n"
    )
    b = BenchReader(path, WatchLog(None)).read()
    got = [(w.start, w.end, w.kind) for w in b.windows]
    assert got == [
        (ago(minutes=50), ago(minutes=45), "bench"),
        (ago(minutes=30), ago(minutes=28), "bench"),
        (ago(minutes=20), ago(minutes=20) + timedelta(hours=2), "test"),  # at most 2 h
    ]
    assert b.in_bench(ago(minutes=47))
    assert not b.in_bench(ago(minutes=10))  # a test window keeps activity
    assert b.suppressed(ago(minutes=10))


def test_bench_file_bad_lines_are_logged_once_and_a_half_line_waits(tmp_path: Path) -> None:
    path = tmp_path / "bench.jsonl"
    path.write_text(
        "not json\n"
        + bench_line(ago(minutes=30), ago(minutes=28), "dev.py cmd say")
        + '\n{"start": "2026'
    )
    log = WatchLog(None)
    reader = BenchReader(path, log)
    assert len(reader.read().windows) == 1
    assert len(reader.read().windows) == 1
    skipped = log.events("watch.bench_line_skipped")
    assert len(skipped) == 1  # once, and not for the half-written last line
    assert skipped[0]["line"] == "not json"


def test_no_bench_file_excludes_nothing(tmp_path: Path) -> None:
    b = BenchReader(tmp_path / "nope.jsonl", WatchLog(None)).read()
    assert b.windows == ()
    assert flat() == []

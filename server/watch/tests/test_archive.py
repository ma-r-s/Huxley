"""The archive's rows: transcripts, tool calls, recordings, streams and
calls, each at its own time, merged in time order, nothing paired. Log
shapes from the real logs of 2026-10-12."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from huxley_watch import archive

from .logs import (
    NOW,
    ago,
    answer,
    bench,
    by_time,
    claim_ended,
    claim_started,
    day_events,
    flat,
    line,
    play_started,
    press,
    ptt_stop,
    radio_started,
    segment,
    starting,
    stream_ended,
    stream_started,
    tool_call,
    transcript,
)

if TYPE_CHECKING:
    from huxley_watch.bench import Bench

T = ago(hours=1)  # 14:00 local
SPANS = [(ago(hours=3), NOW)]  # the walkie connected all along
CONNS = frozenset({"w1"})


def at(seconds: float) -> datetime:
    return T + timedelta(seconds=seconds)


def rows_of(
    lines: list[str],
    files: frozenset[str] = frozenset(),
    spans: list[tuple[datetime, datetime]] = SPANS,
    windows: Bench | None = None,
) -> list[archive.Row]:
    return archive.rows(day_events(lines), NOW, CONNS, spans, files, windows)


def brief(found: list[archive.Row]) -> list[tuple[float, str, str, str]]:
    return [(round((r.ts - T).total_seconds(), 3), r.kind, r.who, r.text) for r in found]


def test_one_list_in_time_order_with_nothing_paired() -> None:
    """Real, 18:31:55Z: Huxley's transcript arrived before his. Each row
    keeps its own time; a recording is not attached to any text."""
    lines = by_time(
        flat(
            press(at(0), turn="t1"),
            ptt_stop(at(1.434), turn="t1"),
            segment("mic", at(0.05), at(1.43), path="recordings/2026/10/12/183153-w1-001-mic.ogg"),
            answer(at(2.177), at(2.286), turn="t1"),
            segment(
                "model",
                at(2.18),
                at(2.28),
                path="recordings/2026/10/12/183155-w1-002-model.ogg",
                ms=1800,
            ),
            transcript(at(2.328), "assistant", "¿Pediste que tú lo pongas?"),
            transcript(at(2.803), "user", "¿Qué hora es?"),
            tool_call(at(2.9), "get_current_time"),
        )
    )
    files = frozenset({"2026/10/12/183153-w1-001-mic.ogg", "2026/10/12/183155-w1-002-model.ogg"})
    got = rows_of(lines, files)
    assert brief(got) == [
        (0.05, "rec", "mic", ""),
        (2.18, "rec", "model", ""),
        (2.328, "say", "huxley", "¿Pediste que tú lo pongas?"),
        (2.803, "say", "him", "¿Qué hora es?"),
        (2.9, "tool", "", "get_current_time"),
    ]
    assert (got[0].audio, got[0].ms) == ("2026/10/12/183153-w1-001-mic.ogg", 1500)
    assert got[1].ms == 1800


def test_a_deleted_or_unfinished_file_is_no_row() -> None:
    lines = [
        segment("mic", at(0), at(1), path="recordings/2026/10/12/gone.ogg"),
        segment("mic", at(5), at(6), path=None),  # timeline only
    ]
    assert rows_of(lines, frozenset()) == []


def test_another_clients_recording_and_words_are_not_his() -> None:
    lines = [
        segment("mic", at(0), at(1), conn="twin", path="recordings/2026/10/12/twin.ogg"),
        transcript(at(2), "user", "desde el teléfono"),
    ]
    away = [(ago(hours=5), ago(hours=4))]  # the walkie was elsewhere
    assert rows_of(lines, frozenset({"2026/10/12/twin.ogg"}), spans=away) == []


def test_tool_arguments_are_kept_short() -> None:
    assert archive.short_args({"query": "Adele", "kind": "artist"}) == "query: Adele, kind: artist"
    assert archive.short_args({}) == ""
    long = archive.short_args({"query": "x" * 200})
    assert len(long) == archive.ARGS_MAX and long.endswith("...")
    real = line(
        "session.rx.tool_call",
        at(0),
        name="play_music",
        call_id="call_1eH3j8CgrUnZfIbg",
        args={"query": "Adele", "kind": "artist"},
    )
    (row,) = rows_of([real])
    assert (row.kind, row.text, row.detail) == ("tool", "play_music", "query: Adele, kind: artist")


def test_a_calls_two_legs_sit_at_the_calls_time() -> None:
    lines = by_time(
        flat(
            claim_started(at(0), "mario"),
            segment(
                "mic",
                at(0.1),
                at(26.9),
                path="recordings/2026/10/12/4-mic-mario.ogg",
                claim_id="claim:active",
                label="mario",
                ms=26800,
            ),
            segment(
                "claim",
                at(0.05),
                at(26.95),
                path="recordings/2026/10/12/5-claim-mario.ogg",
                claim_id="claim:active",
                label="mario",
                ms=26900,
            ),
            claim_ended(at(27)),
        )
    )
    files = frozenset({"2026/10/12/4-mic-mario.ogg", "2026/10/12/5-claim-mario.ogg"})
    got = rows_of(lines, files)
    assert brief(got) == [
        (0, "call", "", "mario"),
        (0.05, "rec", "call", "mario"),  # the other side
        (0.1, "rec", "call", "Him"),  # his side
    ]
    assert got[0].end == at(27)


def test_streams_have_title_start_and_end() -> None:
    lines = by_time(
        flat(
            stream_started(at(0), "s1", "Adele"),
            play_started(at(3), "spotify:artist:x"),
            stream_ended(at(60), "s1"),
            stream_started(at(100), "s2", "Caracol Radio"),
            radio_started(at(100.5)),
            stream_ended(at(200), "s2"),  # a call parked it...
            stream_started(at(231), "s2", "Caracol Radio"),  # ...and it came back: two rows
            stream_ended(at(400), "s2", reason="natural"),
            stream_started(at(500), "s3", "Cien años de soledad"),
            play_started(at(501), "spotify:show:abc"),
            starting(at(900)),  # the server restarted with it playing
            stream_started(at(1000), "s4", "Sade"),
            play_started(at(1001), "spotify:artist:w"),
        )
    )
    got = [r for r in rows_of(lines) if r.kind == "stream"]
    assert [(r.who, r.text, (r.ts - T).total_seconds()) for r in got] == [
        ("music", "Adele", 0),
        ("radio", "Caracol Radio", 100),
        ("radio", "Caracol Radio", 231),
        ("book", "Cien años de soledad", 500),
        ("music", "Sade", 1000),
    ]
    assert [r.end for r in got] == [at(60), at(200), at(400), at(501), NOW]


def test_rows_in_a_bench_window_are_flagged() -> None:
    w = bench((at(90), at(110), "bench.py --steps turn"))
    lines = [transcript(at(10), "user", "suya"), transcript(at(100), "user", "del banco")]
    assert [r.bench for r in rows_of(lines, windows=w)] == [False, True]


def test_a_link_test_window_is_not_bench() -> None:
    """A planned outage hides nothing: what he said during it is his."""
    w = bench((at(90), at(110), "link test: server restart"))
    assert [r.bench for r in rows_of([transcript(at(100), "user", "hola")], windows=w)] == [False]

"""The archive's days: only that day's files are read, what was open at
midnight carries over, the picker's bounds, the day's list (recordings,
calls, streams, bench rows folded, gaps marked) with no sentences
outside the transcripts."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from huxley_watch import daylist
from huxley_watch.config import Config
from huxley_watch.daylog import DayReader, open_at
from huxley_watch.dayview import SERVED, Archive, covered_days, neighbours
from huxley_watch.wlog import WatchLog

from .logs import (
    alive,
    answer,
    by_time,
    call_placed,
    claim_ended,
    claim_started,
    connected,
    device,
    flat,
    line,
    play_started,
    press,
    ptt_stop,
    segment,
    starting,
    stream_ended,
    stream_started,
    tool_call,
    transcript,
    walkie_session,
)

if TYPE_CHECKING:
    from pathlib import Path

TZ = ZoneInfo("America/Bogota")  # UTC-5


def utc(day: int, hour: int, minute: int = 0, second: float = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC) + timedelta(seconds=second)


NOW = utc(12, 20)  # Mon 12 15:00 local


def write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(x + "\n" for x in lines))


def cfg_for(tmp_path: Path) -> Config:
    return Config(
        logs_dir=tmp_path / "logs",
        persona_dir=tmp_path / "persona",
        db_path=tmp_path / "persona" / "data" / "abuelos.db",
        state_dir=tmp_path / "state",
        timezone=TZ,
        recordings_dir=tmp_path / "persona" / "data" / "recordings",
    )


def logs(tmp_path: Path) -> None:
    """Three runs: Thu 08 (alone), Sun 11 evening into Mon 12 (across
    midnight), and Mon 12 afternoon (the live file). Nothing on 09-10."""
    write(
        tmp_path / "logs" / "huxley_20261008T150000.log",
        flat(starting(utc(8, 15)), walkie_session(utc(8, 15), utc(8, 15, 30), "w0")),
    )
    night = flat(
        starting(utc(12, 3, 55)),
        walkie_session(utc(12, 4), utc(12, 6), "w1"),  # Sun 23:00 to Mon 01:00
        stream_started(utc(12, 4, 30), "s1", "Cien años de soledad"),  # Sun 23:30
        play_started(utc(12, 4, 30, 1), "spotify:show:abc"),
        press(utc(12, 5, 10), turn="t1"),  # Mon 00:10, over the book
        ptt_stop(utc(12, 5, 10, 2), turn="t1"),
        answer(utc(12, 5, 10, 3), utc(12, 5, 10, 3.3), turn="t1"),
        transcript(utc(12, 5, 10, 3.35), "assistant", "Son las doce y diez."),
        tool_call(utc(12, 5, 10, 3.36), "get_current_time"),
        transcript(utc(12, 5, 10, 3.5), "user", "¿Qué hora es?"),
        stream_ended(utc(12, 5, 30), "s1", reason="natural"),  # Mon 00:30
    )
    write(tmp_path / "logs" / "huxley_20261012T010000.log", by_time(night))
    live = flat(
        starting(utc(12, 18)),
        walkie_session(utc(12, 18), utc(12, 19, 30), "w2"),  # Mon 13:00 to 14:30
        call_placed(utc(12, 18, 31), "mario"),
        claim_started(utc(12, 18, 31, 0.01), "mario"),
        segment(
            "mic",
            utc(12, 18, 31, 0.1),
            utc(12, 18, 31, 26.9),
            conn="w2",
            path="recordings/2026/10/12/183100-w2-001-mic-mario.ogg",
            claim_id="claim:active",
            label="mario",
            ms=26800,
        ),
        segment(
            "claim",
            utc(12, 18, 31, 0.05),
            utc(12, 18, 31, 26.95),
            conn="w2",
            path="recordings/2026/10/12/183100-w2-002-claim-mario.ogg",
            claim_id="claim:active",
            label="mario",
            ms=26900,
        ),
        claim_ended(utc(12, 18, 31, 27)),
        press(utc(12, 19), conn="w2", turn="t9"),
        ptt_stop(utc(12, 19, 0, 2), conn="w2", turn="t9"),
        segment(
            "mic",
            utc(12, 19, 0, 0.1),
            utc(12, 19, 0, 1.9),
            conn="w2",
            path="recordings/2026/10/12/190000-w2-003-mic.ogg",
        ),
        transcript(utc(12, 19, 0, 2.5), "user", "Pon la radio."),
    )
    write(tmp_path / "logs" / "huxley.log", by_time(live))
    rec = tmp_path / "persona" / "data" / "recordings" / "2026" / "10" / "12"
    rec.mkdir(parents=True)
    for name in (
        "183100-w2-001-mic-mario.ogg",
        "183100-w2-002-claim-mario.ogg",
        "190000-w2-003-mic.ogg",
    ):
        (rec / name).write_bytes(b"OggS" + bytes(60))


def day(arch: Archive, cfg: Config, which: str) -> dict[str, Any] | None:
    """The whole day (rows and strip data); what's served is checked below."""
    return arch.view(cfg, which, NOW)


def test_the_served_day_is_the_markup_and_the_pickers_fields(tmp_path: Path) -> None:
    logs(tmp_path)
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    body = arch.day(cfg, "2026-10-12", NOW)
    assert body is not None
    served = json.loads(body)
    assert sorted(served) == sorted(SERVED)
    whole = arch.view(cfg, "2026-10-12", NOW)
    assert whole is not None
    assert (
        served["list_html"] == whole["list_html"] and served["strip_html"] == whole["strip_html"]
    )


def test_the_chat_is_the_day_as_turns_and_events(tmp_path: Path) -> None:
    """/chat/<day>.json from real-shaped logs: the call with both sides'
    recordings, his press with its words, the day's names from the server."""
    logs(tmp_path)
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    body = arch.chat(cfg, "2026-10-12", NOW)
    assert body is not None
    got = json.loads(body)
    assert (got["date"], got["today"], got["is_today"]) == ("2026-10-12", "2026-10-12", True)
    assert got["prev"] == "2026-10-11" and got["prev_label"] == "Sun 11 Oct"
    call = next(i for i in got["items"] if i.get("kind") == "call")
    assert call["title"] == "Mario" and len(call["clips"]) == 2 and call["live"] is False
    last = [i for i in got["items"] if i["type"] == "turn"][-1]
    assert last["him"]["text"] == "Pon la radio." and len(last["him"]["clips"]) == 1
    assert arch.chat(cfg, "2026-10-30", NOW) is None


def test_a_stream_still_playing_is_live(tmp_path: Path) -> None:
    write(
        tmp_path / "logs" / "huxley.log",
        by_time(
            flat(
                starting(utc(12, 18)),
                walkie_session(utc(12, 18), NOW, "w2"),
                stream_started(utc(12, 19, 50), "s9", "Caracol Radio"),
            )
        ),
    )
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    body = arch.chat(cfg, "2026-10-12", NOW)
    assert body is not None
    (ev,) = [i for i in json.loads(body)["items"] if i["type"] == "event"]
    assert (ev["title"], ev["live"], ev["end"]) == ("Caracol Radio", True, None)


def test_a_day_reads_only_the_files_that_reach_it(tmp_path: Path) -> None:
    logs(tmp_path)
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    day(arch, cfg, "2026-10-08")
    assert arch._reader is not None
    assert [p.name for p in arch._reader.opened] == ["huxley_20261008T150000.log"]
    day(arch, cfg, "2026-10-12")
    assert [p.name for p in arch._reader.opened] == ["huxley_20261012T010000.log", "huxley.log"]


def test_what_was_open_at_midnight_carries_over(tmp_path: Path) -> None:
    """The walkie connected and the book started on Sunday evening: Monday
    still shows both, and his 00:10 press as his (its connection's hello
    was before midnight)."""
    logs(tmp_path)
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    mon = day(arch, cfg, "2026-10-12")
    assert mon is not None
    assert mon["strip"]["online"][0][0] == 0.0  # connected at midnight
    first = mon["strip"]["media"][0]
    assert (first["kind"], first["s"], first["e"]) == ("book", 0.0, 30.0)
    assert mon["strip"]["talk"][0] == [10.0, 10.03]  # and the 14:00 press
    said = [(r["kind"], r["who"], r["text"]) for r in mon["rows"]]
    assert said[:4] == [
        ("stream", "book", "Cien años de soledad"),  # still playing at midnight
        ("say", "huxley", "Son las doce y diez."),
        ("tool", "", "get_current_time"),
        ("say", "him", "¿Qué hora es?"),
    ]
    book = mon["rows"][0]
    assert (book["at"], book["end"], book["seconds"]) == ("Sun 23:30:00", "00:30", 3600)
    sun = day(arch, cfg, "2026-10-11")
    assert sun is not None
    assert [(m["s"], m["e"]) for m in sun["strip"]["media"]] == [(1410.0, 1440.0)]
    assert [(r["kind"], r["end"]) for r in sun["rows"] if r["kind"] == "stream"] == [
        ("stream", "Mon 00:30")
    ]
    assert sun["strip"]["now_min"] is None and mon["strip"]["now_min"] == 900.0


def test_the_picker_steps_over_days_without_logs_and_stops_at_the_ends(tmp_path: Path) -> None:
    logs(tmp_path)
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    assert arch.bounds(cfg, NOW) == {
        "today": "2026-10-12",
        "first": "2026-10-08",
        "prev": "2026-10-11",
    }
    mon, sun, thu = (day(arch, cfg, d) for d in ("2026-10-12", "2026-10-11", "2026-10-08"))
    assert mon and sun and thu
    assert (mon["prev"], mon["next"], mon["is_today"]) == ("2026-10-11", None, True)
    assert (sun["prev"], sun["next"], sun["is_today"]) == ("2026-10-08", "2026-10-12", False)
    assert (thu["prev"], thu["next"]) == (None, "2026-10-11")
    assert day(arch, cfg, "2026-10-07") is None  # before the logs
    assert day(arch, cfg, "2026-10-13") is None  # after today
    assert day(arch, cfg, "2026-10-09") is not None  # inside: an empty day
    for bad in ("2026-13-01", "20261012", "2026-10-1", "../etc", "2026-10-12x"):
        assert arch.day(cfg, bad, NOW) is None


def test_covered_days_and_neighbours() -> None:
    spans = [(utc(8, 15), utc(8, 16)), (utc(12, 4), utc(12, 6))]
    days = covered_days(spans, TZ)
    assert [d.isoformat() for d in days] == ["2026-10-08", "2026-10-11", "2026-10-12"]
    today = days[-1]
    assert neighbours(days[0], days, today) == (None, days[1])
    assert neighbours(today, days, today) == (days[1], None)


def test_a_call_lists_its_row_and_both_legs_with_players(tmp_path: Path) -> None:
    logs(tmp_path)
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    mon = day(arch, cfg, "2026-10-12")
    assert mon is not None
    call = [r for r in mon["rows"] if r["t"] >= 811 and r["t"] < 812]
    assert [(r["kind"], r["who"], r["text"], r["audio"]) for r in call] == [
        ("call", "", "mario", None),
        ("rec", "call", "mario", "2026/10/12/183100-w2-002-claim-mario.ogg"),
        ("rec", "call", "Him", "2026/10/12/183100-w2-001-mic-mario.ogg"),
    ]
    assert call[0]["seconds"] == 27 and call[1]["ms"] == 26900
    html = mon["list_html"]
    assert 'data-src="audio/2026/10/12/183100-w2-001-mic-mario.ogg"' in html
    assert 'data-src="audio/2026/10/12/183100-w2-002-claim-mario.ogg"' in html
    assert html.count('class="track"') == 3  # plain progress bars: the two legs and the press
    # the press after it: a play row at its time, the words at theirs
    later = [(r["kind"], r["who"], r["text"]) for r in mon["rows"] if r["t"] >= 840]
    assert later == [("rec", "mic", ""), ("say", "him", "Pon la radio.")]


def test_a_stream_row_has_title_start_end_and_no_player(tmp_path: Path) -> None:
    logs(tmp_path)
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    mon = day(arch, cfg, "2026-10-12")
    assert mon is not None
    html = daylist.render([mon["rows"][0]])
    assert ">Cien años de soledad<" in html and ">00:30<" in html and ">1.0 h<" in html
    assert ">Sun 23:30:00<" in html
    assert 'class="play"' not in html  # streams aren't recorded


def visible(html: str) -> list[str]:
    """Text a reader sees, the content left out: transcripts (tx), titles and
    names (name), tool arguments (arg)."""
    html = re.sub(r'<span class="(tx|name|arg)">.*?</span>', " ", html, flags=re.S)
    return [t.strip() for t in re.split(r"<[^>]+>", html) if t.strip()]


def test_the_list_has_no_sentences_outside_the_content(tmp_path: Path) -> None:
    logs(tmp_path)
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    mon = day(arch, cfg, "2026-10-12")
    assert mon is not None and len(mon["rows"]) >= 8
    texts = visible(mon["list_html"])
    assert texts
    for text in texts:
        assert len(text.split()) <= 2, text  # times, lengths, tool names: labels and numbers


def row(t: float, kind: str = "say", bench: bool = False, **kw: Any) -> dict[str, Any]:
    base = {
        "t": t,
        "e": t,
        "at": "12:00:00",
        "kind": kind,
        "who": "him",
        "text": "hola",
        "detail": "",
        "audio": None,
        "ms": None,
        "end": None,
        "seconds": None,
        "bench": bench,
    }
    return {**base, **kw}


def test_bench_rows_fold_into_one_and_long_gaps_get_a_marker() -> None:
    html = daylist.render(
        [row(600), row(601, bench=True), row(602, bench=True), row(603), row(640)]
    )
    assert html.count('<details class="bgroup"') == 1
    group = html[html.index("<details") : html.index("</details>")]
    assert group.count('class="li k-say"') == 2 and ">bench<" in group and ">2<" in group
    assert html.count('<div class="gap">') == 1 and ">37 min<" in html  # 603 to 640
    # a long stream is not a gap: rows during it follow on
    long = daylist.render([row(600, "stream", e=700, end="13:40"), row(650)])
    assert '<div class="gap">' not in long


def test_untrusted_text_in_the_list_is_escaped() -> None:
    evil = 'x"><img src=x onerror=alert(1)>'
    html = daylist.render(
        [
            row(1, text=evil),
            row(2, "tool", text=evil, detail=evil, who=""),
            row(3, "rec", who="call", text=evil, audio="2026/10/12/a b.ogg", ms=1500),
            row(4, "stream", who="music", text=evil, end="12:05", seconds=300),
            row(5, "call", who="", text=evil, end="12:06", seconds=60),
        ]
    )
    assert "<img" not in html
    assert 'data-src="audio/2026/10/12/a%20b.ogg"' in html


def test_an_empty_day_is_an_empty_list() -> None:
    assert daylist.render([]) == '<div class="rows"></div>'


def test_a_built_past_day_is_kept_until_its_files_change(tmp_path: Path) -> None:
    logs(tmp_path)
    cfg, log = cfg_for(tmp_path), WatchLog(None)
    arch = Archive(log)
    first = arch.day(cfg, "2026-10-08", NOW)
    assert arch.day(cfg, "2026-10-08", NOW) is first  # the cached bytes
    assert len(log.events("watch.archive_day")) == 1
    old = tmp_path / "logs" / "huxley_20261008T150000.log"
    old.write_text(old.read_text() + transcript(utc(8, 15, 20), "user", "otra") + "\n")
    arch.day(cfg, "2026-10-08", NOW)
    assert len(log.events("watch.archive_day")) == 2
    assert len(arch._built) == 1  # one entry a day, replaced


def test_a_day_still_growing_is_rebuilt_in_its_one_entry(tmp_path: Path) -> None:
    """Critic: yesterday rebuilt as the live file grows must not pile up
    entries that push other days out of the cache."""
    logs(tmp_path)
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    live = tmp_path / "logs" / "huxley.log"
    for n in range(3):
        live.write_text(live.read_text() + transcript(utc(12, 19, 10, n), "user", "más") + "\n")
        arch.day(cfg, "2026-10-11", NOW)
        arch.day(cfg, "2026-10-08", NOW)
    assert sorted(d.isoformat() for d in arch._built) == ["2026-10-08", "2026-10-11"]


def test_asking_for_today_again_reads_only_todays_lines(tmp_path: Path) -> None:
    """The context before midnight is kept per file: the second read starts
    at the day's window. Proof: break the hello before midnight in place
    (same length) after the first read; the press after midnight is still
    his."""
    logs(tmp_path)
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    first = day(arch, cfg, "2026-10-12")
    assert first is not None and first["strip"]["talk"][0] == [10.0, 10.03]
    night = tmp_path / "logs" / "huxley_20261012T010000.log"
    text = night.read_text()
    assert text.count('"shell": "firmware"') == 1
    night.write_text(text.replace('"shell": "firmware"', '"shell": "xxxxware"'))
    again = day(arch, cfg, "2026-10-12")
    assert again is not None and again["strip"]["talk"][0] == [10.0, 10.03]
    fresh = day(Archive(WatchLog(None)), cfg, "2026-10-12")  # a new reader reads it all
    assert fresh is not None and fresh["strip"]["talk"][:1] != [[10.0, 10.03]]


def test_a_call_over_midnight_keeps_its_recordings(tmp_path: Path) -> None:
    """Critic: the recorder logs a leg when it ends, here 50 minutes past
    midnight, still inside the window read after the day."""
    write(
        tmp_path / "logs" / "huxley.log",
        by_time(
            flat(
                starting(utc(12, 4)),
                walkie_session(utc(12, 4, 0, 1), utc(12, 6), "w1"),
                claim_started(utc(12, 4, 40), "mario"),  # Sun 23:40
                claim_ended(utc(12, 5, 30)),  # Mon 00:30
                segment(
                    "claim",
                    utc(12, 4, 40, 0.05),
                    utc(12, 5, 29, 59),
                    path="recordings/2026/10/12/044000-w1-001-claim-mario.ogg",
                    claim_id="claim:active",
                    label="mario",
                    ms=2_999_000,
                ),
            )
        ),
    )
    rec = tmp_path / "persona" / "data" / "recordings" / "2026" / "10" / "12"
    rec.mkdir(parents=True)
    (rec / "044000-w1-001-claim-mario.ogg").write_bytes(b"OggS")
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    sun = day(arch, cfg, "2026-10-11")
    assert sun is not None
    assert [(r["kind"], r["who"], r["at"]) for r in sun["rows"]] == [
        ("call", "", "23:40:00"),
        ("rec", "call", "23:40:00"),
    ]


def test_a_file_rewritten_in_place_is_read_again(tmp_path: Path) -> None:
    """Critic: the runtime opens huxley.log with "w" after a failed
    rotation (same inode). A kept day start past its new end, or from its
    old first line, must not be used."""
    live = tmp_path / "logs" / "huxley.log"
    filler = [line("huxley_ready", utc(11, 19, 0, s)) for s in range(2000)]
    write(live, [*filler, transcript(utc(12, 6), "user", "vieja")])
    reader = DayReader(tmp_path / "logs", WatchLog(None))
    lo = utc(12, 5)  # Mon 00:00 local
    hi = lo + timedelta(days=1)
    assert [e.get("text") for e in reader.read(lo, hi) if e.name == "transcript"] == ["vieja"]
    with live.open("w") as fh:  # the same inode, a fresh run, a smaller file
        fh.write(
            starting(utc(12, 7)) + "\n" + transcript(utc(12, 7, 0, 1), "user", "nueva") + "\n"
        )
    assert [e.get("text") for e in reader.read(lo, hi) if e.name == "transcript"] == ["nueva"]


def test_the_kept_context_stays_small_over_a_long_run(tmp_path: Path) -> None:
    """Critic: a run of weeks in one file must not keep every stream it
    ever played as context. Three days, a song every hour: the day start
    keeps what is open at midnight, a handful of lines, and the strip
    still shows the walkie connected and the song playing."""
    lines = [starting(utc(9, 4, 59)), connected(utc(9, 5), "w1"), device(utc(9, 5, 0, 1), "w1")]
    t = utc(9, 5, 1)
    n = 0
    while t < utc(12, 5):
        lines += [
            stream_started(t, f"s{n}", f"Song {n}"),
            play_started(t + timedelta(seconds=1), "spotify:track:x"),
            stream_ended(t + timedelta(minutes=50), f"s{n}"),
        ]
        t += timedelta(hours=1)
        n += 1
    lines += [
        stream_started(utc(12, 4, 50), "late", "Late"),
        play_started(utc(12, 4, 50, 1), "spotify:track:y"),
    ]
    lines += [alive(utc(12, 4, 45) + timedelta(seconds=5 * k), "w1", 0) for k in range(400)]
    write(tmp_path / "logs" / "huxley.log", by_time(lines))
    cfg, arch = cfg_for(tmp_path), Archive(WatchLog(None))
    mon = arch.view(cfg, "2026-10-12", utc(12, 5, 30))
    assert mon is not None
    assert arch._reader is not None
    kept = [len(s.events) for s in arch._reader._starts.values()]
    assert kept and max(kept) <= 6, (
        kept
    )  # the start, connect, hello, the open song and its kind, the last line
    assert mon["strip"]["online"][0][0] == 0.0
    assert [(m["kind"], m["label"], m["s"]) for m in mon["strip"]["media"]] == [
        ("music", "Late", 0.0)
    ]
    assert open_at([]) == []

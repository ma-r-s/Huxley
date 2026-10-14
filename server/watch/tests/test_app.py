"""/app.json (T1.46): built from the same inputs and segments as the
page, with the honesty rules: no "usual" before 14 days with the walkie
online, offline days never shown as low use, struggles named."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from huxley_watch import app
from huxley_watch.activity import segments

from .logs import (
    NOW,
    TZ,
    ago,
    audio_start,
    bench,
    claim_ended,
    claim_started,
    commit,
    connected,
    device,
    disconnected,
    inputs,
    line,
    play_started,
    press,
    ptt_stop,
    radio_started,
    speaking,
    starting,
    stream_ended,
    stream_started,
    transcript,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from huxley_watch.bench import Bench

TODAY = NOW.astimezone(TZ).date()  # Mon 2026-10-12, now 15:00 local


def health(ts: datetime, conn: str) -> str:
    return line("client.device.health", ts, rssi=-52, heap=81000, conn_id=conn)


def online_day(
    d: date, conn: str, start: time = time(7), end: time = time(22), now: datetime = NOW
) -> list[str]:
    """The walkie connected from `start` to `end` (his time) that day,
    its minute line every 5 minutes."""
    lo = datetime.combine(d, start, TZ)
    hi = min(datetime.combine(d, end, TZ), now)
    out = [connected(lo, conn), device(lo + timedelta(milliseconds=80), conn)]
    t = lo + timedelta(minutes=5)
    while t < hi:
        out.append(health(t, conn))
        t += timedelta(minutes=5)
    if hi < now:
        out.append(disconnected(hi, conn))
    return out


def music(start: datetime, minutes: float, sid: str, label: str = "Adele") -> list[str]:
    return [
        stream_started(start, sid, label),
        play_started(start + timedelta(milliseconds=16), "spotify:artist:x"),
        stream_ended(start + timedelta(minutes=minutes), sid),
    ]


def track(ts: datetime, title: str, artists: str | None) -> str:
    return line("spotify.track", ts, skill="spotify", title=title, artists=artists, conn_id="w1")


def ask(
    at: datetime, said: str, conn: str, tools: Sequence[tuple[str, dict[str, Any]]] = ()
) -> list[str]:
    stop = at + timedelta(seconds=2)
    out = [
        press(at, conn),
        ptt_stop(stop, conn),
        commit(stop + timedelta(milliseconds=1), conn),
        transcript(stop + timedelta(seconds=1), "user", said),
        audio_start(stop + timedelta(seconds=2)),
        speaking(stop + timedelta(seconds=5), False),
    ]
    for name, args in tools:
        out.append(
            line(
                "session.rx.tool_call",
                stop + timedelta(seconds=2),
                name=name,
                call_id="c",
                args=args,
            )
        )
    return out


def build(lines: list[str], now: datetime = NOW, windows: Bench | None = None) -> app.AppStatus:
    i = inputs(sorted(lines, key=_ts), now=now, bench=windows)
    segs = segments(i.events, i.now, i.bench, i.walkie.conns, i.walkie.spans)
    return app.build(i, segs, interval_s=300)


def _ts(raw: str) -> str:
    import json

    return str(json.loads(raw)["timestamp"])


def at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), TZ)


def history(days: int, minutes: list[float] | None = None) -> list[str]:
    """`days` past days with the walkie online all day and music each
    morning at 09:00 (minutes[k] long), plus today online since 07:00."""
    first = TODAY - timedelta(days=days)
    lines = [starting(at(first, 0) - timedelta(hours=1))]
    for k in range(days):
        d = first + timedelta(days=k)
        conn = f"d{k}"
        lines += online_day(d, conn)
        if minutes:
            lines += music(at(d, 9), minutes[k], f"s{k}")
    lines += online_day(TODAY, "today")
    return lines


# --- Today -------------------------------------------------------------------


def test_last_used_his_words_or_the_tool_and_its_query() -> None:
    base = [*online_day(TODAY, "w1"), *ask(at(TODAY, 10), "¿Qué hora es?", "w1")]
    tool = [("play_music", {"query": "Adele", "kind": "artist"})]
    last = build([*base, *ask(at(TODAY, 14, 20), "", "w1", tool)])["today"]["last_used"]
    assert last is not None
    assert (last["clock"], last["said"], last["tool"], last["query"]) == (
        "14:20",
        None,
        "play_music",
        "Adele",
    )
    last = build([*base, *ask(at(TODAY, 14, 20), "pon a Adele", "w1")])["today"]["last_used"]
    assert last is not None and (last["said"], last["heard"]) == ("pon a Adele", True)


def test_last_used_never_shows_a_hallucination_as_his_words() -> None:
    lines = [
        *online_day(TODAY, "w1"),
        *ask(at(TODAY, 10), "Subtítulos por la comunidad de Amara.org", "w1"),
    ]
    last = build(lines)["today"]["last_used"]
    assert last is not None and (last["said"], last["heard"]) == (None, False)


def test_nothing_used_yet() -> None:
    today = build(online_day(TODAY, "w1"))["today"]
    assert today["last_used"] is None
    assert today["used"]["minutes"] == 0
    assert today["enjoying"] is None
    assert today["people"] == []


def test_used_today_counts_overlaps_once() -> None:
    """A press during the music is not more use: the union, not the sum."""
    lines = [
        *online_day(TODAY, "w1"),
        *music(at(TODAY, 10), 30, "s1"),
        *ask(at(TODAY, 10, 10), "más fuerte", "w1"),
    ]
    assert build(lines)["today"]["used"]["minutes"] == 30.0


def test_learning_his_routine_before_14_days() -> None:
    used = build(history(5, [30.0] * 5))["today"]["used"]
    assert used["state"] == "learning"
    assert (used["days"], used["learning_day"], used["needed"]) == (5, 6, 14)
    assert used["lo"] is None and used["hi"] is None and used["position"] is None


def test_offline_days_never_count_toward_his_usual() -> None:
    """13 online days and 3 with the walkie mostly offline: still learning."""
    lines = history(16, [30.0] * 16)
    first = TODAY - timedelta(days=16)
    offline = {f"d{k}" for k in (2, 5, 9)}
    lines = [x for x in lines if not any(f'"conn_id": "{c}"' in x for c in offline)]
    for k in (2, 5, 9):
        lines += online_day(first + timedelta(days=k), f"d{k}", time(7), time(9))  # 2 of 15 h
    status = build(lines)
    used = status["today"]["used"]
    assert used["state"] == "learning"
    assert used["days"] == 13
    assert status["trends"]["usual_day"] is None


def test_his_usual_range_after_14_days() -> None:
    minutes = [30.0 + 2 * k for k in range(14)]  # 30, 32, ... 56
    lines = history(14, minutes) + music(at(TODAY, 9), 10, "today-music")
    status = build(lines)
    used = status["today"]["used"]
    assert used["state"] == "range"
    assert used["days"] == 14
    assert (used["lo"], used["mid"], used["hi"]) == (36.5, 43.0, 49.5)
    assert used["minutes"] == 10.0
    assert used["position"] == "below"
    assert used["by"] == "15:00"
    usual = status["trends"]["usual_day"]
    assert usual is not None and (usual["lo"], usual["hi"]) == (36.5, 49.5)


def test_his_usual_is_by_this_time_of_day() -> None:
    """At 15:00, music he plays at 20:00 on other days isn't due yet."""
    first = TODAY - timedelta(days=14)
    lines = history(14)
    for k in range(14):
        lines += music(at(first + timedelta(days=k), 20), 60, f"e{k}")
    used = build(lines)["today"]["used"]
    assert used["state"] == "range"
    assert (used["lo"], used["hi"]) == (0.0, 0.0)
    assert used["position"] == "within"


def test_a_call_under_20_s_is_called_not_talked() -> None:
    """A call that rang out or dropped at once is not a conversation."""
    lines = [
        *online_day(TODAY, "w1"),
        claim_started(at(TODAY, 9), "ana"),
        claim_ended(at(TODAY, 9) + timedelta(seconds=19)),
        claim_started(at(TODAY, 10), "mario"),
        claim_ended(at(TODAY, 10) + timedelta(seconds=20)),
    ]
    people = build(lines)["today"]["people"]
    assert [(p["name"], p["talked"], p["minutes"]) for p in people] == [
        ("Mario", True, 0.33),
        ("Ana", False, 0.32),
    ]


def test_people_last_call_each() -> None:
    yesterday = TODAY - timedelta(days=1)
    lines = [
        *online_day(TODAY - timedelta(days=3), "a"),
        claim_started(at(TODAY - timedelta(days=3), 11), "mario"),
        claim_ended(at(TODAY - timedelta(days=3), 11, 12)),
        *online_day(yesterday, "b"),
        claim_started(at(yesterday, 18), "ana"),
        claim_ended(at(yesterday, 18, 5)),
        *online_day(TODAY, "c"),
        claim_started(at(TODAY, 9), "mario"),
        claim_ended(at(TODAY, 9, 3)),
    ]
    people = build(lines)["today"]["people"]
    assert [(p["name"], p["days_ago"], p["minutes"], p["calls_30d"]) for p in people] == [
        ("Mario", 0, 3.0, 2),
        ("Ana", 1, 5.0, 1),
    ]


def test_enjoying_now_the_track_playing() -> None:
    lines = [
        *online_day(TODAY, "w1"),
        stream_started(ago(minutes=20), "s1", "Adele"),
        play_started(ago(minutes=20), "spotify:artist:x"),
        track(ago(minutes=20), "Hello", "Adele"),
        track(ago(minutes=4), "Someone Like You", "Adele, Dan Wilson"),
    ]
    en = build(lines)["today"]["enjoying"]
    assert en is not None
    assert (en["kind"], en["title"], en["by"], en["playing"], en["until"]) == (
        "music",
        "Someone Like You",
        "Adele",
        True,
        None,
    )


def test_enjoying_a_book_with_its_chapter_progress() -> None:
    lines = [
        *online_day(TODAY, "w1"),
        line(
            "spotify.book_requested",
            ago(minutes=50),
            skill="spotify",
            query="Siddhartha",
            title="Siddhartha",
            author="Hermann Hesse",
            uri="spotify:show:1",
            chapters=19,
        ),
        stream_started(ago(minutes=50), "b1", "Siddhartha"),
        line(
            "spotify.play_started",
            ago(minutes=50),
            skill="spotify",
            context="spotify:show:1",
            book="Siddhartha",
            chapter="Capítulo 4",
            chapter_index=4,
            chapters=19,
            conn_id="w1",
        ),
        stream_ended(ago(minutes=10), "b1"),
        line(
            "spotify.stream_ended",
            ago(minutes=10),
            skill="spotify",
            reason="cancelled",
            book="Siddhartha",
            audio_ms=2_400_000,
            chapter="Capítulo 5",
            chapter_index=5,
            chapters=19,
            conn_id="w1",
        ),
    ]
    en = build(lines)["today"]["enjoying"]
    assert en is not None
    assert (en["kind"], en["title"], en["playing"], en["until"]) == (
        "book",
        "Siddhartha",
        False,
        "14:50",
    )
    assert (en["chapter"], en["chapter_index"], en["chapters"]) == ("Capítulo 5", 6, 19)


def test_a_book_from_before_the_chapter_index_shows_its_chapter_name() -> None:
    lines = [
        *online_day(TODAY, "w1"),
        line(
            "spotify.book_requested",
            ago(minutes=50),
            skill="spotify",
            title="Siddhartha",
            uri="spotify:show:1",
            chapters=19,
        ),
        stream_started(ago(minutes=50), "b1", "Siddhartha"),
        line(
            "spotify.play_started",
            ago(minutes=50),
            skill="spotify",
            context="spotify:show:1",
            book="Siddhartha",
            chapter="Créditos iniciales",
            conn_id="w1",
        ),
        stream_ended(ago(minutes=10), "b1"),
    ]
    en = build(lines)["today"]["enjoying"]
    assert en is not None
    assert (en["chapter"], en["chapter_index"], en["chapters"]) == ("Créditos iniciales", None, 19)


def test_used_today_says_how_much_of_the_day_the_walkie_was_offline() -> None:
    """Quiet is not unwell: offline 07:00-11:00 of 07:00-15:00 is half."""
    logs = starting(at(TODAY, 0, 5))  # the logs begin before the daytime
    status = build([logs, *online_day(TODAY, "w1", time(11), time(22))])
    assert status["today"]["used"]["offline_pct"] == 50
    assert build([logs, *online_day(TODAY, "w1")])["today"]["used"]["offline_pct"] is None
    # a few minutes away is not worth saying (under 5 %)
    late = build([logs, *online_day(TODAY, "w1", time(7, 20))])
    assert late["today"]["used"]["offline_pct"] is None


def test_device_state_rides_along() -> None:
    lines = [
        *online_day(TODAY, "w1", time(7), time(13)),
        line("client.system.battery", at(TODAY, 12, 50), pct=14, mv=3550, conn_id="w1"),
    ]
    dev = build(lines)["today"]["device"]
    assert (dev["online"], dev["since"], dev["battery_pct"], dev["battery_low"]) == (
        False,
        "13:00",
        14,
        True,
    )


# --- Trends ------------------------------------------------------------------


def test_seven_days_offline_days_hatched_not_low() -> None:
    lines = history(6, [40.0] * 6)
    first = TODAY - timedelta(days=6)
    lines = [x for x in lines if '"conn_id": "d3"' not in x]
    lines += online_day(first + timedelta(days=3), "d3", time(7), time(8))
    days = build(lines)["trends"]["days"]
    assert len(days) == 7
    assert [d["offline"] for d in days] == [False, False, False, True, False, False, False]
    assert days[3]["online_share"] is not None and days[3]["online_share"] < 0.1
    assert days[-1]["today"] and days[-1]["label"] == "Mon 12"


def test_days_before_the_logs_say_no_logs() -> None:
    lines = [starting(at(TODAY - timedelta(days=1), 12)), *online_day(TODAY, "w1")]
    days = build(lines)["trends"]["days"]
    assert [d["logs"] for d in days] == ["none"] * 5 + ["partial", "full"]
    assert not any(d["offline"] for d in days[:5])


def test_online_share_counts_from_where_the_logs_begin() -> None:
    """Logs that begin at 13:00 on a day the walkie was on from then: not
    an offline day, and Trends says the logs begin there."""
    d = TODAY - timedelta(days=1)
    lines = [*online_day(d, "x", time(13), time(22)), *online_day(TODAY, "w1")]
    day = build(lines)["trends"]["days"][-2]
    assert (day["logs"], day["online_share"], day["offline"]) == ("partial", 1.0, False)


def test_a_bench_window_is_not_the_walkie_offline() -> None:
    """A day spent on the bench (Mario testing) isn't his offline day."""
    d = TODAY - timedelta(days=1)
    lines = [starting(at(d, 0) - timedelta(hours=1)), *online_day(d, "x", time(7), time(9))]
    lines += online_day(TODAY, "w1")
    w = bench((at(d, 9), at(d, 15), "bench.py all"), (at(d, 15), at(d, 21), "bench.py all"))
    day = build(lines, windows=w)["trends"]["days"][-2]
    # Online 2 h of the 3 outside the bench (2 of 15 h counting it).
    assert day["online_share"] == round(2 / 3, 3)
    assert day["offline"] is False


def test_favourites_30_days() -> None:
    lines = [
        *online_day(TODAY, "w1"),
        stream_started(at(TODAY, 8), "m1", "Spotify"),
        play_started(at(TODAY, 8), "spotify:artist:x"),
        track(at(TODAY, 8), "Bésame mucho", "Los Panchos"),
        track(at(TODAY, 8, 10), "Despacito", "Luis Fonsi, Daddy Yankee"),
        stream_ended(at(TODAY, 8, 15), "m1"),
        stream_started(at(TODAY, 9), "r1", "Caracol Radio"),
        radio_started(at(TODAY, 9)),
        stream_ended(at(TODAY, 10), "r1"),
        stream_started(at(TODAY, 11), "r2", "Blu Radio"),
        radio_started(at(TODAY, 11)),
        stream_ended(at(TODAY, 11, 20), "r2"),
        stream_started(at(TODAY, 12), "b1", "Siddhartha"),
        line(
            "spotify.play_started",
            at(TODAY, 12),
            skill="spotify",
            context="spotify:show:1",
            book="Siddhartha",
            chapter="Capítulo 2",
            chapter_index=2,
            chapters=19,
            conn_id="w1",
        ),
        stream_ended(at(TODAY, 12, 30), "b1"),
    ]
    fav = build(lines)["trends"]["favourites"]
    assert fav["artist"] is not None and (fav["artist"]["name"], fav["artist"]["minutes"]) == (
        "Los Panchos",
        10.0,
    )
    assert fav["station"] is not None and fav["station"]["name"] == "Caracol Radio"
    assert fav["book"] is not None
    assert (fav["book"]["name"], fav["book"]["chapter_index"], fav["book"]["chapters"]) == (
        "Siddhartha",
        3,
        19,
    )


def test_a_resumed_song_goes_on_with_the_track_playing() -> None:
    """A resume (label "Spotify") logs no new track: it is the one before."""
    lines = [
        *online_day(TODAY, "w1"),
        *music(at(TODAY, 8), 5, "m1", "Chayanne"),
        track(at(TODAY, 8), "Torero", "Chayanne"),
        *music(at(TODAY, 9), 20, "m2", "Spotify"),
    ]
    fav = build(lines)["trends"]["favourites"]
    assert fav["artist"] is not None and fav["artist"]["minutes"] == 25.0


def test_usually_first_uses_it_needs_5_days() -> None:
    first = TODAY - timedelta(days=6)
    lines = history(6)
    for k, (hh, mm) in enumerate([(8, 0), (9, 0), (8, 30), (10, 0)]):
        lines += ask(at(first + timedelta(days=k), hh, mm), "hola", f"d{k}")
    fu = build(lines)["trends"]["first_use"]
    assert (fu["clock"], fu["days"], fu["needed"]) == (None, 4, 5)
    lines += ask(at(first + timedelta(days=4), 8, 40), "hola", "d4")
    lines += ask(at(first + timedelta(days=4), 7, 50), "buenos días", "d4")  # that day's first
    fu = build(lines)["trends"]["first_use"]
    assert (fu["clock"], fu["days"]) == ("08:30", 5)


# --- Talks ----------------------------------------------------------------------


def test_struggles_today_and_conversations_by_day() -> None:
    yesterday = TODAY - timedelta(days=1)
    lines = [
        starting(at(yesterday, 0) - timedelta(hours=1)),
        *online_day(yesterday, "y"),
        *ask(at(yesterday, 10), "hola", "y"),
        *online_day(TODAY, "w1"),
        *ask(at(TODAY, 9), "¿Qué hora es?", "w1"),
        *ask(at(TODAY, 10), "Subtítulos por la comunidad de Amara.org", "w1"),
        *ask(at(TODAY, 11), "pon a Juanes", "w1", [("play_music", {"query": "Juanes"})]),
        *ask(at(TODAY, 11) + timedelta(seconds=20), "no, esa no", "w1"),
    ]
    talks = build(lines)["talks"]
    assert talks["requests_today"] == 4
    assert [(s["at"], s["reasons"], s["said"]) for s in talks["struggles"]] == [
        ("10:00", ["not_heard"], None),  # a hallucination is never quoted as his words
        ("11:00", ["corrected"], "pon a Juanes"),
    ]
    assert (
        talks["struggles"][0]["detail"]
        == 'transcribed as "Subtítulos por la comunidad de Amara.org"'
    )
    assert talks["struggles"][1]["t"] == 660.0  # minutes since his midnight: the day view's row
    assert talks["struggles"][1]["detail"] == "no, esa no"
    assert [(d["date"], d["requests"], d["struggles"], d["logs"]) for d in talks["days"]] == [
        (TODAY.isoformat(), 4, 2, "full"),
        (yesterday.isoformat(), 1, 0, "full"),
        ((yesterday - timedelta(days=1)).isoformat(), 0, 0, "partial"),  # the logs begin at 23:00
    ]


def test_bench_requests_are_not_his() -> None:
    t = at(TODAY, 10)
    w = bench((t - timedelta(minutes=5), t + timedelta(minutes=5), "bench.py hearing"))
    lines = [
        *online_day(TODAY, "w1"),
        *ask(t, "frase de prueba", "w1"),
        *ask(at(TODAY, 11), "hola", "w1"),
    ]
    talks = build(lines, windows=w)["talks"]
    assert talks["requests_today"] == 1


# --- the frame ------------------------------------------------------------------


def test_schema_times_and_timezone() -> None:
    status = build(online_day(TODAY, "w1"))
    assert status["schema"] == 1
    assert (status["checked"], status["timezone"], status["utc_offset_min"]) == (
        "15:00",
        "America/Bogota",
        -300,
    )
    assert status["stale"] is False


def test_mark_stale() -> None:
    status = build(online_day(TODAY, "w1"))
    assert app.mark_stale(status, NOW + timedelta(minutes=10)) is status
    flagged = app.mark_stale(status, NOW + timedelta(minutes=17))
    assert flagged["stale"] is True and status["stale"] is False


def test_summary_for_the_log_line() -> None:
    lines = [*online_day(TODAY, "w1"), *ask(at(TODAY, 9), "Gracias por ver el video", "w1")]
    assert app.summary(build(lines)) == {
        "requests_today": 1,
        "struggles_today": 1,
        "usual": "learning",
        "usual_days": 0,
        "people": 0,
        "talk_days": 1,
    }

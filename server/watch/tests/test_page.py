"""The page and /status.json: labels, numbers and charts, no sentences,
and every number drawn comes from the status the JSON serves."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from huxley_watch.activity import presses, segments
from huxley_watch.page import render
from huxley_watch.rules import evaluate
from huxley_watch.views import build, mark_stale

from .logs import (
    NOW,
    ago,
    alive,
    answer,
    bench,
    claim_ended,
    claim_started,
    connected,
    device,
    disconnected,
    flat,
    fw,
    inputs,
    line,
    play_started,
    playing,
    press,
    ptt_stop,
    radio_started,
    starting,
    stream_ended,
    stream_started,
    walkie_session,
)


def status_for(lines: list[str], **kw: Any) -> dict[str, Any]:
    i = inputs(lines, **kw)
    items = evaluate(i)
    return build(
        i,
        items,
        segments(i.events, i.now, i.bench, i.walkie.conns, i.walkie.spans),
        presses(i.events, i.bench, i.walkie.conns),
        alerts_channel="log",
        interval_s=300,
    )


def visible_text(page: str) -> list[str]:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)
    return [t.strip() for t in re.split(r"<[^>]+>", body) if t.strip()]


START = ago(hours=3)  # 12:00 local
DAY = flat(
    starting(ago(days=3)),
    connected(START, "w1"),
    device(START, "w1", version="5d89504f", rssi=-48),
    alive(START, "w1", 0),
    playing(START, 40, "w1", 6000),  # 12:00-12:40, gaps 9 %
    alive(NOW - timedelta(seconds=5), "w1", 120_000),
    press(ago(hours=2, minutes=50)),
    ptt_stop(ago(hours=2, minutes=50) + timedelta(seconds=3)),
    answer(ago(hours=2, minutes=49), ago(hours=2, minutes=48)),
    stream_started(ago(hours=2), "s1", "Joropo"),
    play_started(ago(hours=2), "spotify:track:x"),
    stream_ended(ago(hours=1), "s1"),
    stream_started(ago(minutes=50), "s2", "Caracol"),
    radio_started(ago(minutes=50)),
    stream_ended(ago(minutes=20), "s2"),
    claim_started(ago(minutes=15), "ana"),
    claim_ended(ago(minutes=10)),
)
DAY.sort(key=lambda x: x.split('"timestamp": "')[1][:20])  # the time only: ties keep their order


def test_all_good_has_no_cards() -> None:
    lines = flat(starting(ago(days=1)), walkie_session(ago(hours=1), NOW, "w1"))
    status = status_for(lines)
    page = render(status)
    assert status["headline"] == "All good"
    assert status["status"] == "good"
    assert "<h1>All good</h1>" in page
    assert 'class="dot good"' in page
    assert 'class="card alert' not in page


def test_cards_show_number_label_and_chip_only() -> None:
    lines = flat(
        starting(ago(days=1)),
        walkie_session(ago(hours=5), ago(hours=4), "w1"),
        disconnected(ago(hours=4), "w1"),
    )
    status = status_for(lines, disk_pct=91.0)
    page = render(status)
    assert status["headline"] == "2 need you"
    assert status["status"] == "crit"
    cards = re.findall(r'<div class="card alert (\w+)" data-id="([\w.]+)">', page)
    assert cards == [("crit", "walkie.offline"), ("warn", "disk.full")]
    assert '<div class="big">4 h</div><div class="what">Walkie offline</div>' in page
    assert '<span class="act">Check charger</span>' in page
    assert '<div class="big">91 %</div>' in page


def test_no_sentences_on_the_page() -> None:
    lines = flat(DAY, disconnected(NOW, "w1"))
    i = inputs(lines, disk_pct=95.0)
    items = evaluate(i)
    page = render(status_for(lines, disk_pct=95.0))
    for item in items:  # the alert's long text stays out of the page
        assert item.sentence not in page
        assert item.why not in page
    for text in visible_text(page):
        assert len(text.split()) <= 5, text  # labels and numbers, no sentence


def test_tiles() -> None:
    status = status_for(DAY)
    t = status["tiles"]
    assert t["walkie"] == {"online": True, "since": "12:00", "last_seen": None}
    assert t["wifi"] == {"rssi": -48, "bars": 4}
    assert t["audio"]["gap_pct"] == 9.1
    assert t["audio"]["over"] is True
    assert t["server"] == {
        "running": True,
        "ready": None,
        "up": "3 d",
        "firmware": "5d89504f",
        "starts_today": 0,
        "unclean_today": 0,
    }
    assert t["battery"]["pct"] is None
    assert t["battery"]["wired"] is None  # nothing either way: "not reported"
    assert t["openai"]["balance_left_usd"] is None
    page = render(status)
    labels = re.findall(r'<div class="lab">(?:<i[^>]*></i>)?([^<]+)</div>', page)
    assert labels == ["Walkie", "Wi-Fi", "Audio gaps", "Server", "Battery", "OpenAI today"]
    assert '<div class="val">9.1 %</div>' in page
    assert ">not reported<" in page  # no reading and no not_wired line


def test_today_strip_lanes_match_the_status() -> None:
    w = bench(
        (ago(minutes=9), ago(minutes=8), "bench.py --steps turn"),
        (ago(minutes=7), ago(minutes=6), "link test: off"),
    )
    status = status_for(DAY, bench=w)
    today = status["today"]
    assert today["online"] == [[720.0, 900.0]]  # 12:00 to now (15:00)
    assert today["talk"] == [[730.0, 730.05]]
    assert [m["kind"] for m in today["media"]] == ["music", "radio", "call"]
    assert today["media"][-1]["label"] == "ana"
    assert today["now_min"] == 900.0
    assert [x["kind"] for x in today["windows"]] == ["bench", "test"]
    page = render(status)
    drawn = [
        (float(a), float(b)) for a, b in re.findall(r'data-s="([\d.]+)" data-e="([\d.]+)"', page)
    ]
    listed = [tuple(x) for x in today["online"] + today["talk"] + today["answer"]] + [
        (m["s"], m["e"]) for m in today["media"]
    ]
    assert sorted(drawn) == sorted(listed)  # every span drawn, nothing else
    assert '<i class="band bench"' in page
    assert '<i class="band test"' in page
    assert 'class="now"' in page


def test_week_columns_and_no_logs_days() -> None:
    status = status_for(DAY)
    days = status["week"]["days"]
    assert len(days) == 7
    assert [d["has_logs"] for d in days] == [False, False, False, True, True, True, True]
    today = days[-1]
    assert today["presses"] == 1
    assert today["hours"]["music"] == 1.0
    assert today["hours"]["radio"] == 0.5
    page = render(status)
    assert page.count('<div class="nolog">no logs</div>') == 3
    for k, v in today["hours"].items():
        if v:
            assert f'class="{k}" style="height:' in page and f'data-h="{v}"' in page


def test_gaps_chart_is_a_continuous_half_hour_axis() -> None:
    status = status_for(DAY)
    slots = status["gaps"]["slots"]
    assert len(slots) == 24
    starts = [s["start"] for s in slots]
    assert starts[0] == "03:30" and starts[-1] == "15:00"  # every half hour, none skipped
    played = [s for s in slots if s["gap_pct"] is not None]
    assert [s["start"] for s in played] == ["12:00", "12:30"]
    assert all(s["gap_pct"] is None for s in slots if s["start"] < "12:00")  # empty slots kept
    page = render(status)
    assert page.count('<div class="slot"') == 24
    assert page.count('class="over"') == 2
    assert 'class="thresh"' in page
    for s in played:
        assert f'data-pct="{s["gap_pct"]}"' in page


def test_books_gauge_with_and_without_allowance() -> None:
    page = render(status_for(DAY, book_seconds=1800.0))
    assert '<div class="num">30 min</div>' in page
    assert "allowance not set" in page
    page = render(status_for(DAY, book_seconds=4 * 3600.0, book_hours_per_month=15.0))
    assert '<div class="num">11 h</div><div class="of">of 15 h</div>' in page  # what's left


def test_calls_per_person() -> None:
    status = status_for(DAY)
    assert status["calls"] == {"total": 1, "days": 7, "people": [{"name": "Ana", "count": 1}]}
    assert '<span class="name">Ana</span>' in render(status)


def test_status_json_carries_every_card_number() -> None:
    lines = flat(DAY, disconnected(NOW, "w1"))
    status = status_for(lines, now=NOW + timedelta(hours=1), disk_pct=88.0)
    json.dumps(status)
    page = render(status)
    bigs = re.findall(r'<div class="big">([^<]+)</div>', page)
    assert bigs == [i["big"] for i in status["items"]]


def test_a_stale_page_says_it_stopped_checking() -> None:
    status = status_for(flat(starting(ago(days=1)), walkie_session(ago(hours=1), NOW, "w1")))
    assert mark_stale(status, NOW + timedelta(minutes=10)) is status  # within 3 intervals
    stale = mark_stale(status, NOW + timedelta(hours=1))
    assert stale["stale"] is True
    page = render(stale)
    assert "<h1>Stopped checking</h1>" in page
    assert 'class="dot stale"' in page
    assert "All good" not in page
    assert "at 15:00" in page


def test_untrusted_text_is_escaped_in_text_and_tooltips() -> None:
    """Stream labels, contact names and bench `what` come from outside
    (Spotify, Telegram, another process): never markup, in text or in a
    data-tip attribute."""
    evil = 'Q"><img src=x onerror=alert(1)><'
    lines = flat(
        DAY,
        stream_started(ago(minutes=9), "s9", evil),
        radio_started(ago(minutes=9) + timedelta(milliseconds=5)),
        stream_ended(ago(minutes=8), "s9"),
        claim_started(ago(minutes=7), evil),
        claim_ended(ago(minutes=6)),
    )
    lines.sort(key=lambda x: x.split('"timestamp": "')[1][:24])
    w = bench((ago(minutes=5), ago(minutes=4), evil))
    page = render(status_for(lines, bench=w))
    assert "<img" not in page.lower()
    tips = re.findall(r'data-tip="([^"]*)"', page)
    assert sum("Q&quot;&gt;&lt;img src=x" in t for t in tips) >= 2  # the stream's and the bench's
    assert 'Q"&gt;&lt;Img Src=X' in page  # the caller's row: element text (title case)


def test_the_page_is_self_contained() -> None:
    page = render(status_for(DAY))
    assert not re.search(r'<script[^>]+src=|<link[^>]+href=|src="http', page)
    assert "prefers-color-scheme:dark" in page
    assert 'name="viewport"' in page


def test_the_page_on_disk_checks_its_own_age() -> None:
    """Critic: the index.html --once leaves is never re-served, so the
    page itself turns to "Stopped checking" when it is too old."""
    page = render(status_for(DAY))
    assert 'data-checked="2026-10-12T20:00:00+00:00"' in page
    assert 'data-interval="300"' in page
    assert "Stopped checking" in page.split("<script>")[1]  # the inline check


def test_battery_tile_shows_the_reading() -> None:
    lines = flat(DAY, line("client.system.battery", ago(minutes=3), mv=3870, pct=62, conn_id="w1"))
    status = status_for(lines)
    b = status["tiles"]["battery"]
    assert (b["pct"], b["mv"], b["wired"], b["stale"]) == (62, 3870, True, False)
    page = render(status)
    assert ">62 %<" in page
    assert ">3.87 V<" in page
    assert "Battery not wired" not in page


def test_battery_not_wired_says_so() -> None:
    lines = flat(DAY, fw(ago(minutes=30), "w1", "battery.not_wired probe=gpio6"))
    status = status_for(lines)
    assert status["tiles"]["battery"]["wired"] is False
    page = render(status)
    assert ">not wired<" in page
    assert "Battery not wired" in page


def _battery_day() -> list[str]:
    """The walkie as it reports: a reading every 20 minutes while connected.
    Online from 190 to 70 minutes ago, off for 40 minutes, back for the last
    half hour; plus one reading from yesterday."""
    out = [line("client.system.battery", ago(hours=30), mv=4100, pct=95, conn_id="w0")]
    out += walkie_session(ago(minutes=190), ago(minutes=70), "w1")
    out.append(disconnected(ago(minutes=70), "w1"))
    for k, m in enumerate(range(190, 69, -20)):  # 7 readings, 80 % down to 74 %
        out.append(
            line("client.system.battery", ago(minutes=m), mv=3900, pct=80 - k, conn_id="w1")
        )
    out += walkie_session(ago(minutes=30), NOW, "w2")
    for m in (30, 10):
        out.append(line("client.system.battery", ago(minutes=m), mv=3700, pct=18, conn_id="w2"))
    return out


def test_battery_history_keeps_the_last_day_and_breaks_only_when_offline() -> None:
    h = status_for(flat(DAY, *_battery_day()))["battery_history"]
    pts = h["points"]
    assert h["window_min"] == 24 * 60
    assert [p["pct"] for p in pts] == [80, 79, 78, 77, 76, 75, 74, 18, 18]  # not yesterday's 95
    # 20 minutes between readings is normal; only the 40 minutes offline breaks the line
    assert [p["gap_before"] for p in pts].count(True) == 1 and pts[7]["gap_before"]
    assert pts[-1]["mv"] == 3700 and pts[-1]["m"] == 24 * 60 - 10
    assert (h["low_pct"], h["crit_pct"]) == (20, 10)
    assert all(m["label"][-3:] == ":00" for m in h["marks"])
    assert 3 <= len(h["marks"]) <= 4  # every 6 hours


def test_battery_line_breaks_across_a_restart_closed_span() -> None:
    """The critic's case: a server restart ends a connection at the walkie's
    last line, and a reading can land after it. The offline stretch then
    doesn't contain the reading pair, but it overlaps it by an hour."""
    lines = walkie_session(ago(minutes=130), ago(minutes=125), "w1")
    lines.append(line("client.system.battery", ago(minutes=124), mv=3800, pct=50, conn_id="w1"))
    lines.append(starting(ago(minutes=123)))
    lines += walkie_session(ago(minutes=62), NOW, "w2")
    lines.append(line("client.system.battery", ago(minutes=60), mv=3790, pct=48, conn_id="w2"))
    pts = status_for(flat(DAY, *lines))["battery_history"]["points"]
    assert [p["gap_before"] for p in pts] == [False, True]


def test_battery_reading_at_the_right_edge_keeps_its_tip() -> None:
    lines = walkie_session(ago(minutes=10), NOW, "w1")
    lines.append(line("client.system.battery", NOW, mv=3700, pct=30, conn_id="w1"))
    chart = render(status_for(flat(DAY, *lines))).split('id="battery"')[1].split("</section>")[0]
    assert "30 % (3.70 V)" in chart


def test_battery_history_one_point_per_five_minutes() -> None:
    lines = walkie_session(ago(minutes=60), NOW, "w1")
    lines += [
        line("client.system.battery", ago(minutes=m), mv=3800, pct=50, conn_id="w1")
        for m in range(60, 0, -1)
    ]
    assert len(status_for(flat(DAY, *lines))["battery_history"]["points"]) <= 13


def test_battery_chart_breaks_where_the_walkie_was_off() -> None:
    page = render(status_for(flat(DAY, *_battery_day())))
    chart = page.split('id="battery"')[1].split("</section>")[0]
    assert "Battery, last 24 h" in chart
    assert chart.count('class="ln"') == 2  # two runs
    assert 'class="low"' in chart  # the 20 % line
    assert '<i class="bdot warn"' in chart  # 18 % sits under the low line
    assert ">18 %<" in chart
    assert "18 % (3.70 V)" in chart  # a half-hour tip
    assert '<span class="blab left"' in chart  # at the right edge the label sits left of the dot
    assert chart.count("data-tip=") <= 48


def test_battery_chart_empty_states() -> None:
    chart = render(status_for(DAY)).split('id="battery"')[1].split("</section>")[0]
    assert ">no readings<" in chart and 'class="ln"' not in chart
    lines = flat(DAY, fw(ago(minutes=30), "w1", "battery.not_wired probe=gpio6"))
    chart = render(status_for(lines)).split('id="battery"')[1].split("</section>")[0]
    assert ">not wired<" in chart


def test_the_server_tile_counts_todays_unclean_restarts() -> None:
    """Flickering power: each cut is a start with no stop before it."""
    lines = flat(
        DAY,
        starting(ago(hours=3)),
        starting(ago(hours=2)),
        starting(ago(minutes=30)),
    )
    status = status_for(lines)
    sv = status["tiles"]["server"]
    assert sv["unclean_today"] >= 3 and sv["starts_today"] >= sv["unclean_today"]
    page = render(status)
    assert f">{sv['starts_today']} restarts today, {sv['unclean_today']} unclean<" in page


def test_no_restart_line_on_a_clean_day() -> None:
    assert "restarts today" not in render(status_for(DAY))


def test_server_up_but_not_ready() -> None:
    status = status_for(DAY, health_ok=False)
    assert status["tiles"]["server"]["ready"] is False
    assert ">not ready, 3 d, fw 5d89504f<" in render(status)


def test_the_day_picker_sits_above_the_strip_with_the_panel_under_it() -> None:
    status = status_for(DAY)
    status["archive"] = {"today": "2026-10-12", "first": "2026-10-09", "prev": "2026-10-11"}
    page = render(status)
    picker = page.index('class="picker"')
    assert picker < page.index('class="strip"') < page.index('id="panel"')
    assert 'data-go="prev" data-day="2026-10-11"' in page
    assert 'data-go="next" data-day="" aria-label="next day" disabled' in page
    assert 'data-go="today" disabled>Today<' in page
    assert '<span class="pd">Mon 12 Oct</span>' in page
    assert '<symbol id="i-play"' in page  # the panels' icons
    # Reloading would cut a recording or a past day short: the script
    # reloads only when idle; the meta refresh is for pages without it.
    assert '<noscript><meta http-equiv="refresh"' in page


def test_no_previous_day_without_older_logs() -> None:
    status = status_for(DAY)
    status["archive"] = {"today": "2026-10-12", "first": "2026-10-12", "prev": None}
    assert 'data-go="prev" data-day="" aria-label="previous day" disabled' in render(status)


def test_the_huxley_lane_is_the_answer_not_the_song() -> None:
    assert status_for(DAY)["today"]["answer"] == [[731.0, 732.0]]  # 12:11 to 12:12

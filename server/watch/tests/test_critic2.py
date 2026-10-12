"""Regression tests for the second critic's findings (2026-10-12). Each
failed on the code before the fix."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from huxley_watch.__main__ import once_summary
from huxley_watch.activity import presses, segments
from huxley_watch.alerts import AlertState, plan
from huxley_watch.bench import BenchReader
from huxley_watch.config import ConfigError, load_config
from huxley_watch.events import LogReader
from huxley_watch.facts import server_alive, walkie_state
from huxley_watch.rules import evaluate, make_inputs
from huxley_watch.views import build
from huxley_watch.web import make_server
from huxley_watch.wlog import WatchLog

from .logs import (
    NOW,
    TZ,
    ago,
    alive,
    bench,
    bench_line,
    claim_ended,
    claim_started,
    connected,
    connection_failed,
    device,
    disconnected,
    events,
    flat,
    inputs,
    line,
    phone,
    press,
    ptt_stop,
    radio_started,
    session_connected,
    speaking,
    starting,
    stopped,
    stream_ended,
    stream_started,
    walkie_session,
)
from .test_alerts import FakeSender
from .test_cycle import setup, watcher

if TYPE_CHECKING:
    from pathlib import Path

    from huxley_watch.rules import Inputs

BOOT = starting(ago(hours=6))


def status_for(i: Inputs) -> dict[str, Any]:
    w = i.walkie
    return build(
        i,
        evaluate(i),
        segments(i.events, i.now, i.bench, w.conns, w.spans),
        presses(i.events, i.bench, w.conns),
        alerts_channel="log",
        interval_s=300,
    )


# 1. A stream said again, and a paused stream.


def test_a_stream_said_again_keeps_its_start() -> None:
    """Real: Caracol ran 13:27:53 to 13:49:15 but was drawn as 16 s, the
    coordinator re-announcing it each time the walkie came back."""
    lines = [
        stream_started(ago(minutes=30), "s1", "Caracol"),
        radio_started(ago(minutes=30)),
        stream_started(ago(minutes=20), "s1", "Caracol"),  # client back
        stream_started(ago(minutes=10), "s1", "Caracol"),
        stream_ended(ago(minutes=5), "s1"),
    ]
    segs = segments(events(lines), NOW)
    assert [(s.kind, s.start, s.end) for s in segs] == [("radio", ago(minutes=30), ago(minutes=5))]


def test_a_paused_stream_is_two_blocks_and_counts_only_played_time() -> None:
    lines = [
        stream_started(ago(minutes=30), "s1", "Caracol"),
        radio_started(ago(minutes=30)),
        stream_ended(ago(minutes=20), "s1"),  # a reminder parks it
        stream_started(ago(minutes=18), "s1", "Caracol"),  # back
        stream_started(ago(minutes=10), "s1", "Caracol"),  # and said again
        stream_ended(ago(minutes=5), "s1"),
    ]
    segs = segments(events(lines), NOW)
    assert [(s.kind, round(s.seconds / 60)) for s in segs] == [("radio", 10), ("radio", 13)]


# 2. No false "cleared".


def run(states: list[tuple[Inputs, str]]) -> list[str]:
    log = WatchLog(None)
    state = AlertState()
    for i, _ in states:
        state = plan(state, evaluate(i), i.now, TZ, False, log)
    return [r["id"] for r in log.events("watch.item_cleared")]


def test_an_open_offline_item_stays_open_when_a_window_starts() -> None:
    base = flat(
        BOOT, walkie_session(ago(hours=3), ago(hours=2), "w1"), disconnected(ago(hours=2), "w1")
    )
    first = inputs(base, now=ago(minutes=10))
    assert [x.id for x in evaluate(first)] == ["walkie.offline"]
    w = bench((ago(minutes=5), NOW + timedelta(minutes=30), "link test: router off"))
    second = inputs(base, bench=w, was_open=frozenset({"walkie.offline"}))
    assert [x.id for x in evaluate(second)] == ["walkie.offline"]
    assert run([(first, ""), (second, "")]) == []


def test_server_down_keeps_an_open_offline_item() -> None:
    lines = flat(
        BOOT, walkie_session(ago(hours=3), ago(hours=2), "w1"), disconnected(ago(hours=2), "w1")
    )
    first = inputs(lines, now=ago(minutes=30))
    down = inputs(flat(lines, stopped(ago(minutes=20))), was_open=frozenset({"walkie.offline"}))
    assert {x.id for x in evaluate(down)} == {"server.down", "walkie.offline"}
    assert run([(first, ""), (down, "")]) == []
    # Not open before: the server being down doesn't open it.
    fresh = inputs(flat(lines, stopped(ago(minutes=20))))
    assert [x.id for x in evaluate(fresh)] == ["server.down"]


def test_quota_keeps_an_open_openai_down_item() -> None:
    fails = [connection_failed(ago(minutes=m)) for m in (12, 8, 4)]
    lines = flat(
        BOOT, session_connected(ago(hours=1)), fails, walkie_session(ago(hours=1), NOW, "w1")
    )
    lines.sort(key=lambda x: x.split('"timestamp": "')[1][:24])
    first = inputs(lines, now=ago(minutes=3))
    assert [x.id for x in evaluate(first)] == ["openai.down"]
    quota = line("session.rx.error", ago(minutes=2), level="error", code="insufficient_quota")
    second = inputs(flat(lines, quota), was_open=frozenset({"openai.down"}))
    assert {x.id for x in evaluate(second)} == {"openai.quota", "openai.down"}
    assert run([(first, ""), (second, "")]) == []


def test_server_dependent_items_stay_open_while_the_server_is_down() -> None:
    failed = line("skill.setup_failed", ago(hours=1), level="error", skill="telegram", error="x")
    lines = flat(starting(ago(hours=2)), failed, walkie_session(ago(hours=1), NOW, "w1"))
    lines.sort(key=lambda x: x.split('"timestamp": "')[1][:24])
    first = inputs(lines, now=ago(minutes=30))
    assert [x.id for x in evaluate(first)] == ["telegram.down"]
    down = inputs(flat(lines, stopped(ago(minutes=20))), was_open=frozenset({"telegram.down"}))
    assert {x.id for x in evaluate(down)} == {"server.down", "telegram.down"}


# 3. Windows capped at 6 h; "Testing until" instead of "All good".


def test_a_window_longer_than_6_hours_is_capped_and_logged(tmp_path: Path) -> None:
    path = tmp_path / "bench.jsonl"
    path.write_text(
        bench_line(ago(hours=1), ago(hours=1) + timedelta(days=3), "link test: forever") + "\n"
    )
    log = WatchLog(None)
    reader = BenchReader(path, log)
    w = reader.read().windows[0]
    assert w.end - w.start == timedelta(hours=6)
    reader.read()
    assert len(log.events("watch.bench_window_capped")) == 1


def test_the_header_says_testing_while_a_window_silences_alerts() -> None:
    lines = flat(BOOT, walkie_session(ago(hours=1), NOW, "w1"))
    w = bench((ago(minutes=5), ago(minutes=-25), "link test: router off"))
    status = status_for(inputs(lines, bench=w))
    assert (status["status"], status["headline"]) == ("test", "Testing until 15:25")
    assert status_for(inputs(lines))["headline"] == "All good"


# 4. Only the walkie's presses.


def test_presses_and_the_him_lane_count_only_the_walkie() -> None:
    lines = flat(
        BOOT,
        walkie_session(ago(hours=1), NOW, "w1"),
        connected(ago(minutes=40), "p1"),
        phone(ago(minutes=40), "p1"),
        press(ago(minutes=30), "w1"),
        ptt_stop(ago(minutes=30) + timedelta(seconds=2), "w1"),
        press(ago(minutes=20), "p1"),  # the phone app
        ptt_stop(ago(minutes=20) + timedelta(seconds=2), "p1"),
    )
    lines.sort(key=lambda x: x.split('"timestamp": "')[1][:24])
    i = inputs(lines)
    status = status_for(i)
    assert status["week"]["days"][-1]["presses"] == 1
    assert len(status["today"]["talk"]) == 1


def test_activity_while_the_walkie_was_away_is_not_his() -> None:
    lines = flat(
        BOOT,
        walkie_session(ago(hours=1), ago(minutes=30), "w1"),
        disconnected(ago(minutes=30), "w1"),
        speaking(ago(minutes=20), True),  # to another client
        speaking(ago(minutes=19), False),
    )
    lines.sort(key=lambda x: x.split('"timestamp": "')[1][:24])
    assert status_for(inputs(lines))["today"]["answer"] == []


# 6, 7. The books month and a cut call.


def test_the_books_month_is_the_utc_month_like_the_db_key() -> None:
    late = datetime(2026, 11, 1, 3, 30, tzinfo=UTC)  # Oct 31, 22:30 in Bogota
    i = make_inputs(events([starting(late - timedelta(hours=1))]), late, TZ, inputs([]).th)
    assert status_for(i)["books"]["month"] == "November"


def test_a_call_cut_by_a_bench_window_counts_once() -> None:
    lines = flat(
        BOOT,
        walkie_session(ago(hours=1), NOW, "w1"),
        claim_started(ago(minutes=30), "ana"),
        claim_ended(ago(minutes=10)),
    )
    lines.sort(key=lambda x: x.split('"timestamp": "')[1][:24])
    w = bench((ago(minutes=22), ago(minutes=20), "dev.py cmd say"))
    calls = status_for(inputs(lines, bench=w))["calls"]
    assert calls["total"] == 1
    assert calls["people"] == [{"name": "Ana", "count": 1}]


# 8. "Seen" never moves backward as the board's lines age out.


def test_last_heard_survives_the_pruning_of_board_lines(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    lines = [
        BOOT.replace(iso_of(ago(hours=6)), iso_of(ago(hours=30))),
        connected(ago(hours=20), "w1"),
        device(ago(hours=20), "w1"),
        alive(ago(hours=15), "w1", 0),  # its last line, older than the 13 h kept
    ]
    (logs / "huxley.log").write_text("".join(x + "\n" for x in lines))
    result = LogReader(logs, timedelta(days=40), WatchLog(None)).read(NOW)
    assert all(e.name != "client.huxley.firmware_log" for e in result.events)  # pruned
    w = walkie_state(result.events, NOW, heard_map=result.heard)
    assert w.since == ago(hours=15)  # not 20 h ago, when it connected


def iso_of(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


# 9. Config and a fenced render.


@pytest.mark.parametrize(
    ("kwargs"),
    [
        {"thresholds": "gap_share_pct = 0\n"},
        {"thresholds": "offline_minutes = -5\n"},
        {"watch": 'bind = "[::0]:8780"\n'},
        {"watch": 'bind = "0:8780"\n'},
    ],
)
def test_more_bad_config(tmp_path: Path, kwargs: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        load_config(setup(tmp_path, [], **kwargs))


def test_an_ipv6_bind_serves(tmp_path: Path) -> None:
    cfg = load_config(setup(tmp_path, [], watch='bind = "[::1]:0"\n'))
    assert cfg.bind_host == "::1"
    w = watcher(setup(tmp_path, [BOOT]), FakeSender())
    try:
        server = make_server(w, cfg.bind_host, 0)
    except OSError:
        pytest.skip("no IPv6 loopback here")
    server.server_close()


def test_a_page_that_fails_to_build_still_saves_state_and_shows_the_cards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = flat(
        BOOT,
        walkie_session(ago(hours=2), ago(minutes=40), "w1"),
        disconnected(ago(minutes=40), "w1"),
    )
    w = watcher(setup(tmp_path, lines), FakeSender())

    def broken(*_a: object, **_k: object) -> None:
        raise ZeroDivisionError

    monkeypatch.setattr("huxley_watch.cycle.build", broken)
    status = w.cycle(NOW)
    assert status["error"] == "ZeroDivisionError"
    assert [i["id"] for i in status["items"]] == ["walkie.offline"]
    page = (tmp_path / "state" / "index.html").read_text()
    assert "Walkie offline" in page
    saved = json.loads((tmp_path / "state" / "alerts.json").read_text())
    assert list(saved["open"]) == ["walkie.offline"]


# 10. Only the configured port's lock.


def test_only_the_configured_ports_lock_is_read(tmp_path: Path) -> None:
    (tmp_path / "huxley-8766.lock").write_text(str(os.getpid()))  # the A/B server, alive
    assert server_alive(tmp_path, port=8765) is None  # no lock for 8765
    (tmp_path / "huxley-8765.lock").write_text("999999999")  # gone
    assert server_alive(tmp_path, port=8765) is False
    assert server_alive(tmp_path, port=8766) is True


# 12. --once never prints None.


def test_once_summary_without_wifi_or_audio() -> None:
    i = inputs([BOOT])
    text = once_summary(status_for(i))
    assert "None" not in text
    assert "wifi: -" in text

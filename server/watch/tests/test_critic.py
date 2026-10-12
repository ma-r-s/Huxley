"""Regression tests for the critic's findings (2026-10-12). Each failed on
the code before the fix."""

from __future__ import annotations

from datetime import timedelta

from huxley_watch.config import Thresholds
from huxley_watch.facts import server_state, walkie_state
from huxley_watch.rules import (
    evaluate,
    rule_audio_gaps,
    rule_balance,
    rule_books,
    rule_disk,
    rule_server_down,
    rule_server_unclean,
    rule_spend_today,
    rule_spotify,
    rule_walkie_offline,
)

from .logs import (
    NOW,
    ago,
    alive,
    app_ready,
    bench,
    connected,
    cost,
    device,
    disconnected,
    events,
    flat,
    inputs,
    line,
    playing,
    starting,
    stopped,
    swap_committed,
    walkie_session,
)

BOOT = starting(ago(hours=6))


# 1. A persona swap or reload logs huxley_shutting_down / huxley_stopped.


def test_a_persona_swap_is_not_the_server_stopping() -> None:
    swap = flat(BOOT, stopped(ago(minutes=30)), app_ready(ago(minutes=30) + timedelta(seconds=2)))
    assert server_state(events(swap)).running is True
    assert rule_server_down(inputs(swap)) is None
    reload = flat(BOOT, stopped(ago(minutes=30)), swap_committed(ago(minutes=29)))
    assert rule_server_down(inputs(reload)) is None


def test_a_persona_swap_keeps_the_walkie_connected() -> None:
    lines = flat(
        BOOT,
        walkie_session(ago(hours=1), NOW, "w1"),
        stopped(ago(minutes=30)),
        app_ready(ago(minutes=30)),
    )
    lines.sort(key=lambda x: x.split('"timestamp": "')[1])
    w = walkie_state(events(lines), NOW)
    assert w.online is True
    assert rule_walkie_offline(inputs(lines)) is None


# 2. rule_spotify took the newest setup failure of any skill.


def test_spotify_setup_failure_found_behind_another_skills_failure() -> None:
    spotify = line(
        "skill.setup_failed",
        ago(minutes=50),
        level="error",
        skill="spotify",
        exception_class="KeyError",
        error="'client_id'",
    )
    news = line(
        "skill.setup_failed",
        ago(minutes=49),
        level="error",
        skill="news",
        exception_class="ConnectError",
        error="unreachable",
    )
    item = rule_spotify(inputs([starting(ago(hours=1)), spotify, news]))
    assert item is not None
    assert item.id == "spotify.down"


# 8. Unclean starts, and whether the process is alive.


def test_a_start_with_no_stop_before_it_is_unclean() -> None:
    crash = [starting(ago(minutes=50)), starting(ago(minutes=20))]
    item = rule_server_unclean(inputs(crash))
    assert item is not None
    assert (item.label, item.big) == ("Unclean restart", "1")
    clean = flat(starting(ago(minutes=50)), stopped(ago(minutes=21)), starting(ago(minutes=20)))
    assert rule_server_unclean(inputs(clean)) is None
    # A swap's stop lines don't make a later crash clean.
    swap_then_crash = flat(
        starting(ago(minutes=50)),
        stopped(ago(minutes=40)),
        app_ready(ago(minutes=40)),
        starting(ago(minutes=20)),
    )
    assert rule_server_unclean(inputs(swap_then_crash)) is not None
    assert rule_server_unclean(inputs(crash, now=NOW + timedelta(hours=2))) is None


def test_the_process_check_wins_over_the_last_line() -> None:
    # Last line a stop (a swap that never logged app_ready): alive says up.
    lines = flat(BOOT, stopped(ago(minutes=30)))
    assert rule_server_down(inputs(lines, server_alive=True)) is None
    # Killed hard: no stop line, but the process is gone.
    killed = flat(BOOT, cost(ago(minutes=20), 0.01, 0.5))
    item = rule_server_down(inputs(killed, server_alive=False))
    assert item is not None
    assert item.since == ago(minutes=20)


# 9. Hysteresis on the balance and the disk.


def test_balance_clears_only_well_past_the_threshold() -> None:
    lines = flat(starting(ago(days=7)), cost(ago(days=6), 1.0, 1.0))  # pace 1/7 a day
    kw = {"balance_at": ago(days=6, hours=1)}
    assert rule_balance(inputs(lines, balance_usd=6.5, **kw)) is None  # 5.5 left
    still = rule_balance(
        inputs(lines, balance_usd=6.5, was_open=frozenset({"openai.balance"}), **kw)
    )
    assert still is not None  # open, and 5.5 isn't 20 % past 5
    assert (
        rule_balance(inputs(lines, balance_usd=8.0, was_open=frozenset({"openai.balance"}), **kw))
        is None
    )


def test_balance_older_than_the_logs_says_it_may_be_less() -> None:
    lines = flat(starting(ago(days=1)), cost(ago(hours=12), 1.0, 1.0))
    item = rule_balance(inputs(lines, balance_usd=5.0, balance_at=ago(days=60)))
    assert item is not None
    assert "may be less" in item.sentence
    assert item.label == "OpenAI credit (or less)"


def test_disk_clears_five_points_below() -> None:
    open_ = frozenset({"disk.full"})
    assert rule_disk(inputs([BOOT], disk_pct=77.0)) is None
    assert rule_disk(inputs([BOOT], disk_pct=77.0, was_open=open_)) is not None
    assert rule_disk(inputs([BOOT], disk_pct=74.0, was_open=open_)) is None


# Nits.


def test_spend_today_clears_at_local_midnight() -> None:
    # 23:30 yesterday in Bogota is the same UTC day as now.
    canary = line(
        "cost.threshold_crossed",
        ago(hours=15, minutes=30),
        level="warning",
        tier="bug_canary",
        threshold_usd=5.0,
        day_total_usd=5.0,
    )
    assert rule_spend_today(inputs([canary])) is None


def test_a_refused_book_start_doesnt_clear_the_hours_alert() -> None:
    refused = line(
        "spotify.start_refused",
        ago(hours=2),
        level="warning",
        skill="spotify",
        uri="spotify:show:x",
        cause="book_no_hours",
    )
    retry = line(
        "spotify.stream_ended",
        ago(hours=1),
        skill="spotify",
        reason="refused",
        generation=3,
        played_ms=60_000,
        book="Las aventuras de Sherlock Holmes",
    )
    assert rule_books(inputs([BOOT, refused, retry])) is not None


# Bench and test windows.


def test_no_offline_alert_inside_a_link_test_and_counted_from_its_end() -> None:
    gone = ago(minutes=60)  # 14:00
    lines = flat(
        BOOT, walkie_session(gone - timedelta(hours=1), gone, "w1"), disconnected(gone, "w1")
    )
    test = bench((gone - timedelta(minutes=1), ago(minutes=10), "link test: router off"))
    assert rule_walkie_offline(inputs(lines, now=ago(minutes=20), bench=test)) is None  # inside
    assert rule_walkie_offline(inputs(lines, bench=test)) is None  # 10 min since it ended
    item = rule_walkie_offline(inputs(lines, now=NOW + timedelta(minutes=10), bench=test))
    assert item is not None
    assert "14:00" in item.sentence  # the page still says since when


def test_gaps_inside_a_window_dont_count() -> None:
    start = ago(minutes=60)
    lines = flat(
        BOOT,
        connected(start - timedelta(minutes=1), "w1"),
        device(start - timedelta(minutes=1), "w1"),
        alive(start, "w1", 0),
        playing(start, 30, "w1", 6000),
    )
    assert rule_audio_gaps(inputs(lines)) is not None
    window = bench(
        (start - timedelta(minutes=2), start + timedelta(minutes=31), "bench.py --wifi")
    )
    assert rule_audio_gaps(inputs(lines, bench=window)) is None


def test_every_rule_has_a_card() -> None:
    lines = flat(
        BOOT, walkie_session(ago(hours=2), ago(hours=1), "w1"), disconnected(ago(hours=1), "w1")
    )
    items = evaluate(inputs(lines, disk_pct=90.0, alert_failures=5, th=Thresholds()))
    assert {i.id for i in items} >= {"walkie.offline", "disk.full", "watch.alerts_failing"}
    for i in items:
        assert i.label and len(i.label.split()) <= 4
        assert i.big and len(i.big) <= 8
        assert i.chip and len(i.chip.split()) <= 3
        assert i.severity in ("warn", "crit")

"""Each rule: it opens, it stays shut when it should, it clears."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from huxley_watch.config import Thresholds
from huxley_watch.rules import (
    Item,
    evaluate,
    rule_audio_gaps,
    rule_balance,
    rule_battery,
    rule_books,
    rule_disk,
    rule_logs,
    rule_openai_down,
    rule_openai_quota,
    rule_server_down,
    rule_server_restarts,
    rule_skills,
    rule_spend_today,
    rule_spotify,
    rule_telegram,
    rule_walkie_offline,
)

from .logs import (
    NOW,
    TWIN_MAC,
    TZ,
    WALKIE_MAC,
    ago,
    alive,
    bench,
    connected,
    connection_failed,
    cost,
    device,
    disconnected,
    evicted,
    flat,
    info_rssi,
    inputs,
    line,
    phone,
    playing,
    session_connected,
    starting,
    stopped,
    walkie_session,
)

BOOT = starting(ago(hours=6))


def ids(lines: list[str], **kw: Any) -> set[str]:
    return {item.id for item in evaluate(inputs(lines, **kw))}


# --- walkie offline -------------------------------------------------------------


def test_walkie_online_is_not_an_item() -> None:
    lines = flat(BOOT, walkie_session(ago(hours=2), NOW, "w1"))
    assert rule_walkie_offline(inputs(lines)) is None


def test_walkie_offline_20_min_in_the_day_opens() -> None:
    lines = flat(
        BOOT,
        walkie_session(ago(hours=2), ago(minutes=20), "w1"),
        disconnected(ago(minutes=20), "w1"),
    )
    item = rule_walkie_offline(inputs(lines))
    assert item is not None
    assert item.id == "walkie.offline"
    assert item.sentence == "The walkie hasn't connected since 14:40."
    assert "charged" in item.action


def test_walkie_offline_10_min_is_not_yet_an_item() -> None:
    lines = flat(
        BOOT,
        walkie_session(ago(hours=2), ago(minutes=10), "w1"),
        disconnected(ago(minutes=10), "w1"),
    )
    assert rule_walkie_offline(inputs(lines)) is None


def test_walkie_back_online_clears() -> None:
    lines = flat(
        BOOT,
        walkie_session(ago(hours=2), ago(minutes=40), "w1"),
        disconnected(ago(minutes=40), "w1"),
        walkie_session(ago(minutes=5), NOW, "w2"),
    )
    assert rule_walkie_offline(inputs(lines)) is None


def test_walkie_offline_at_night_waits_for_daytime_minutes() -> None:
    # Gone at 21:50 local; at 22:05 only 10 min of it were daytime.
    gone = datetime(2026, 10, 13, 2, 50, tzinfo=UTC)  # 21:50 Bogota
    lines = flat(
        starting(gone - timedelta(hours=3)),
        walkie_session(gone - timedelta(hours=1), gone, "w1"),
        disconnected(gone, "w1"),
    )
    assert rule_walkie_offline(inputs(lines, now=gone + timedelta(minutes=15))) is None
    assert rule_walkie_offline(inputs(lines, now=gone + timedelta(hours=8))) is None  # 05:50
    morning = datetime(2026, 10, 13, 12, 10, tzinfo=UTC)  # 07:10: 10 + 10 min of day
    item = rule_walkie_offline(inputs(lines, now=morning))
    assert item is not None
    assert item.sentence == "The walkie hasn't connected since yesterday 21:50."


def test_walkie_not_seen_for_two_days_opens_even_without_daytime() -> None:
    th = Thresholds(day_start=time(7, 0), day_end=time(7, 0))  # no "day" at all
    gone = ago(hours=50)
    lines = flat(
        starting(gone - timedelta(hours=1)),
        walkie_session(gone - timedelta(minutes=30), gone, "w1"),
        disconnected(gone, "w1"),
    )
    assert rule_walkie_offline(inputs(lines, now=gone + timedelta(hours=47), th=th)) is None
    item = rule_walkie_offline(inputs(lines, th=th))
    assert item is not None
    assert item.sentence == "The walkie hasn't connected since Saturday 13:00."


def test_walkie_evicted_by_another_client_is_offline_from_then() -> None:
    lines = flat(
        BOOT,
        walkie_session(ago(hours=1), ago(minutes=30), "w1"),
        connected(ago(minutes=30), "p1"),
        evicted(ago(minutes=30), "w1", "p1"),
        phone(ago(minutes=30), "p1"),
    )
    item = rule_walkie_offline(inputs(lines))
    assert item is not None
    assert "14:30" in item.sentence


def test_server_restart_closes_the_walkies_connection() -> None:
    lines = flat(
        BOOT, walkie_session(ago(hours=1), ago(minutes=30), "w1"), starting(ago(minutes=30))
    )
    item = rule_walkie_offline(inputs(lines))
    assert item is not None
    assert "14:29" in item.sentence  # ended at the last line heard from it


def test_walkie_silent_without_a_disconnect_line_is_offline() -> None:
    # A server killed hard writes no disconnect: 30 min of nothing.
    lines = flat(BOOT, walkie_session(ago(hours=1), ago(minutes=30), "w1"))
    item = rule_walkie_offline(inputs(lines))
    assert item is not None
    assert "14:29" in item.sentence  # its last line, 14:29:55


def test_walkie_mac_filter_ignores_the_twin_board() -> None:
    lines = flat(
        BOOT,
        walkie_session(ago(hours=2), ago(hours=1), "w1", mac=WALKIE_MAC),
        disconnected(ago(hours=1), "w1"),
        walkie_session(ago(minutes=50), NOW, "t1", mac=TWIN_MAC),
    )
    assert rule_walkie_offline(inputs(lines)) is None  # any board counts
    item = rule_walkie_offline(inputs(lines, walkie_mac=WALKIE_MAC))
    assert item is not None
    assert "14:00" in item.sentence


def test_walkie_never_seen() -> None:
    lines = flat(starting(ago(hours=3)), line("huxley_ready", ago(hours=3)))
    item = rule_walkie_offline(inputs(lines))
    assert item is not None
    assert item.sentence == "The walkie hasn't connected at all since 12:00."


def test_server_down_replaces_walkie_offline() -> None:
    lines = flat(
        BOOT, walkie_session(ago(hours=1), ago(minutes=40), "w1"), stopped(ago(minutes=40))
    )
    found = ids(lines)
    assert "server.down" in found
    assert "walkie.offline" not in found


# --- audio cutting up --------------------------------------------------------------


def audio(gap_ms_per_min: int, minutes: float = 30, start_ago_min: float = 60) -> list[str]:
    start = ago(minutes=start_ago_min)
    return flat(
        BOOT,
        connected(start - timedelta(minutes=1), "w1"),
        device(start - timedelta(minutes=1), "w1", rssi=-52),
        alive(start, "w1", 0),
        playing(start, minutes, "w1", gap_ms_per_min),
    )


def test_audio_gaps_open_at_one_minute_in_eleven() -> None:
    item = rule_audio_gaps(inputs(audio(6000)))  # 6 s a minute: 9.1 %
    assert item is not None
    assert (
        item.sentence
        == "Audio has been cutting up over the last 3 h: about 1 minute in 11 was silence."
    )
    assert "-52 dBm" in item.action
    assert "looks fine" in item.action


def test_audio_gaps_under_the_threshold_is_not_an_item() -> None:
    assert rule_audio_gaps(inputs(audio(1000))) is None  # 1.6 %


def test_audio_gaps_need_enough_playing_to_say_anything() -> None:
    assert rule_audio_gaps(inputs(audio(20000, minutes=1))) is None


def test_audio_gaps_older_than_the_window_dont_count() -> None:
    assert rule_audio_gaps(inputs(audio(6000, start_ago_min=240))) is None


def test_audio_gaps_clear_with_hysteresis() -> None:
    open_ = frozenset({"audio.gaps"})
    assert rule_audio_gaps(inputs(audio(500), was_open=open_)) is None  # 0.8 %: clears
    still = rule_audio_gaps(inputs(audio(2400), was_open=open_))  # 3.8 %: between
    assert still is not None
    quiet = rule_audio_gaps(inputs(flat(BOOT), was_open=open_))  # nothing played
    assert quiet is not None
    assert "nothing has played since" in quiet.sentence


def test_audio_gaps_skip_a_reboot_of_the_counter() -> None:
    start = ago(minutes=60)
    lines = flat(
        audio(0, minutes=10),
        # the board reboots: the counter restarts from 0, no negative step
        alive(start + timedelta(minutes=11), "w1", 0),
        playing(start + timedelta(minutes=11), 10, "w1", 0),
    )
    assert rule_audio_gaps(inputs(lines)) is None


def test_audio_gaps_weak_wifi_says_so() -> None:
    lines = flat(audio(6000), info_rssi(ago(minutes=1), "w1", -81))
    item = rule_audio_gaps(inputs(lines))
    assert item is not None
    assert "weak (-81 dBm)" in item.action


# --- server ------------------------------------------------------------------------


def test_restart_loop_opens_at_three_in_an_hour_and_ages_out() -> None:
    lines = [starting(ago(minutes=50)), starting(ago(minutes=30)), starting(ago(minutes=5))]
    item = rule_server_restarts(inputs(lines))
    assert item is not None
    assert item.sentence == "The server restarted 3 times in the last hour."
    assert rule_server_restarts(inputs(lines[1:])) is None
    assert rule_server_restarts(inputs(lines, now=NOW + timedelta(minutes=20))) is None


def test_planned_restarts_inside_marked_windows_are_not_a_loop() -> None:
    """A deploy marked in bench.jsonl (the firmware session restarts the
    server after each one) is planned: three of them are not a crash loop."""
    lines = [starting(ago(minutes=50)), starting(ago(minutes=30)), starting(ago(minutes=5))]
    marked = bench(
        (ago(minutes=51), ago(minutes=49), "link test: restart for deploy"),
        (ago(minutes=31), ago(minutes=29), "link test: restart for deploy"),
    )
    assert rule_server_restarts(inputs(lines, bench=marked)) is None
    more = [*lines, starting(ago(minutes=3)), starting(ago(minutes=1))]
    assert rule_server_restarts(inputs(more, bench=marked)) is not None  # 3 unplanned


def test_server_down_after_five_minutes_and_clears_on_start() -> None:
    lines = flat(BOOT, stopped(ago(minutes=10)))
    item = rule_server_down(inputs(lines))
    assert item is not None
    assert item.sentence == "The Huxley server stopped at 14:50 and hasn't started again."
    assert rule_server_down(inputs(flat(BOOT, stopped(ago(minutes=3))))) is None
    assert rule_server_down(inputs(flat(lines, starting(ago(minutes=1))))) is None


def test_skill_setup_failed_in_the_running_persona() -> None:
    failed = line(
        "skill.setup_failed",
        ago(hours=1),
        level="error",
        skill="news",
        exception_class="ConnectError",
        error="network unreachable",
        persona="abuelos",
        cancelled_tasks=0,
    )
    items = rule_skills(inputs([starting(ago(hours=1, minutes=1)), failed]))
    assert [i.id for i in items] == ["skill.news"]
    assert items[0].sentence == "The news skill failed to start: network unreachable."
    # The next start set it up fine: cleared.
    assert rule_skills(inputs([starting(ago(hours=2)), failed, starting(ago(minutes=5))])) == []


def test_disabled_skills_from_app_ready_and_own_rule_skills_skipped() -> None:
    ready = line(
        "huxley_app_ready",
        ago(minutes=30),
        persona="Abuelo",
        skills=["news"],
        disabled_skills=["radio", "telegram"],
    )
    items = rule_skills(inputs([starting(ago(minutes=31)), ready]))
    assert [i.id for i in items] == ["skill.radio"]


# --- OpenAI --------------------------------------------------------------------------


def test_openai_down_after_three_failures_clears_on_connect() -> None:
    fails = [connection_failed(ago(minutes=m)) for m in (12, 8, 4)]
    item = rule_openai_down(inputs(flat(BOOT, session_connected(ago(hours=1)), fails)))
    assert item is not None
    assert item.sentence == "Huxley can't reach OpenAI: 3 failed tries since 14:48."
    assert rule_openai_down(inputs(flat(BOOT, fails[1:]))) is None
    assert rule_openai_down(inputs(flat(BOOT, fails, session_connected(ago(minutes=1))))) is None


def test_openai_quota_from_an_error_and_from_a_429() -> None:
    err = line(
        "session.rx.error",
        ago(minutes=10),
        level="error",
        code="insufficient_quota",
        message="You exceeded your current quota",
        error_type="insufficient_quota",
    )
    item = rule_openai_quota(inputs(flat(BOOT, err)))
    assert item is not None
    assert "lack of credit at 14:50" in item.sentence
    assert rule_openai_quota(inputs(flat(BOOT, err, cost(ago(minutes=1), 0.004, 1.0)))) is None
    http = connection_failed(
        ago(minutes=3),
        "websockets.exceptions.InvalidStatus: server rejected WebSocket connection: HTTP 429",
    )
    # Critic nit: an HTTP 429 alone is a rate limit, not credit. It counts
    # as a failed connect instead.
    assert rule_openai_quota(inputs(flat(BOOT, http))) is None
    found = ids(
        flat(BOOT, http, connection_failed(ago(minutes=2)), connection_failed(ago(minutes=1)))
    )
    assert "openai.down" in found
    assert "openai.quota" not in found
    # The quota item says it better than "can't reach".
    quota = connection_failed(
        ago(minutes=3), "openai.RateLimitError: Error code: 429 - insufficient_quota"
    )
    found = ids(
        flat(BOOT, quota, connection_failed(ago(minutes=2)), connection_failed(ago(minutes=1)))
    )
    assert "openai.quota" in found
    assert "openai.down" not in found


def test_spend_today_on_bug_canary_and_kill_switch() -> None:
    canary = line(
        "cost.threshold_crossed",
        ago(hours=1),
        level="warning",
        tier="bug_canary",
        threshold_usd=5.0,
        day_total_usd=5.0,
    )
    item = rule_spend_today(inputs([canary]))
    assert item is not None
    assert item.sentence == "OpenAI spend today passed USD 5.00, about 10 times a normal day."
    warn = line(
        "cost.threshold_crossed",
        ago(hours=1),
        level="warning",
        tier="warn",
        threshold_usd=0.5,
        day_total_usd=0.5,
    )
    assert rule_spend_today(inputs([warn])) is None
    kill = line("cost.kill_switch_triggered", ago(minutes=30), level="error", day_total_usd=20.01)
    item = rule_spend_today(inputs([canary, kill]))
    assert item is not None
    assert "USD 20 safety stop" in item.sentence
    # Yesterday's (UTC) day is over.
    old = line(
        "cost.threshold_crossed",
        ago(hours=21),
        level="warning",
        tier="bug_canary",
        threshold_usd=5.0,
        day_total_usd=5.0,
    )
    assert rule_spend_today(inputs([old])) is None


def test_balance_low_by_money_and_by_days() -> None:
    entered = ago(days=3)
    spend = [cost(ago(days=d), 2.0, 2.0) for d in (2.5, 1.5, 0.5)]  # USD 2 a day
    lines = flat(starting(ago(days=5)), spend)
    item = rule_balance(inputs(lines, balance_usd=10.0, balance_at=entered))
    assert item is not None
    # USD 4 left; the week's pace over the 5 days the logs cover is 1.2 a day.
    assert item.sentence == "OpenAI credits: about USD 4.00 left, about 3 days at this pace."
    assert rule_balance(inputs(lines, balance_usd=60.0, balance_at=entered)) is None
    # Plenty of money but under a week at this pace.
    assert rule_balance(inputs(lines, balance_usd=12.0, balance_at=entered)) is not None


def test_balance_run_out_and_partial_logs() -> None:
    lines = flat(starting(ago(days=1)), cost(ago(hours=12), 6.0, 6.0))
    item = rule_balance(inputs(lines, balance_usd=5.0, balance_at=ago(days=2)))
    assert item is not None
    assert item.sentence.startswith(
        "OpenAI credits have probably run out: USD 6.00 spent of the USD 5.00"
    )
    assert "The logs start" in item.sentence  # entered before the logs begin


def test_no_balance_entered_no_item() -> None:
    assert rule_balance(inputs(flat(BOOT, cost(ago(hours=1), 9.0, 9.0)))) is None


# --- audiobook hours ---------------------------------------------------------------


def refused(ts: datetime, cause: str = "book_no_hours") -> str:
    """A confirmed refusal: the skill played a control book (2026-10-12)."""
    return line(
        "spotify.refusal_explained",
        ts,
        level="warning",
        skill="spotify",
        uri="spotify:show:5YDIXaAsNFCxJdO9jidF0n",
        control="spotify:show:3Qrv7kBXD3b8GSMgCNQfjF",
        control_result="no" if cause == "book_no_hours" else "ok",
        cause=cause,
        conn_id="w1",
    )


def test_a_bare_start_refused_is_never_no_hours() -> None:
    """start_refused called an unavailable book 'no hours' (Siddhartha,
    2026-10-12): only refusal_explained is trusted."""
    bare = line(
        "spotify.start_refused",
        ago(hours=1),
        level="warning",
        skill="spotify",
        uri="spotify:show:0977vYklBQExCa3kfZhfgW",
        cause="book_no_hours",
        conn_id="w1",
    )
    assert rule_books(inputs([BOOT, bare])) is None


def test_book_cycle_renews_on_the_billing_day() -> None:
    from datetime import date

    from huxley_watch.facts import book_cycle

    start, end = book_cycle(NOW, TZ, 14)  # NOW = 12 Oct local
    assert (start.month, start.day, end.month, end.day) == (9, 14, 10, 14)
    start, end = book_cycle(NOW, TZ, 14, since=date(2026, 10, 12))
    assert (start.month, start.day) == (10, 12)  # the add-on began on the 12th
    start, end = book_cycle(NOW + timedelta(days=3), TZ, 14)
    assert (start.month, start.day, end.month, end.day) == (10, 14, 11, 14)
    start, end = book_cycle(datetime(2026, 2, 20, 12, tzinfo=TZ), TZ, 31)
    assert (start.month, start.day, end.month, end.day) == (1, 31, 2, 28)  # short month


def test_book_seconds_since_counts_finished_and_playing_books_only() -> None:
    from huxley_watch.facts import book_seconds_since

    lines = [
        line("spotify.play_started", ago(hours=3), skill="spotify", context="spotify:show:a"),
        line("spotify.stream_ended", ago(hours=2), skill="spotify", book="A", audio_ms=1_800_000),
        line("spotify.play_started", ago(hours=2), skill="spotify", context="spotify:artist:x"),
        line(
            "spotify.stream_ended",
            ago(hours=1),
            skill="spotify",
            reason="cancelled",
            audio_ms=900_000,
        ),
        line("spotify.play_started", ago(minutes=10), skill="spotify", context="spotify:show:b"),
    ]
    evs = inputs(lines).events
    used = book_seconds_since(evs, ago(hours=4), NOW)
    assert used == 1800 + 600  # the music isn't counted; book B has played 10 min
    assert book_seconds_since(evs, ago(minutes=30), NOW) == 600


def test_books_left_from_the_cycle() -> None:
    lines = [
        BOOT,
        line("spotify.play_started", ago(hours=3), skill="spotify", context="spotify:show:a"),
        line(
            "spotify.stream_ended",
            ago(hours=1),
            skill="spotify",
            book="A",
            audio_ms=13.5 * 3_600_000,
        ),
    ]
    from datetime import date

    i = inputs(lines, book_hours_per_month=15.0, book_renews_day=14, book_since=date(2026, 10, 1))
    item = rule_books(i)
    assert item is not None and item.big == "1.5 h"
    assert i.book_cycle is not None


def book_played(ts: datetime, audio_ms: int) -> str:
    return line(
        "spotify.stream_ended",
        ts,
        skill="spotify",
        reason="cancelled",
        generation=6,
        played_ms=audio_ms + 800,
        book="Las aventuras de Sherlock Holmes",
        audio_ms=audio_ms,
    )


def test_books_refused_for_hours_opens_and_a_book_playing_clears() -> None:
    item = rule_books(inputs(flat(BOOT, refused(ago(hours=2)))))
    assert item is not None
    assert item.sentence == "Spotify stopped playing audiobooks at 13:00: the hours ran out."
    assert (
        rule_books(inputs(flat(BOOT, refused(ago(hours=2)), book_played(ago(hours=1), 600_000))))
        is None
    )
    assert rule_books(inputs(flat(BOOT, refused(ago(hours=2), cause="book_unavailable")))) is None


def test_books_hours_out_line_and_last_month_ignored() -> None:
    out = line(
        "spotify.book_hours_out",
        ago(hours=1),
        level="warning",
        skill="spotify",
        control="spotify:show:x",
    )
    assert rule_books(inputs([out])) is not None
    last_month = refused(datetime(2026, 9, 30, 12, 0, tzinfo=UTC))
    assert rule_books(inputs([last_month])) is None


def test_books_low_from_the_db_and_the_budget() -> None:
    item = rule_books(inputs([BOOT], book_seconds=14 * 3600, book_hours_per_month=15.0))
    assert item is not None
    assert item.sentence == "About 1 h of audiobook time left until the hours renew."
    assert rule_books(inputs([BOOT], book_seconds=5 * 3600, book_hours_per_month=15.0)) is None
    used = rule_books(inputs([BOOT], book_seconds=15.2 * 3600, book_hours_per_month=15.0))
    assert used is not None
    assert used.sentence == "This month's audiobook hours are used up."
    assert rule_books(inputs([BOOT], book_seconds=14 * 3600)) is None  # no budget known


# --- Spotify and Telegram ----------------------------------------------------------------


def ws(ts: datetime, ok: bool) -> str:
    if ok:
        return line(
            "spotify.ws_state", ts, skill="spotify", connected=True, url="ws://127.0.0.1:9090"
        )
    return line(
        "spotify.ws_state",
        ts,
        level="warning",
        skill="spotify",
        connected=False,
        url="ws://127.0.0.1:9090",
        error="ConnectionRefusedError: [Errno 61] Connect call failed",
        retry_in_s=30.0,
    )


def auth(ts: datetime, logged_in: bool) -> str:
    return line(
        "spotify.auth_state",
        ts,
        skill="spotify",
        logged_in=logged_in,
        is_active=False,
        conn_id="w1",
    )


def test_soloist_unreachable_ten_minutes_opens_and_reconnect_clears() -> None:
    down = [ws(ago(minutes=m), False) for m in (15, 14, 13, 12)]
    item = rule_spotify(inputs(flat(BOOT, ws(ago(hours=1), True), down)))
    assert item is not None
    assert item.sentence == "Spotify's player (Soloist) has been unreachable since 14:45."
    assert (
        rule_spotify(inputs(flat(BOOT, ws(ago(hours=1), True), ws(ago(minutes=5), False)))) is None
    )
    assert rule_spotify(inputs(flat(BOOT, down, ws(ago(minutes=1), True)))) is None


def test_spotify_logged_out_and_back() -> None:
    lines = flat(BOOT, ws(ago(hours=1), True), auth(ago(minutes=30), False))
    item = rule_spotify(inputs(lines))
    assert item is not None
    assert item.sentence == "Spotify's player is logged out."
    assert rule_spotify(inputs(flat(lines, auth(ago(minutes=1), True)))) is None
    unpaired = line(
        "spotify.unavailable", ago(minutes=2), skill="spotify", reason="player_not_paired"
    )
    assert rule_spotify(inputs(flat(BOOT, ws(ago(hours=1), True), unpaired))) is not None


def test_spotify_setup_failed() -> None:
    failed = line(
        "skill.setup_failed",
        ago(hours=1),
        level="error",
        skill="spotify",
        exception_class="KeyError",
        error="'client_id'",
    )
    item = rule_spotify(inputs([starting(ago(hours=2)), failed]))
    assert item is not None
    assert item.sentence == "Spotify didn't start ('client_id')."


def test_telegram_setup_failed_or_no_credentials_until_it_connects() -> None:
    failed = line(
        "skill.setup_failed",
        ago(hours=1),
        level="error",
        skill="telegram",
        exception_class="AuthKeyUnregistered",
        error="[401 AUTH_KEY_UNREGISTERED]",
    )
    item = rule_telegram(inputs([starting(ago(hours=2)), failed]))
    assert item is not None
    assert item.sentence.startswith("Telegram didn't start ([401 AUTH_KEY_UNREGISTERED])")
    missing = line(
        "telegram.credentials_missing",
        ago(hours=1),
        level="warning",
        hint="Drop {api_id, api_hash, userbot_phone} into ...",
    )
    assert rule_telegram(inputs([starting(ago(hours=2)), missing])) is not None
    ok = line("telegram.transport.connected", ago(minutes=5), conn_id="w1")
    assert (
        rule_telegram(inputs([starting(ago(hours=2)), failed, starting(ago(minutes=6)), ok]))
        is None
    )


# --- disk, logs, and all together ----------------------------------------------------------


def test_disk_and_logs() -> None:
    assert rule_disk(inputs([BOOT], disk_pct=86.4)) is not None
    assert rule_disk(inputs([BOOT], disk_pct=50.0)) is None
    item = rule_logs(inputs([], logs_problem="huxley-watch can't find the server's logs at /x."))
    assert item is not None
    assert rule_logs(inputs([BOOT])) is None


def test_a_healthy_day_has_no_items() -> None:
    lines = flat(
        BOOT,
        session_connected(ago(hours=5)),
        ws(ago(hours=6), True),
        auth(ago(hours=6), True),
        line("telegram.transport.connected", ago(hours=6)),
        walkie_session(ago(hours=2), NOW, "w1"),
        cost(ago(hours=1), 0.01, 0.2),
    )
    assert (
        evaluate(inputs(lines, book_seconds=3600.0, book_hours_per_month=15.0, disk_pct=40.0))
        == []
    )


# --- battery ----------------------------------------------------------------


def _battery(pct: int, at: datetime) -> str:
    return line("client.system.battery", at, mv=3500 + pct * 7, pct=pct, conn_id="w1")


def _battery_item(lines: list[str], was_open: frozenset[str] = frozenset()) -> Item | None:
    return rule_battery(inputs(lines, was_open=was_open))


def test_battery_opens_under_20_and_is_critical_under_10() -> None:
    assert _battery_item([_battery(25, ago(minutes=2))]) is None
    low = _battery_item([_battery(18, ago(minutes=2))])
    assert low is not None and low.big == "18 %" and low.severity == "warn"
    crit = _battery_item([_battery(7, ago(minutes=2))])
    assert crit is not None and crit.severity == "crit"


def test_battery_clears_only_back_at_30() -> None:
    was = frozenset({"battery.low"})
    assert _battery_item([_battery(24, ago(minutes=2))], was) is not None  # a wobble
    assert _battery_item([_battery(31, ago(minutes=2))], was) is None  # charged


def test_battery_old_reading_opens_nothing_but_keeps_an_open_item() -> None:
    old = [_battery(12, ago(hours=3))]
    assert _battery_item(old) is None
    assert _battery_item(old, frozenset({"battery.low"})) is not None  # no false "charged"


def test_battery_takes_the_last_reading() -> None:
    lines = [_battery(15, ago(minutes=20)), _battery(40, ago(minutes=2))]
    assert _battery_item(lines) is None

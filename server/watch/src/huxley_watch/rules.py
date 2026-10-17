"""The rules: logs and DB facts in, "needs you" items out.

Each rule is a pure function of `Inputs` returning an `Item` (or a few,
or None). An item has two faces. The page shows a card: one big number,
a 2-3 word label, an action chip. The Telegram alert carries the long
form: one plain sentence, why it matters, one action, and the sentence
its clearing says. Its id is stable while the problem lasts, so the alert
goes out once when it appears and once when it clears (alerts.py),
however its numbers change in between.

Only signals the server logs today. What a rule would need and the server
doesn't log is listed in docs/observability.md ("What huxley-watch reads").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from huxley_watch.bench import Bench
from huxley_watch.credit import budget
from huxley_watch.facts import (
    BatteryState,
    battery_state,
    book_cycle,
    book_seconds_since,
    current_app,
    gaps_since,
    server_state,
    walkie_state,
    wifi_label,
)
from huxley_watch.text import hours, money, plural, short, span, usd, when

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from zoneinfo import ZoneInfo

    from huxley_watch.bookshelf import Shelf
    from huxley_watch.config import Thresholds
    from huxley_watch.credit import Admin, Budget
    from huxley_watch.events import Event
    from huxley_watch.facts import ServerState, WalkieState

# OpenAI refusing for money (docs: spend-limits guide, 2026-07). Credit:
# the prepaid balance is gone. Limit: a monthly cap was reached, the
# organization's or a project's we set, or OpenAI's own approved limit
# (billing_hard_limit_reached is the old name of the same cap).
CREDIT_CODES = frozenset({"insufficient_quota", "credit_balance_exhausted"})
LIMIT_CODES = frozenset(
    {
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "billing_hard_limit_reached",
    }
)
OWN_RULE_SKILLS = frozenset({"telegram", "spotify"})  # their rules say more
CLEAR_MARGIN = 1.2  # hysteresis: a rule with a threshold clears 20 % past it


@dataclass(frozen=True)
class Item:
    id: str
    label: str  # the card: 2-3 words
    big: str  # the card: one number
    chip: str  # the card: the action, 2-3 words
    sentence: str  # the alert: what is wrong
    why: str  # the alert: why it matters
    action: str  # the alert: what to do
    cleared: str  # the alert when it clears
    since: datetime | None = None
    severity: str = "warn"  # warn | crit


@dataclass(frozen=True)
class Inputs:
    events: Sequence[Event]
    now: datetime
    tz: ZoneInfo
    th: Thresholds
    walkie: WalkieState
    server: ServerState
    was_open: frozenset[str] = frozenset()
    bench: Bench = field(default_factory=Bench)
    logs_from: datetime | None = None  # earliest event read
    logs_problem: str | None = None  # the logs can't be read at all
    book_seconds: float | None = None  # this month's, from the persona DB
    book_hours_per_month: float | None = None
    # His books (T1.45): the skill's per-book summaries and the shelf.
    book_summaries: tuple[dict[str, Any], ...] = ()
    shelf: Shelf | None = None
    balance_usd: float | None = None
    balance_at: datetime | None = None
    disk_pct: float | None = None
    alert_failures: int = 0  # deliveries failed in a row
    battery: BatteryState = field(default_factory=BatteryState)
    # The audiobook allowance's cycle (renewal to renewal), when configured;
    # book_seconds is then the seconds played in it (from the logs).
    book_cycle: tuple[datetime, datetime] | None = None
    book_partial: bool = False  # the logs start after the cycle did
    health_ok: bool | None = None  # the server's /health "ok", when it answers
    # This month's OpenAI budget (credit.py): limit, spend, pace. One
    # computation for the rules and the page, so card and alert agree.
    budget: Budget | None = None
    admin: Admin | None = None  # the Admin API's last answer, with an admin key
    budget_error: str | None = None  # the budget couldn't be computed (by type)


def make_inputs(
    events: Sequence[Event],
    now: datetime,
    tz: ZoneInfo,
    th: Thresholds,
    *,
    walkie_mac: str | None = None,
    server_alive: bool | None = None,
    was_open: frozenset[str] = frozenset(),
    bench: Bench | None = None,
    logs_problem: str | None = None,
    book_seconds: float | None = None,
    book_hours_per_month: float | None = None,
    balance_usd: float | None = None,
    balance_at: datetime | None = None,
    disk_pct: float | None = None,
    alert_failures: int = 0,
    heard: Mapping[str, datetime] | None = None,
    health_ok: bool | None = None,
    book_renews_day: int | None = None,
    book_since: date | None = None,
    admin: Admin | None = None,
    monthly_limit_usd: float | None = None,
    openai_project_id: str | None = None,
    book_summaries: tuple[dict[str, Any], ...] = (),
    shelf: Shelf | None = None,
) -> Inputs:
    """Inputs with the walkie and server facts computed from the events."""
    cycle: tuple[datetime, datetime] | None = None
    partial = False
    if book_renews_day is not None:
        cycle = book_cycle(now, tz, book_renews_day, book_since)
        book_seconds = book_seconds_since(events, cycle[0], now)
        partial = bool(events) and events[0].ts > cycle[0]
    logs_from = events[0].ts if events else None
    month: Budget | None = None
    budget_error: str | None = None
    try:
        month = budget(
            events,
            now,
            admin=admin,
            project_id=openai_project_id,
            config_limit=monthly_limit_usd,
            logs_from=logs_from,
        )
    except Exception as exc:  # an odd cached answer must not cost the alerts
        budget_error = type(exc).__name__
    return Inputs(
        events=events,
        now=now,
        tz=tz,
        th=th,
        walkie=walkie_state(events, now, walkie_mac, timedelta(minutes=th.silent_minutes), heard),
        server=server_state(events, server_alive),
        was_open=was_open,
        bench=bench or Bench(),
        logs_from=logs_from,
        logs_problem=logs_problem,
        book_seconds=book_seconds,
        book_hours_per_month=book_hours_per_month,
        book_summaries=book_summaries,
        shelf=shelf,
        balance_usd=balance_usd,
        balance_at=balance_at,
        disk_pct=disk_pct,
        alert_failures=alert_failures,
        battery=battery_state(events),
        book_cycle=cycle,
        book_partial=partial,
        health_ok=health_ok,
        budget=month,
        admin=admin,
        budget_error=budget_error,
    )


# --- helpers --------------------------------------------------------------


def daytime_overlap(start: datetime, end: datetime, i: Inputs) -> timedelta:
    """How much of [start, end] fell inside the day window (local time)."""
    total = timedelta()
    day = start.astimezone(i.tz).date()
    last_day = end.astimezone(i.tz).date()
    for _ in range(62):  # bounded: beyond two months it's open anyway
        if day > last_day:
            break
        lo = datetime.combine(day, i.th.day_start, i.tz)
        hi = datetime.combine(day, i.th.day_end, i.tz)
        overlap = min(hi, end) - max(lo, start)
        if overlap > timedelta():
            total += overlap
        day += timedelta(days=1)
    return total


def balance_estimate(i: Inputs) -> tuple[float, float | None, bool] | None:
    """(USD left, days left at the last week's pace or None if no spend,
    partial: the logs start after the balance was entered, so spend
    before that is missing and the real figure is lower)."""
    if i.balance_usd is None or i.balance_at is None:
        return None
    costs = [
        (e.ts, float(e.get("cost_usd") or 0)) for e in i.events if e.name == "cost.response_done"
    ]
    spent = sum(c for ts, c in costs if ts > i.balance_at)
    left = i.balance_usd - spent
    week_start = max(i.now - timedelta(days=7), i.logs_from or i.now)
    days = max(1.0, (i.now - week_start).total_seconds() / 86400)
    pace = sum(c for ts, c in costs if ts >= week_start) / days
    partial = i.logs_from is not None and i.logs_from > i.balance_at
    return left, (left / pace if pace > 0 else None), partial


def spent_today(i: Inputs) -> float:
    """OpenAI spend since local midnight."""
    today = i.now.astimezone(i.tz).date()
    return sum(
        float(e.get("cost_usd") or 0)
        for e in i.events
        if e.name == "cost.response_done" and e.ts.astimezone(i.tz).date() == today
    )


def book_refusal(i: Inputs) -> Event | None:
    """The cycle's last confirmed 'no hours' from Spotify, unless a book has
    really played since (more hours were bought): 30 s of book audio, not a
    refused start. Confirmed = `spotify.refusal_explained cause=book_no_hours`
    (the skill played a control book, which was refused too) or
    `spotify.book_hours_out`; a bare start_refused is never trusted (it
    called an unavailable book "no hours", 2026-10-12). The cycle is the
    configured one, else the UTC month the skill counts."""
    if i.book_cycle is not None:
        cycle_start = i.book_cycle[0]

        def in_cycle(e: Event) -> bool:
            return e.ts >= cycle_start
    else:
        month = i.now.astimezone(UTC).strftime("%Y-%m")

        def in_cycle(e: Event) -> bool:
            return e.ts.strftime("%Y-%m") == month

    refusal: Event | None = None
    for e in i.events:
        if not in_cycle(e):
            continue
        if e.name == "spotify.book_hours_out" or (
            e.name == "spotify.refusal_explained" and e.get("cause") == "book_no_hours"
        ):
            refusal = e
        elif (
            refusal is not None
            and e.name == "spotify.stream_ended"
            and e.get("book")
            and e.get("reason") != "refused"
            and float(e.get("audio_ms") or 0) >= 30_000
        ):
            refusal = None
    return refusal


# --- rules ----------------------------------------------------------------


def rule_logs(i: Inputs) -> Item | None:
    if i.logs_problem is None:
        return None
    return Item(
        "watch.logs",
        "Logs unreadable",
        "0",
        "Set logs_dir",
        i.logs_problem,
        "Without them this page can't tell you anything, and would say nothing is wrong.",
        "Point huxley.logs_dir (or huxley.server_dir) in watch.toml at the server's logs.",
        "huxley-watch can read the server's logs again.",
        severity="crit",
    )


def rule_server_down(i: Inputs) -> Item | None:
    s = i.server
    if s.running is not False or s.stopped_at is None:
        return None
    if i.now - s.stopped_at < timedelta(minutes=i.th.down_minutes):
        return None
    return Item(
        "server.down",
        "Server down",
        short(i.now - s.stopped_at),
        "Start server",
        f"The Huxley server stopped at {when(s.stopped_at, i.now, i.tz)} and hasn't started again.",
        "The walkie can't do anything until it's back.",
        "Start it on the box (systemctl start huxley) and read the end of its last log.",
        "The Huxley server is running again.",
        s.stopped_at,
        "crit",
    )


def rule_server_restarts(i: Inputs) -> Item | None:
    window = timedelta(minutes=i.th.restarts_window_minutes)
    # A restart inside a marked window (bench.jsonl: a deploy, a bench or
    # link test) was planned by whoever marked it: not a crash loop.
    recent = [t for t in i.server.starts if i.now - t <= window and not i.bench.suppressed(t)]
    if len(recent) < i.th.restarts_max:
        return None
    label = "hour" if window == timedelta(hours=1) else span(window)
    return Item(
        "server.restarts",
        "Restarts, 1 h" if window == timedelta(hours=1) else "Restarts",
        str(len(recent)),
        "Read the log",
        f"The server restarted {len(recent)} times in the last {label}.",
        "Each restart cuts off whatever he was doing.",
        "Look at the log around the restarts for a crash, or roll back the last deploy.",
        "The server has stopped restarting.",
        recent[0],
    )


def rule_server_unclean(i: Inputs) -> Item | None:
    window = timedelta(minutes=i.th.unclean_window_minutes)
    recent = [t for t in i.server.unclean if i.now - t <= window and not i.bench.suppressed(t)]
    if not recent:
        return None
    return Item(
        "server.unclean",
        "Unclean restart",
        str(len(recent)),
        "Read the log",
        f"The server started at {when(recent[-1], i.now, i.tz)} without having stopped: it crashed, was killed, or the box lost power.",
        "Whatever he was doing was cut off, and a crash can come back.",
        "Read the end of the previous log (the last huxley_<time>.log) and the box's power history.",
        "No unclean restart in the last hour.",
        recent[-1],
    )


def rule_walkie_offline(i: Inputs) -> Item | None:
    w = i.walkie
    if w.online or not i.events:
        return None
    if battery_cut_off(i):
        return None  # it switched itself off with an empty battery: rule_battery says so
    was = "walkie.offline" in i.was_open  # open: it stays open until the walkie is back
    if i.bench.suppressed(i.now) and not was:
        return None  # a planned outage or a bench run: no new alert inside it
    if w.since is None:
        start = i.logs_from or i.now
        sentence = f"The walkie hasn't connected at all since {when(start, i.now, i.tz)}."
    else:
        start = w.since
        sentence = f"The walkie hasn't connected since {when(start, i.now, i.tz)}."
    counted = i.bench.resume(start)  # time inside a test window doesn't count
    day = daytime_overlap(counted, i.now, i)
    if (
        not was
        and day < timedelta(minutes=i.th.offline_minutes)
        and i.now - counted < timedelta(days=i.th.not_seen_days)
    ):
        return None
    return Item(
        "walkie.offline",
        "Walkie offline",
        short(i.now - start),
        "Check charger",
        sentence,
        "He can't use it, and nothing reaches him until it's back.",
        "Check it's charged and switched on, or ask someone there to look at it.",
        "The walkie is back online.",
        start,
        "crit",
    )


def rule_audio_gaps(i: Inputs) -> Item | None:
    window = timedelta(hours=i.th.gap_window_hours)
    if i.bench.suppressed(i.now) and "audio.gaps" not in i.was_open:
        return None
    g = gaps_since(i.events, i.now - window, i.tz, i.walkie.conns, i.bench)
    enough = g.played_s >= i.th.gap_min_played_s
    pct = g.share * 100
    was = "audio.gaps" in i.was_open
    if not was and not (enough and pct >= i.th.gap_share_pct):
        return None
    if was and enough and pct < i.th.gap_share_pct * 0.6:
        return None  # clearly better: clear (below 60 % of the threshold)
    rssi = i.walkie.rssi
    if wifi_label(rssi) == "weak":
        action = f"Its Wi-Fi signal is weak ({rssi} dBm): check the router near him or move the walkie closer to it."
    elif rssi is not None:
        action = f"Its Wi-Fi signal looks fine ({rssi} dBm): check the internet at his end and at the box."
    else:
        action = "Check the Wi-Fi near him and the internet at the box."
    hours_label = f"{i.th.gap_window_hours:g} h"
    if enough:
        ratio = (
            f"about 1 minute in {round(1 / g.share)}" if g.share >= 0.02 else f"{pct:.1f} % of it"
        )
        sentence = f"Audio has been cutting up over the last {hours_label}: {ratio} was silence."
        big = f"{pct:.0f} %" if pct >= 10 else f"{pct:.1f} %"
    else:
        sentence = "Audio was cutting up, and nothing has played since to show it's better."
        big = "?"
    return Item(
        "audio.gaps",
        f"Audio gaps, {hours_label}",
        big,
        "Check Wi-Fi",
        sentence,
        "Books, music and answers break up for him.",
        action,
        "Audio is playing cleanly again.",
    )


def rule_skills(i: Inputs) -> list[Item]:
    app = current_app(i.events)
    failed: dict[str, Event | None] = {}
    for e in app:
        if e.name == "skill.setup_failed" and e.get("skill"):
            failed[str(e.get("skill"))] = e
        elif e.name == "huxley_app_ready":
            for name in e.get("disabled_skills") or []:
                failed.setdefault(str(name), None)
    items = []
    for name, fail in sorted(failed.items()):
        if name in OWN_RULE_SKILLS:
            continue
        error = str((fail.get("error") or fail.get("exception_class") or "") if fail else "")
        detail = f": {error[:120]}" if error else ""
        items.append(
            Item(
                f"skill.{name}",
                f"{name.capitalize()} off",
                short(i.now - fail.ts) if fail else "off",
                "Fix and restart",
                f"The {name} skill failed to start{detail}.",
                f"He can't use {name} until it starts; he hears an apology instead.",
                "Read skill.setup_failed in the server log, fix the cause, then restart the server.",
                f"The {name} skill is working again.",
                fail.ts if fail else None,
            )
        )
    return items


def rule_openai_down(i: Inputs) -> Item | None:
    failures: list[Event] = []
    for e in i.events:
        if e.name == "session_connected":
            failures = []
        elif e.name == "connection_failed":
            failures.append(e)
    if len(failures) < i.th.openai_failures:
        return None
    first = failures[0].ts
    return Item(
        "openai.down",
        "OpenAI failing",
        str(len(failures)),
        "Check internet",
        f"Huxley can't reach OpenAI: {len(failures)} failed tries since {when(first, i.now, i.tz)}.",
        "He hears beeps instead of answers.",
        "Check the internet at the box and status.openai.com; if it says credit, top up.",
        "Huxley is reaching OpenAI again.",
        first,
        "crit",
    )


def _refused(e: Event, codes: frozenset[str]) -> bool:
    """OpenAI refused for one of these codes: an error in the session, a
    refused answer (`session.response_refused`, from an error or a failed
    `response.done`), or a failed connect (its `refused` field, read from
    the response body since T1.43; older servers only had the traceback,
    which says "HTTP 429" without the code). An HTTP 429 alone is a rate
    limit: it counts as a failed connect (openai.down), not here."""
    if e.name in ("session.rx.error", "session.response_refused"):
        return str(e.get("code") or "") in codes
    if e.name == "connection_failed":
        refused = str(e.get("refused") or "")
        if refused:
            return refused in codes
        text = str(e.get("exception") or "")
        return any(code in text for code in codes)
    return False


def _last_refusal(i: Inputs, codes: frozenset[str], since: datetime | None = None) -> Event | None:
    """The last refusal not followed by a billed answer (which proves it
    works again), at or after `since`."""
    refusal: Event | None = None
    for e in i.events:
        if _refused(e, codes):
            refusal = e
        elif e.name == "cost.response_done":
            refusal = None
    if refusal is not None and since is not None and refusal.ts < since:
        return None
    return refusal


def rule_openai_quota(i: Inputs) -> Item | None:
    """Credit gone: the prepaid balance ran out (it can before the monthly
    limit does). Clears with a billed answer."""
    refusal = _last_refusal(i, CREDIT_CODES)
    if refusal is None:
        return None
    return Item(
        "openai.quota",
        "Credit gone",
        short(i.now - refusal.ts),
        "Top up",
        f"OpenAI refused to answer for lack of credit at {when(refusal.ts, i.now, i.tz)}.",
        "He hears beeps instead of answers until it's topped up.",
        "Add credit on platform.openai.com (Settings, Billing).",
        "OpenAI is answering again.",
        refusal.ts,
        "crit",
    )


def day_month(ts: datetime) -> str:
    """'1 Nov', in UTC: OpenAI's days and months."""
    utc = ts.astimezone(UTC)
    return f"{utc.day} {utc.strftime('%b')}"


def rule_openai_limit(i: Inputs) -> Item | None:
    """Budget hit: OpenAI refused because a monthly spend limit was
    reached. Clears with a billed answer (the limit was raised) or when the
    month turns (it reset)."""
    b = i.budget
    refusal = _last_refusal(i, LIMIT_CODES, b.start if b is not None else None)
    if refusal is None:
        return None
    resets = day_month(b.end) if b is not None else "the 1st"
    return Item(
        "openai.limit",
        "Budget hit",
        short(i.now - refusal.ts),
        "Raise limit or wait",
        f"OpenAI refused to answer at {when(refusal.ts, i.now, i.tz)}: the monthly spend limit is reached.",
        f"He hears beeps instead of answers until it resets on {resets} or the limit is raised.",
        "Raise the monthly limit on platform.openai.com (Settings, Limits), or wait for the reset.",
        "OpenAI is answering again.",
        refusal.ts,
        "crit",
    )


def rule_budget(i: Inputs) -> Item | None:
    """At this pace the month's budget runs out before it resets. Opens
    when the projection reaches 0 before the reset (the card's chip, from
    the same numbers); clears once it lasts 20 % past the reset."""
    b = i.budget
    if b is None or b.limit_usd is None or b.left_usd is None:
        return None
    days, to_reset = b.days_left, b.days_to_reset
    if days is None:
        return None
    margin = CLEAR_MARGIN if "openai.budget" in i.was_open else 1.0
    if days >= to_reset * margin:
        return None
    resets = day_month(b.end)
    if b.left_usd <= 0:
        big = "$0"
        sentence = (
            f"This month's OpenAI budget of {usd(b.limit_usd)} is used up; it resets on {resets}."
        )
    else:
        big = day_month(b.now + timedelta(days=days))
        sentence = (
            f"At this pace this month's OpenAI budget of {usd(b.limit_usd)} runs out on "
            f"{big}, before it resets on {resets}: {usd(b.left_usd)} left, about "
            f"{usd(b.pace_usd or 0)} a day."
        )
    return Item(
        "openai.budget",
        "Budget runs out",
        big,
        "Raise limit or wait",
        sentence,
        "Past the limit OpenAI refuses, and he hears beeps instead of answers until it resets.",
        "Raise the monthly limit on platform.openai.com (Settings, Limits), or let it reset.",
        "This month's OpenAI budget will last until it resets.",
    )


def rule_spend_today(i: Inputs) -> Item | None:
    """The server's cost canary or kill switch fired today (local day, so
    it clears at midnight here)."""
    today = i.now.astimezone(i.tz).date()
    canary: Event | None = None
    kill: Event | None = None
    for e in i.events:
        if e.ts.astimezone(i.tz).date() != today:
            continue
        tier = e.get("tier") if e.name == "cost.threshold_crossed" else None
        if e.name == "cost.kill_switch_triggered" or tier == "kill_switch":
            kill = kill or e
        elif tier == "bug_canary":
            canary = canary or e
    hit = kill or canary
    if hit is None:
        return None
    total = float(hit.get("day_total_usd") or hit.get("threshold_usd") or 0)
    if hit is kill:
        sentence = (
            f"OpenAI spend today hit the {usd(total)} safety stop and the server cut the session."
        )
    else:
        sentence = f"OpenAI spend today passed {usd(total)}, about 10 times a normal day."
    return Item(
        "openai.spend_today",
        "Spent today",
        money(max(total, spent_today(i))),
        "Check for loops",
        sentence,
        "Something may be repeating and spending credit.",
        "Look at today's log for a turn or tool call that keeps repeating.",
        "OpenAI spend is back to a normal day.",
        hit.ts,
    )


def rule_balance(i: Inputs) -> Item | None:
    """The balance you entered minus the spend since: only while no
    monthly limit is known (the budget rules say it better). One already
    open is judged to the end: dropping it would send a false "fine"."""
    # Credit and the monthly budget are separate stops: prepaid credit can
    # run out long before the month's limit (2026-10-12: $3.16 left of
    # credit, $27 of budget). So this rule runs whenever a balance is known.
    est = balance_estimate(i)
    if est is None or i.balance_usd is None:
        return None
    left, days, partial = est
    margin = CLEAR_MARGIN if "openai.balance" in i.was_open else 1.0
    if left > i.th.balance_low_usd * margin and (
        days is None or days > i.th.balance_low_days * margin
    ):
        return None
    if left <= 0:
        sentence = f"OpenAI credits have probably run out: {usd(i.balance_usd - left)} spent of the {usd(i.balance_usd)} you entered."
    elif days is not None:
        sentence = f"OpenAI credits: about {usd(left)} left, about {plural(max(0, round(days)), 'day')} at this pace."
    else:
        sentence = f"OpenAI credits: about {usd(left)} left."
    if partial and i.logs_from is not None:
        sentence += f" (The logs start {when(i.logs_from, i.now, i.tz)}, so it may be less.)"
    return Item(
        "openai.balance",
        "OpenAI credit" + (" (or less)" if partial else ""),
        money(max(0.0, left)),
        "Top up",
        sentence,
        "When they run out, he hears beeps instead of answers.",
        "Top up, then enter the new balance and the time you read it in watch.toml.",
        "OpenAI credits are fine again.",
    )


def rule_books(i: Inputs) -> Item | None:
    refusal = book_refusal(i)
    why = "When they run out, books stop for him until more are bought or the month turns."
    if refusal is not None:
        return Item(
            "books.hours",
            "Book hours left",
            "0 h",
            "Buy hours",
            f"Spotify stopped playing audiobooks at {when(refusal.ts, i.now, i.tz)}: the hours ran out.",
            why,
            "Buy more audiobook hours in Spotify.",
            "Audiobook time is fine again.",
            refusal.ts,
        )
    if i.book_hours_per_month is None or i.book_seconds is None:
        return None
    left = i.book_hours_per_month * 3600 - i.book_seconds
    margin = CLEAR_MARGIN if "books.hours" in i.was_open else 1.0
    if left > i.th.book_hours_low * 3600 * margin:
        return None
    sentence = (
        f"About {hours(left)} of audiobook time left until the hours renew."
        if left > 0
        else "This month's audiobook hours are used up."
    )
    return Item(
        "books.hours",
        "Book hours left",
        hours(max(0.0, left)),
        "Buy hours",
        sentence,
        why,
        "Buy more audiobook hours in Spotify.",
        "Audiobook time is fine again.",
    )


def rule_telegram(i: Inputs) -> Item | None:
    problem: Event | None = None
    for e in current_app(i.events):
        if (e.name == "skill.setup_failed" and e.get("skill") == "telegram") or (
            e.name == "telegram.credentials_missing"
        ):
            problem = e
        elif e.name == "telegram.transport.connected":
            problem = None
    if problem is None:
        return None
    if problem.name == "telegram.credentials_missing":
        sentence = "Telegram is off: the server has no Telegram credentials, so he can't call or get messages."
        action = (
            "Put the userbot's api_id, api_hash and phone in the persona's secrets, then restart."
        )
        chip = "Add credentials"
    else:
        error = str(problem.get("error") or problem.get("exception_class") or "")[:120]
        detail = f" ({error})" if error else ""
        sentence = f"Telegram didn't start{detail}, so he can't call or get messages."
        action = "Read skill.setup_failed in the server log; if the session was logged out, sign the userbot in again."
        chip = "Sign in again"
    return Item(
        "telegram.down",
        "Telegram down",
        short(i.now - problem.ts),
        chip,
        sentence,
        "Calls and messages from the family don't reach him.",
        action,
        "Telegram is working again.",
        problem.ts,
    )


def rule_spotify(i: Inputs) -> Item | None:
    app = current_app(i.events)
    failed = next(
        (
            e
            for e in reversed(app)
            if e.name == "skill.setup_failed" and e.get("skill") == "spotify"
        ),
        None,
    )
    why = "Music and audiobooks won't play for him."
    if failed is not None:
        error = str(failed.get("error") or failed.get("exception_class") or "")[:120]
        detail = f" ({error})" if error else ""
        return Item(
            "spotify.down",
            "Spotify down",
            short(i.now - failed.ts),
            "Fix and restart",
            f"Spotify didn't start{detail}.",
            why,
            "Read skill.setup_failed in the server log, fix the cause, then restart the server.",
            "Spotify is working again.",
            failed.ts,
        )
    down_since: datetime | None = None
    logged_out: Event | None = None
    told = False  # a refused request or the skill's warning, not just an answer
    reached = False  # this server process has reached the player at least once
    for e in app:
        if e.name == "spotify.ws_state":
            if e.get("connected"):
                down_since = None
                reached = True
            elif down_since is None:
                down_since = e.ts
        elif e.name == "spotify.auth_state":
            if e.get("logged_in"):
                logged_out, told = None, False
            else:
                logged_out = logged_out or e
        elif e.name in ("spotify.not_logged_in", "spotify.unavailable") and (
            e.name == "spotify.not_logged_in" or e.get("reason") == "player_not_paired"
        ):
            logged_out, told = logged_out or e, True
    # Open, and the server restarted without reaching the player since:
    # still down. Each reboot of a flickering power supply closed it
    # ("Spotify is working again") while Soloist was dead, and reopened it
    # minutes later (2026-10-13).
    held = not reached and "spotify.down" in i.was_open and (failed is None)
    if held and down_since is None:
        down_since = app[0].ts if app else i.now
    if down_since is not None and (
        held or i.now - down_since >= timedelta(minutes=i.th.spotify_down_minutes)
    ):
        return Item(
            "spotify.down",
            "Soloist down",
            short(i.now - down_since),
            "Restart Soloist",
            f"Spotify's player (Soloist) has been unreachable since {when(down_since, i.now, i.tz)}.",
            why,
            "Check the Soloist container on the box (docker ps) and restart it.",
            "Spotify is working again.",
            down_since,
        )
    # A logged-out answer is normal for the second a fresh Soloist takes to
    # log in; a refused request or the skill's own warning is not.
    # Open already: a moment unreachable (the container restarting after a
    # failed login) doesn't close it ("working again") only to reopen.
    if (
        (down_since is None or "spotify.down" in i.was_open)
        and logged_out is not None
        and (
            told
            or "spotify.down" in i.was_open
            or i.now - logged_out.ts >= timedelta(minutes=i.th.spotify_down_minutes)
        )
    ):
        return Item(
            "spotify.down",
            "Spotify logged out",
            short(i.now - logged_out.ts),
            "Restart Soloist",
            f"Spotify's player has been logged out since {when(logged_out.ts, i.now, i.tz)}.",
            why,
            "Restart it on the box (docker restart huxley-soloist; it restarts itself after a"
            " failed login). Still logged out after that: pair the Spotify account again.",
            "Spotify is working again.",
            logged_out.ts,
        )
    return None


def rule_disk(i: Inputs) -> Item | None:
    if i.disk_pct is None:
        return None
    threshold = i.th.disk_pct - (5.0 if "disk.full" in i.was_open else 0.0)
    if i.disk_pct < threshold:
        return None
    return Item(
        "disk.full",
        "Disk used",
        f"{i.disk_pct:.0f} %",
        "Free space",
        f"The box's disk is {i.disk_pct:.0f} % full.",
        "When it fills up, logs stop and the server can fail.",
        "Free some space on the box (old downloads, old logs moved elsewhere).",
        "The box's disk has room again.",
    )


BATTERY_STALE = timedelta(hours=2)  # older says nothing about now


def battery_cut_off(i: Inputs) -> bool:
    """Offline because the firmware's cutoff (F-0030) switched it off: the
    last reading carried `cutoff: true` and no connection began after it.
    A walkie that came back without a reading (lost, older firmware) and
    went away again is plain offline, not "empty" from an old cutoff."""
    b, w = i.battery, i.walkie
    if not b.cutoff or b.at is None or w.online:
        return False
    return not w.spans or w.spans[-1][0] <= b.at


def rule_battery(i: Inputs) -> Item | list[Item] | None:
    """Low battery, from the walkie's own readings (every 20 minutes, at
    each press and at each connect). Hysteresis: opens under low, clears
    only back at clear (a charge, not a wobble). A reading too old to judge
    keeps an open item open (no false "charged").

    Empty: the cutoff sends a last reading with `cutoff: true`, says so out
    loud and parks offline until a charger lifts it. Then "battery empty"
    opens (not "offline") and closes when the walkie is back, and "battery
    low" is held open under it: it may only clear at a real charge, never
    at the cutoff itself or at the reconnect, where the walkie comes back
    at about 10 % sitting on its charger."""
    b, key = i.battery, "battery.low"
    was = key in i.was_open
    if b.pct is None or b.at is None:
        return None

    def low(crit: bool) -> Item:
        return Item(
            key,
            "Battery low",
            f"{b.pct} %",
            "Charge it",
            f"The walkie's battery is at {b.pct} %.",
            "When it runs out he can't use it, and reminders and calls can't reach him.",
            "Ask someone there to plug it into its wall charger (never a computer).",
            "The walkie's battery is charged again.",
            since=b.at,
            severity="crit" if crit else "warn",
        )

    if battery_cut_off(i):
        items = [low(crit=True)]
        if not i.bench.suppressed(i.now) or "battery.empty" in i.was_open:
            items.insert(
                0,
                Item(
                    "battery.empty",
                    "Battery empty",
                    short(i.now - b.at),
                    "Charge it",
                    "The walkie switched itself off: its battery is empty.",
                    "He can't use it, and reminders and calls can't reach him until it's charged.",
                    "Ask someone there to plug it into its wall charger (never a computer).",
                    "The walkie is back online, charging.",
                    since=b.at,
                    severity="crit",
                ),
            )
        return items
    stale = i.now - b.at > BATTERY_STALE
    if stale and not was:
        return None
    limit = i.th.battery_clear_pct if was else i.th.battery_low_pct
    if b.pct >= limit:
        return None
    return low(crit=b.pct < i.th.battery_crit_pct)


def rule_alerts_failing(i: Inputs) -> Item | None:
    """Alerts that can't be delivered can't say so through Telegram: the
    page carries it."""
    if i.alert_failures < i.th.alert_failures:
        return None
    return Item(
        "watch.alerts_failing",
        "Alerts failing",
        str(i.alert_failures),
        "Check bot",
        f"The last {i.alert_failures} alert deliveries failed.",
        "You won't hear about new problems until they go through.",
        "Read watch.alert_failed in huxley-watch.log (Telegram says why) and fix the bot token or chat id.",
        "Alerts are getting through again.",
    )


Rule = Callable[[Inputs], "Item | list[Item] | None"]
# In order of how much they matter: the page lists items this way.
RULES: tuple[Rule, ...] = (
    rule_logs,
    rule_server_down,
    rule_walkie_offline,
    rule_battery,
    rule_openai_quota,
    rule_openai_limit,
    rule_openai_down,
    rule_server_restarts,
    rule_server_unclean,
    rule_budget,
    rule_balance,
    rule_spend_today,
    rule_audio_gaps,
    rule_spotify,
    rule_telegram,
    rule_books,
    rule_skills,
    rule_disk,
    rule_alerts_failing,
)


# Items about the running server's skills and session: with the server
# down they can't be judged, so none opens; one already open stays open
# (dropping it would send a false "working again").
SERVER_DEPENDENT = ("skill.", "openai.down", "telegram.down", "spotify.down")


def evaluate(i: Inputs) -> list[Item]:
    items: list[Item] = []
    for rule in RULES:
        out = rule(i)
        if out is None:
            continue
        items.extend(out if isinstance(out, list) else [out])
    ids = {item.id for item in items}

    def keep(item: Item, hidden_by: bool) -> bool:
        return not hidden_by or item.id in i.was_open

    if i.server.running is False:
        items = [it for it in items if keep(it, it.id.startswith(SERVER_DEPENDENT))]
    if "server.down" in ids:
        # With the server stopped nothing can connect: "check it's
        # charged" would send him the wrong way.
        items = [it for it in items if keep(it, it.id == "walkie.offline")]
    if "openai.quota" in ids or "openai.limit" in ids:
        # Refused for money says it better than "can't reach".
        items = [it for it in items if keep(it, it.id == "openai.down")]
    if "openai.limit" in ids:
        items = [it for it in items if keep(it, it.id == "openai.budget")]
    return items

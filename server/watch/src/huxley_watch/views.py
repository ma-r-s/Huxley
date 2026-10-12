"""The page's data: every number the page draws, as one JSON-ready dict.

`/status.json` serves exactly this, and page.py renders only from it, so
what the page shows and what the JSON says can't drift apart. Times on
the day strip are minutes since local midnight.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from huxley_watch.activity import KINDS, Segment
from huxley_watch.facts import _int, gaps_since, half_hours, wifi_bars
from huxley_watch.rules import balance_estimate, day_month, rule_books, spent_today
from huxley_watch.text import short, short_when

if TYPE_CHECKING:
    from collections.abc import Sequence
    from zoneinfo import ZoneInfo

    from huxley_watch.bench import Window
    from huxley_watch.rules import Inputs, Item

SCHEMA = 3  # 3: "credit", the month's OpenAI budget (T1.42)
WEEK_DAYS = 7
GAP_SLOTS = 24  # half hours on the gaps chart: the last 12 h
GAP_MIN_PLAYED_S = 30.0  # under this a half hour has nothing to say (dev.py link)


def _minutes(ts: datetime, midnight: datetime) -> float:
    return round((ts - midnight).total_seconds() / 60, 2)


def _clip(
    start: datetime, end: datetime, lo: datetime, hi: datetime
) -> tuple[datetime, datetime] | None:
    s, e = max(start, lo), min(end, hi)
    return (s, e) if e > s else None


def headline(items: Sequence[Item], testing_until: str | None = None) -> tuple[str, str]:
    """(status, headline): good / warn / crit / test, 'All good' / 'N need
    you' / 'Testing until HH:MM' (a test window is silencing alerts: not a
    green 'All good')."""
    if not items:
        if testing_until:
            return "test", f"Testing until {testing_until}"
        return "good", "All good"
    status = "crit" if any(i.severity == "crit" for i in items) else "warn"
    n = len(items)
    return status, ("1 needs you" if n == 1 else f"{n} need you")


def build(
    i: Inputs,
    items: Sequence[Item],
    segments: Sequence[Segment],
    presses: Sequence[datetime],
    *,
    alerts_channel: str,
    interval_s: float,
    archive: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tz, now = i.tz, i.now
    local_now = now.astimezone(tz)
    midnight = datetime.combine(local_now.date(), datetime.min.time(), tz)
    testing = (
        i.bench.resume(now).astimezone(tz).strftime("%H:%M") if i.bench.suppressed(now) else None
    )
    status, head = headline(items, testing)
    return {
        "schema": SCHEMA,
        "checked_at": now.isoformat(),
        "checked": local_now.strftime("%H:%M"),
        "timezone": tz.key,
        "interval_s": interval_s,
        "stale": False,
        "status": status,
        "headline": head,
        "items": [
            {
                "id": it.id,
                "label": it.label,
                "big": it.big,
                "chip": it.chip,
                "severity": it.severity,
                "since": it.since.isoformat() if it.since else None,
            }
            for it in items
        ],
        "tiles": _tiles(i),
        "today": _today(i, segments, midnight),
        "week": _week(i, segments, presses, midnight),
        "books": _books(i),
        "credit": credit(i, items),
        "gaps": _gaps(i),
        "battery_history": battery_history(i),
        "calls": _calls(segments, midnight - timedelta(days=WEEK_DAYS - 1)),
        "alerts": alerts_channel,
        "logs_from": short_when(i.logs_from, now, tz) if i.logs_from else None,
        "archive": archive,  # the day picker: today, the first day, the previous one
    }


def _out(i: Inputs) -> bool:
    item = rule_books(i)
    return item is not None and item.big == "0 h"


def _books(i: Inputs) -> dict[str, Any]:
    used, allowance = i.book_seconds, i.book_hours_per_month
    left = allowance * 3600 - used if allowance is not None and used is not None else None
    out: dict[str, Any] = {
        "month": i.now.astimezone(UTC).strftime("%B"),  # the skill counts UTC months
        "used_s": used,
        "allowance_h": allowance,
        "left_s": max(0.0, left) if left is not None else None,
        "renews": None,
        "partial": i.book_partial,
        "out": _out(i),
        "low_h": i.th.book_hours_low,
    }
    if i.book_cycle is not None:
        end = i.book_cycle[1].astimezone(i.tz)
        out["renews"] = f"{end.day} {end.strftime('%b')}"
        out["month"] = f"until {out['renews']}"
    return out


def _tiles(i: Inputs) -> dict[str, Any]:
    w, s, now, tz = i.walkie, i.server, i.now, i.tz
    g = gaps_since(i.events, now - timedelta(hours=i.th.gap_window_hours), tz, w.conns, i.bench)
    enough = g.played_s >= i.th.gap_min_played_s
    pct = round(g.share * 100, 1) if enough else None
    return {
        "walkie": {
            "online": w.online if w.ever_seen else None,
            "since": short_when(w.since, now, tz) if w.online and w.since else None,
            "last_seen": short_when(w.since, now, tz) if not w.online and w.since else None,
        },
        "wifi": {"rssi": w.rssi, "bars": wifi_bars(w.rssi)},
        "audio": {
            "gap_pct": pct,
            "played_s": round(g.played_s),
            "window_h": i.th.gap_window_hours,
            "threshold_pct": i.th.gap_share_pct,
            "over": pct is not None and pct >= i.th.gap_share_pct,
        },
        "server": {
            "running": s.running,
            "ready": i.health_ok,
            "up": short(now - s.started_at) if s.running and s.started_at else None,
            "firmware": w.version,
        },
        "battery": {
            "wired": i.battery.wired,
            "pct": i.battery.pct,
            "mv": i.battery.mv,
            "at": short_when(i.battery.at, now, tz) if i.battery.at else None,
            "stale": i.battery.at is not None and now - i.battery.at > timedelta(minutes=30),
            "low_pct": i.th.battery_low_pct,
            "crit_pct": i.th.battery_crit_pct,
        },
        "openai": _openai_tile(i),
    }


def _openai_tile(i: Inputs) -> dict[str, Any]:
    """Both limits that can stop the walkie: this month's budget left
    (when a limit is known) and the prepaid credit left (when a balance
    was read: the money actually in the account). The tile shows the one
    that runs out first; else today's spend."""
    b = i.budget
    known = b is not None and b.limit_usd is not None and b.left_usd is not None
    est = balance_estimate(i)
    return {
        "today_usd": round(spent_today(i), 2),
        "budget_left_usd": round(max(0.0, b.left_usd), 2)
        if known and b and b.left_usd is not None
        else None,
        "limit_usd": b.limit_usd if known and b else None,
        "budget_partial": bool(known and b and b.partial),
        "balance_left_usd": round(est[0], 2) if est else None,
        "days_left": round(est[1], 1) if est and est[1] is not None else None,
        "partial": bool(est and est[2]),
    }


def _money(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def credit(i: Inputs, items: Sequence[Item]) -> dict[str, Any]:
    """The OpenAI card: this month's budget (UTC month, OpenAI's), each
    day's spend and its source, the budget left after each day, and the
    projection at the pace to the reset. x is days since the 1st 00:00
    UTC; every number the card draws is here."""
    b = i.budget
    ids = {it.id for it in items}
    # The chip says what the card shows: the projection reaching 0 before
    # the reset (or a refusal for the limit); "Top up" when the credit is
    # gone. The alert keeps its own hysteresis.
    out = b is not None and b.left_usd is not None and (b.left_usd <= 0 or b.runs_out is not None)
    chip = (
        "Top up"
        if "openai.quota" in ids
        else "Raise limit or wait"
        if "openai.limit" in ids or out
        else None
    )
    if b is None:
        return {"mode": "spend", "chip": chip, "days": [], "line": [], "projection": []}
    limit = b.limit_usd
    n = (b.end - b.start).days
    now_x = round((b.now - b.start).total_seconds() / 86400, 3)
    days: list[dict[str, Any]] = []
    line: list[list[float]] = [[0.0, limit]] if limit is not None else []
    used = 0.0
    ran_out: str | None = None  # the day the spend passed the limit
    for k, d in enumerate(b.days):
        used += d.usd
        if limit is not None and ran_out is None and used >= limit:
            ran_out = f"{d.date.day} {d.date.strftime('%b')}"
        entry: dict[str, Any] = {
            "date": d.date.isoformat(),
            "label": f"{d.date.day} {d.date.strftime('%b')}",
            "x": k,
            "usd": round(d.usd, 4),
            "source": d.source,
        }
        if limit is not None:
            entry["left_usd"] = round(limit - used, 2)
            line.append([min(float(k + 1), now_x), round(limit - used, 4)])
        days.append(entry)
    projection: list[list[float]] = []
    ahead: list[dict[str, Any]] = []
    runs_out_x: float | None = None
    left = b.left_usd
    if limit is not None and left is not None and left > 0 and b.pace_usd:
        if b.runs_out is not None:
            runs_out_x = round((b.runs_out - b.start).total_seconds() / 86400, 3)
            projection = [[now_x, round(left, 4)], [runs_out_x, 0.0]]
        else:
            projection = [[now_x, round(left, 4)], [float(n), round(b.end_left_usd or 0.0, 4)]]
        for k in range(len(b.days), n):
            when_ = b.start + timedelta(days=k)
            ahead.append(
                {
                    "date": when_.date().isoformat(),
                    "label": f"{when_.day} {when_.strftime('%b')}",
                    "x": k,
                    "left_usd": round(max(0.0, left - b.pace_usd * (k + 1 - now_x)), 2),
                }
            )
    if limit is not None:
        mode = "budget"
    elif i.balance_usd is not None:
        mode = "balance"  # the entered balance: the tile and its rule only
    else:
        mode = "spend"
    est = balance_estimate(i)
    balance = {
        "balance_left_usd": round(est[0], 2) if est else None,
        "balance_days": round(est[1], 1) if est and est[1] is not None else None,
        "balance_partial": bool(est and est[2]),
    }
    return {
        **balance,
        "mode": mode,
        "month": b.start.strftime("%B"),
        "month_start": b.start.date().isoformat(),
        "days_in_month": n,
        "resets": day_month(b.end),
        "resets_at": b.end.isoformat(),
        "limit_usd": limit,
        "limit_source": b.limit_source,
        "project_id": b.project_id,
        "spent_usd": round(b.spent_usd, 2),
        "left_usd": _money(left),
        "partial": b.partial,
        "today_usd": round(b.today_usd, 2),
        "pace_usd": _money(b.pace_usd),
        "pace_days": b.pace_days,
        # At this pace, before the reset (only while some is left).
        "runs_out": day_month(b.runs_out) if runs_out_x is not None and b.runs_out else None,
        "runs_out_x": runs_out_x,
        "ran_out": ran_out,
        "end_left_usd": _money(b.end_left_usd),
        "now_x": now_x,
        "source": b.source,
        **_admin_state(i),
        "chip": chip,
        "line": line,
        "projection": projection,
        "days": days,
        "ahead": ahead,
    }


def _admin_state(i: Inputs) -> dict[str, Any]:
    """Whether the Admin API is read, and why it failed (by type only)."""
    a = i.admin
    return {
        "admin": a is not None,
        "billed_at": a.fetched_at.isoformat() if a is not None and a.fetched_at else None,
        "billed_error": a.costs_error if a is not None else None,
        "limit_error": a.limit_error if a is not None else None,
    }


def _today(i: Inputs, segments: Sequence[Segment], midnight: datetime) -> dict[str, Any]:
    return strip(midnight, i.now, segments, i.walkie.spans, i.bench.windows, today=True)


def strip(
    midnight: datetime,
    now: datetime,
    segments: Sequence[Segment],
    online: Sequence[tuple[datetime, datetime]],
    windows: Sequence[Window],
    *,
    today: bool,
) -> dict[str, Any]:
    """One day's strip: the walkie connected, him and Huxley, what played,
    the test windows; minutes since `midnight`. Today's has a "now"; the
    archive draws past days with the same function (dayview.py)."""
    end = midnight + timedelta(days=1)
    hi = min(end, now)

    def spans(pairs: Sequence[tuple[datetime, datetime]]) -> list[list[float]]:
        out = []
        for a, b in pairs:
            c = _clip(a, b, midnight, hi)
            if c:
                out.append([_minutes(c[0], midnight), _minutes(c[1], midnight)])
        return out

    media = []
    for seg in segments:
        if seg.kind not in KINDS:
            continue
        c = _clip(seg.start, seg.end, midnight, hi)
        if c:
            media.append(
                {
                    "kind": seg.kind,
                    "s": _minutes(c[0], midnight),
                    "e": _minutes(c[1], midnight),
                    "label": seg.label,
                }
            )
    bands = []
    for w in windows:
        c = _clip(w.start, w.end, midnight, end)
        if c:
            bands.append(
                {
                    "kind": w.kind,
                    "s": _minutes(c[0], midnight),
                    "e": _minutes(c[1], midnight),
                    "what": w.what,
                }
            )
    return {
        "date": midnight.date().isoformat(),
        "label": midnight.strftime("%a %d"),
        "now_min": _minutes(now, midnight) if today else None,
        "online": spans(online),
        "talk": spans([(s.start, s.end) for s in segments if s.kind == "talk"]),
        "answer": spans([(s.start, s.end) for s in segments if s.kind == "answer"]),
        "media": media,
        "windows": bands,
    }


def _week(
    i: Inputs, segments: Sequence[Segment], presses: Sequence[datetime], midnight: datetime
) -> dict[str, Any]:
    days: list[dict[str, Any]] = []
    top = 0.0
    for back in range(WEEK_DAYS - 1, -1, -1):
        lo = midnight - timedelta(days=back)
        hi = min(lo + timedelta(days=1), i.now)
        totals = dict.fromkeys(KINDS, 0.0)
        for seg in segments:
            if seg.kind in totals:
                c = _clip(seg.start, seg.end, lo, hi)
                if c:
                    totals[seg.kind] += (c[1] - c[0]).total_seconds()
        days.append(
            {
                "date": lo.date().isoformat(),
                "label": lo.strftime("%a %d"),
                "has_logs": i.logs_from is not None and lo + timedelta(days=1) > i.logs_from,
                "hours": {k: round(v / 3600, 2) for k, v in totals.items()},
                "presses": sum(1 for p in presses if lo <= p < hi),
            }
        )
        top = max(top, sum(totals.values()) / 3600)
    return {"days": days, "max_h": max(1, math.ceil(top))}


def _gaps(i: Inputs) -> dict[str, Any]:
    local = i.now.astimezone(i.tz)
    current = local.replace(minute=0 if local.minute < 30 else 30, second=0, microsecond=0)
    first = current - timedelta(minutes=30 * (GAP_SLOTS - 1))
    buckets = {b.start: b for b in half_hours(i.events, first, i.tz, i.walkie.conns, i.bench)}
    slots = []
    for n in range(GAP_SLOTS):
        start = first + timedelta(minutes=30 * n)
        b = buckets.get(start)
        played = b.played_s if b else 0.0
        slots.append(
            {
                "start": start.strftime("%H:%M"),
                "played_s": round(played),
                "gap_pct": round(b.share * 100, 1) if b and played >= GAP_MIN_PLAYED_S else None,
            }
        )
    return {"threshold_pct": i.th.gap_share_pct, "slots": slots}


def _calls(segments: Sequence[Segment], since: datetime) -> dict[str, Any]:
    counts: dict[str, int] = {}
    seen: set[tuple[str, datetime | None]] = set()
    for seg in segments:
        if seg.kind == "call" and seg.start >= since and (seg.label, seg.origin) not in seen:
            seen.add((seg.label, seg.origin))  # a call cut in two by a bench window is one call
            name = (seg.label or "?").strip().title()
            counts[name] = counts.get(name, 0) + 1
    people = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "total": sum(counts.values()),
        "days": WEEK_DAYS,
        "people": [{"name": n, "count": c} for n, c in people],
    }


def mark_stale(status: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The same data, flagged stale when the last check is older than about
    three intervals: the loop stopped, and "All good" would be a lie."""
    try:
        checked = datetime.fromisoformat(str(status["checked_at"]))
        interval = float(status.get("interval_s") or 300)
    except (KeyError, ValueError):
        return status
    if now - checked <= timedelta(seconds=3 * interval + 60):
        return status
    return {**status, "stale": True}


def fallback_status(
    items: Sequence[Item], now: datetime, tz: ZoneInfo, interval_s: float, error: str
) -> dict[str, Any]:
    """When the full page can't be built: the header and the cards still
    show, and the JSON says what broke."""
    status, head = headline(items)
    return {
        "schema": SCHEMA,
        "error": error,
        "checked_at": now.isoformat(),
        "checked": now.astimezone(tz).strftime("%H:%M"),
        "timezone": tz.key,
        "interval_s": interval_s,
        "stale": False,
        "status": status,
        "headline": head,
        "items": [
            {
                "id": it.id,
                "label": it.label,
                "big": it.big,
                "chip": it.chip,
                "severity": it.severity,
                "since": it.since.isoformat() if it.since else None,
            }
            for it in items
        ],
    }


BATTERY_WINDOW = timedelta(hours=24)
BATTERY_GAP = timedelta(minutes=15)  # longer between readings: the walkie was off


def battery_history(i: Inputs) -> dict[str, Any]:
    """The walkie's battery over the last 24 h, from its own readings
    (client_event system.battery, about once a minute while connected).
    Points carry minutes since the window's start; a gap of more than 15
    minutes between readings breaks the line (the walkie was off or away)."""
    start = i.now - BATTERY_WINDOW
    raw: list[tuple[datetime, int, int | None]] = []
    for e in i.events:
        if e.name != "client.system.battery" or e.ts < start:
            continue
        pct = _int(e.get("pct"))
        if pct is None:
            continue
        raw.append((e.ts, max(0, min(100, pct)), _int(e.get("mv"))))
    # One point per 5 minutes is plenty for a day (the last reading wins).
    buckets: dict[int, tuple[datetime, int, int | None]] = {}
    for ts, pct, mv in raw:
        buckets[int((ts - start).total_seconds() // 300)] = (ts, pct, mv)
    points: list[dict[str, Any]] = []
    prev: datetime | None = None
    for key in sorted(buckets):
        ts, pct, mv = buckets[key]
        points.append(
            {
                "m": round((ts - start).total_seconds() / 60, 1),
                "pct": pct,
                "mv": mv,
                "at": ts.astimezone(i.tz).strftime("%H:%M"),
                "gap_before": prev is not None and ts - prev > BATTERY_GAP,
            }
        )
        prev = ts
    first_hour = (start.astimezone(i.tz) + timedelta(hours=1)).replace(
        minute=0, second=0, microsecond=0
    )
    marks = []
    t = first_hour
    while t < i.now:
        if t.hour % 6 == 0:
            marks.append(
                {"m": round((t - start).total_seconds() / 60, 1), "label": t.strftime("%H:%M")}
            )
        t += timedelta(hours=1)
    return {
        "window_min": int(BATTERY_WINDOW.total_seconds() // 60),
        "points": points,
        "marks": marks,
        "low_pct": i.th.battery_low_pct,
        "crit_pct": i.th.battery_crit_pct,
        "wired": i.battery.wired,
    }

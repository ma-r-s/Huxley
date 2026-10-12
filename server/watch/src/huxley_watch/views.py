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
from huxley_watch.facts import gaps_since, half_hours, wifi_bars
from huxley_watch.rules import balance_estimate, spent_today
from huxley_watch.text import short, short_when

if TYPE_CHECKING:
    from collections.abc import Sequence
    from zoneinfo import ZoneInfo

    from huxley_watch.bench import Window
    from huxley_watch.rules import Inputs, Item

SCHEMA = 2
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
        "books": {
            "month": now.astimezone(UTC).strftime("%B"),  # the skill counts UTC months
            "used_s": i.book_seconds,
            "allowance_h": i.book_hours_per_month,
        },
        "gaps": _gaps(i),
        "calls": _calls(segments, midnight - timedelta(days=WEEK_DAYS - 1)),
        "alerts": alerts_channel,
        "logs_from": short_when(i.logs_from, now, tz) if i.logs_from else None,
        "archive": archive,  # the day picker: today, the first day, the previous one
    }


def _tiles(i: Inputs) -> dict[str, Any]:
    w, s, now, tz = i.walkie, i.server, i.now, i.tz
    g = gaps_since(i.events, now - timedelta(hours=i.th.gap_window_hours), tz, w.conns, i.bench)
    enough = g.played_s >= i.th.gap_min_played_s
    pct = round(g.share * 100, 1) if enough else None
    est = balance_estimate(i)
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
        "openai": {
            "today_usd": round(spent_today(i), 2),
            "balance_left_usd": round(est[0], 2) if est else None,
            "days_left": round(est[1], 1) if est and est[1] is not None else None,
            "partial": bool(est and est[2]),
        },
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

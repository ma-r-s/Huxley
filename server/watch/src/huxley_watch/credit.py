"""OpenAI spend against the month's budget: the battery on the page.

The battery is this month's budget left: the monthly hard spend limit
minus what was spent since the 1st. OpenAI's limits reset with its monthly
cycle; its docs name no zone, so a month here is a calendar month in UTC,
and a day a UTC day, the same 1-day buckets its Costs API bills in.

- The spend: what OpenAI billed (the Costs API, through an admin key, see
  openai_admin.py) for every UTC day it has settled; a day not yet
  settled is the larger of billed-plus-estimate and the estimate; every
  day when there is no admin key or the read fails, the server's own
  estimate (the `cost_usd` of each `cost.response_done`). Each day says
  which.
- The limit: the organization's (or a configured project's) enforced
  spend limit read from the Admin API; if neither can be read, `[openai]
  monthly_limit_usd` from the config, as the organization's. With both an
  organization and a project limit, the one with less left (it bites
  first).
- The pace: the average of the last 7 finished days that had spend (days
  of nothing don't dilute it, so it errs toward "runs out sooner"); today
  joins once it is already over that average.

Everything here is pure: events, the Admin API's cached answer and the
config in, numbers out. rules.py and views.py use the same numbers, so the
card and the alert can't disagree.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from huxley_watch.events import Event

DAY = timedelta(days=1)
PACE_DAYS = 7  # the pace averages this many finished days that had spend
HISTORY_DAYS = 60  # days of spend kept: the month, and the pace before it
# A billed day is taken as settled this long after it ends: the Costs API
# reports late, so a read just after midnight can be short.
BILLING_LAG = timedelta(hours=6)


@dataclass(frozen=True)
class Bucket:
    """One UTC day of what OpenAI billed, in USD: the organization's
    total, and each project's."""

    start: datetime
    end: datetime
    total: float
    projects: tuple[tuple[str, float], ...] = ()

    def usd(self, project_id: str | None) -> float:
        if project_id is None:
            return self.total
        return sum(v for p, v in self.projects if p == project_id)


@dataclass(frozen=True)
class Admin:
    """What the Admin API said at its last read (cached in the state dir).
    A failed read keeps the last good answer and says why it failed."""

    fetched_at: datetime | None = None  # the last good costs read
    buckets: tuple[Bucket, ...] = ()
    org_limit_usd: float | None = None  # enforced, monthly; None: none or never read
    project_limit_usd: float | None = None
    costs_error: str | None = None  # the last attempt's failure, by type ("HTTP 404")
    limit_error: str | None = None


@dataclass(frozen=True)
class Day:
    date: date
    usd: float
    source: str | None  # billed | estimate | mixed (billed so far + estimate since); None: no data


@dataclass(frozen=True)
class Budget:
    start: datetime  # the 1st, 00:00 UTC
    end: datetime  # the next 1st: the reset
    days: tuple[Day, ...]  # each UTC day of the month up to today
    spent_usd: float
    today_usd: float
    limit_usd: float | None
    limit_source: str | None  # org | project | config
    left_usd: float | None
    pace_usd: float | None
    pace_days: int
    runs_out: datetime | None  # at this pace, before the reset (None: it lasts)
    end_left_usd: float | None  # at this pace, left at the reset
    partial: bool  # days of the month with no data: spent at least this
    source: str  # billed | estimate | mixed, over the month's days
    now: datetime
    project_id: str | None = None  # whose spend, when it's a project's

    @property
    def days_left(self) -> float | None:
        """At this pace (None: no limit, or nothing spent to judge by)."""
        if self.left_usd is None or not self.pace_usd:
            return None
        return max(0.0, self.left_usd) / self.pace_usd

    @property
    def days_to_reset(self) -> float:
        return (self.end - self.now).total_seconds() / 86400


# --- the Admin API's answers ------------------------------------------------


class ShapeError(ValueError):
    """An Admin API answer that isn't the documented shape."""


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ShapeError(f"not a number: {type(value).__name__}")
    try:
        return float(value)
    except ValueError as exc:
        raise ShapeError("not a number") from exc


def parse_costs(page: dict[str, Any]) -> tuple[list[Bucket], str | None]:
    """One page of GET /v1/organization/costs: (its buckets, the next
    page's cursor or None). Results in another currency than USD are
    refused rather than summed."""
    data = page.get("data")
    if page.get("object") != "page" or not isinstance(data, list):
        raise ShapeError("not a page of buckets")
    buckets = []
    for raw in data:
        if not isinstance(raw, dict):
            raise ShapeError("bucket not an object")
        start = datetime.fromtimestamp(_number(raw.get("start_time")), UTC)
        end = datetime.fromtimestamp(_number(raw.get("end_time")), UTC)
        total = 0.0
        projects: dict[str, float] = {}
        for result in raw.get("results") or []:
            amount = result.get("amount") if isinstance(result, dict) else None
            if not isinstance(amount, dict) or amount.get("value") is None:
                continue
            currency = str(amount.get("currency") or "usd").lower()
            if currency != "usd":
                raise ShapeError(f"currency {currency}")
            value = _number(amount["value"])
            total += value
            project = result.get("project_id")
            if project:
                projects[str(project)] = projects.get(str(project), 0.0) + value
        buckets.append(Bucket(start, end, total, tuple(sorted(projects.items()))))
    cursor = page.get("next_page") if page.get("has_more") else None
    return buckets, (str(cursor) if cursor else None)


def parse_limit(obj: dict[str, Any]) -> float | None:
    """GET /v1/organization/spend_limit (or a project's): the monthly
    limit in USD while it is enforced, else None. `threshold_amount` is in
    cents."""
    if obj.get("object") not in ("organization.spend_limit", "project.spend_limit"):
        raise ShapeError("not a spend limit")
    enforcement = obj.get("enforcement")
    status = enforcement.get("status") if isinstance(enforcement, dict) else None
    if status != "enforcing":
        return None
    if str(obj.get("currency") or "USD").upper() != "USD" or obj.get("interval") != "month":
        raise ShapeError("not a monthly USD limit")
    cents = _number(obj.get("threshold_amount"))
    return cents / 100 if cents > 0 else None


# --- spend per day -------------------------------------------------------------


def month_start(now: datetime) -> datetime:
    utc = now.astimezone(UTC)
    return datetime(utc.year, utc.month, 1, tzinfo=UTC)


def next_month(start: datetime) -> datetime:
    return datetime(start.year + start.month // 12, start.month % 12 + 1, 1, tzinfo=UTC)


def day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def cost_points(events: Sequence[Event]) -> tuple[list[datetime], list[float]]:
    """The server's estimate: each billed answer's time and cost, in time
    order, with a running total (index n: the spend before point n)."""
    pts = sorted(
        (e.ts, float(e.get("cost_usd") or 0)) for e in events if e.name == "cost.response_done"
    )
    times = [t for t, _ in pts]
    running = [0.0]
    for _, usd in pts:
        running.append(running[-1] + usd)
    return times, running


def _logged(times: list[datetime], running: list[float], lo: datetime, hi: datetime) -> float:
    """The estimate's spend in [lo, hi)."""
    if hi <= lo:
        return 0.0
    return running[bisect_left(times, hi)] - running[bisect_left(times, lo)]


def daily_spend(
    events: Sequence[Event],
    now: datetime,
    *,
    admin: Admin | None,
    project_id: str | None,
    logs_from: datetime | None,
    days_back: int = HISTORY_DAYS,
) -> list[Day]:
    """Each UTC day from `days_back - 1` days ago to today: billed where
    OpenAI has settled it (BILLING_LAG after its end, at the read), else
    the server's estimate. A day read before it settled (today, or one
    that just ended) is the larger of billed-at-the-read plus the estimate
    since, and the day's whole estimate: billing reports late, and neither
    is ever more than was spent. A day the logs don't reach and nothing
    billed is no data."""
    times, running = cost_points(events)
    today = now.astimezone(UTC).date()
    read = admin.fetched_at if admin is not None else None
    final: dict[date, Bucket] = {}
    early: dict[date, Bucket] = {}
    if admin is not None and read is not None:
        for b in admin.buckets:
            day = b.start.astimezone(UTC).date()
            if day_start(day) + DAY + BILLING_LAG <= read:
                final[day] = b
            elif b.start < read:
                early[day] = b
    out = []
    for back in range(days_back - 1, -1, -1):
        d = today - timedelta(days=back)
        lo, hi = day_start(d), min(day_start(d) + DAY, now)
        if d in final:
            out.append(Day(d, final[d].usd(project_id), "billed"))
            continue
        whole = _logged(times, running, lo, hi)
        if d in early and read is not None:
            billed = early[d].usd(project_id) + _logged(times, running, max(lo, read), hi)
            usd = max(billed, whole)
            out.append(Day(d, usd, "billed" if usd == early[d].usd(project_id) else "mixed"))
        elif logs_from is not None and logs_from < hi:
            out.append(Day(d, whole, "estimate"))
        else:
            out.append(Day(d, 0.0, None))
    return out


def pace(days: Sequence[Day], today: date) -> tuple[float | None, int]:
    """(USD a day, days averaged): the mean of the last PACE_DAYS finished
    days that had spend (a day of nothing, or one the logs don't reach,
    doesn't dilute it). Today, unfinished, counts only once it is already
    over that mean, or when no finished day had spend: either way the pace
    never goes down for a day that has barely begun."""
    finished = [d.usd for d in sorted(days, key=lambda d: d.date, reverse=True) if d.date < today]
    spent = [usd for usd in finished if usd > 0][:PACE_DAYS]
    now_day = next((d.usd for d in days if d.date == today), 0.0)
    mean = sum(spent) / len(spent) if spent else 0.0
    if now_day > mean:
        spent = [now_day, *spent][:PACE_DAYS]
    if not spent:
        return None, 0
    return sum(spent) / len(spent), len(spent)


def _source(days: Sequence[Day]) -> str:
    kinds = {d.source for d in days if d.source is not None}
    if kinds == {"billed"}:
        return "billed"
    if kinds <= {"estimate"}:
        return "estimate"
    return "mixed"


# --- the month's budget ---------------------------------------------------------


def budget(
    events: Sequence[Event],
    now: datetime,
    *,
    admin: Admin | None = None,
    project_id: str | None = None,
    config_limit: float | None = None,
    logs_from: datetime | None = None,
) -> Budget:
    """This month's budget: the limit (read, else configured), what was
    spent since the 1st, and where this pace takes it before the reset."""
    start = month_start(now)
    end = next_month(start)
    today = now.astimezone(UTC).date()

    def for_scope(scope: str | None) -> list[Day]:
        return daily_spend(events, now, admin=admin, project_id=scope, logs_from=logs_from)

    candidates: list[tuple[str, float, str | None]] = []
    if admin is not None and admin.org_limit_usd:
        candidates.append(("org", admin.org_limit_usd, None))
    if admin is not None and project_id and admin.project_limit_usd:
        candidates.append(("project", admin.project_limit_usd, project_id))
    if not candidates and config_limit:
        # The organization's: against its whole spend (with a project set,
        # the larger figure: the card never shows more left than there is).
        candidates.append(("config", config_limit, None))

    chosen: tuple[str | None, float | None, str | None] = (None, None, project_id)
    days = for_scope(project_id)
    best_left: float | None = None
    for name, cap, scope_id in candidates:
        scoped = for_scope(scope_id)
        used = sum(d.usd for d in scoped if d.date >= start.date())
        if best_left is None or cap - used < best_left:
            best_left, chosen, days = cap - used, (name, cap, scope_id), scoped

    source_name, limit, scope = chosen
    month = tuple(d for d in days if d.date >= start.date())
    spent = sum(d.usd for d in month)
    today_usd = next((d.usd for d in month if d.date == today), 0.0)
    per_day, n = pace(days, today)
    left = limit - spent if limit is not None else None
    runs_out: datetime | None = None
    end_left: float | None = None
    if left is not None and per_day:
        to_reset = (end - now).total_seconds() / 86400
        end_left = left - per_day * to_reset
        if left <= 0:
            runs_out = now
        elif left / per_day < to_reset:
            runs_out = now + timedelta(days=left / per_day)
    partial = any(
        d.source in (None, "estimate") and (logs_from is None or logs_from > day_start(d.date))
        for d in month
    )
    return Budget(
        start=start,
        end=end,
        days=month,
        spent_usd=spent,
        today_usd=today_usd,
        limit_usd=limit,
        limit_source=source_name,
        left_usd=left,
        pace_usd=per_day,
        pace_days=n,
        runs_out=runs_out,
        end_left_usd=end_left,
        partial=partial,
        source=_source(month),
        now=now,
        project_id=scope,
    )

"""The OpenAI card: this month's budget (the monthly limit minus the spend
since the 1st, UTC), its sources (OpenAI billed through the Admin API, else
the server's estimate), the projection, the rules that share its numbers,
and the admin key staying secret. No network: the Admin API's answers are
written by hand from the API reference's shapes (2026-10)."""

from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from huxley_watch.config import load_config
from huxley_watch.credit import (
    Admin,
    Bucket,
    ShapeError,
    daily_spend,
    parse_costs,
    parse_limit,
)
from huxley_watch.cycle import Watcher
from huxley_watch.events import make_events
from huxley_watch.openai_admin import REFRESH, AdminReader, fetch_costs, fetch_limit
from huxley_watch.page import render
from huxley_watch.rules import (
    evaluate,
    rule_balance,
    rule_budget,
    rule_openai_limit,
    rule_openai_quota,
)
from huxley_watch.wlog import WatchLog

from .logs import NOW, bench, connection_failed, cost, flat, inputs, line, starting
from .test_alerts import FakeSender
from .test_cycle import setup
from .test_page import status_for, visible_text

if TYPE_CHECKING:
    from pathlib import Path

KEY = "sk-admin-TESTKEY0123456789-must-never-leak"
UTC_DAY = timedelta(days=1)


def at(day: int, hour: float = 12.0, month: int = 10) -> datetime:
    return datetime(2026, month, day, tzinfo=UTC) + timedelta(hours=hour)


# A month of the server's estimate. NOW is 12 Oct 20:00 UTC.
BOOT = starting(at(20, month=9))
SPEND = flat(
    BOOT,
    cost(at(30, 23, month=9), 4.0, 4.0),  # last month: not this month's spend, but in the pace
    cost(at(1, 9), 5.0, 5.0),
    cost(at(5, 9), 3.0, 3.0),
    cost(at(10, 9), 2.0, 2.0),
    cost(at(12, 9), 1.0, 1.0),
)


# --- the month's budget from the server's estimate ----------------------------


def test_spend_per_utc_day_from_the_cost_lines() -> None:
    lines = flat(BOOT, cost(at(11, 23.9), 0.5, 0.5), cost(at(12, 0.1), 0.25, 0.75))
    days = {
        d.date: d
        for d in daily_spend(
            make_events(lines), NOW, admin=None, project_id=None, logs_from=at(20, month=9)
        )
    }
    # 23:54 and 00:06 UTC fall on two UTC days (OpenAI's), whatever the local day.
    assert days[date(2026, 10, 11)].usd == 0.5
    assert days[date(2026, 10, 12)].usd == 0.25
    assert days[date(2026, 10, 12)].source == "estimate"
    assert days[date(2026, 9, 19)].source is None  # before the logs: no data


def test_budget_left_pace_and_the_day_it_runs_out() -> None:
    i = inputs(SPEND, monthly_limit_usd=30.0)
    b = i.budget
    assert b is not None
    assert b.limit_usd == 30.0 and b.limit_source == "config"
    assert b.spent_usd == pytest.approx(11.0)  # 5 + 3 + 2 + 1; September's 4 isn't October's
    assert b.left_usd == pytest.approx(19.0)
    # The last finished days that had spend (zero days skipped); today's 1
    # is under their mean, so it doesn't pull the pace down.
    assert b.pace_usd == pytest.approx((2 + 3 + 5 + 4) / 4)
    assert b.pace_days == 4
    # 19 left at 3.5 a day: 5.4 days, before the reset in 19.2.
    assert b.runs_out == NOW + timedelta(days=19 / 3.5)
    assert b.end.date() == date(2026, 11, 1)
    assert b.partial is False  # the logs reach back past the 1st
    assert b.source == "estimate"


def test_a_budget_that_lasts_has_no_run_out() -> None:
    b = inputs(SPEND, monthly_limit_usd=100.0).budget
    assert b is not None
    assert b.runs_out is None
    assert b.end_left_usd == pytest.approx(89.0 - 3.5 * (19 + 4 / 24))


def test_the_month_boundary_is_utc() -> None:
    """31 Oct 23:59:59 UTC is October's; 00:00:01 is November's, though in
    Bogota both are the evening of the 31st."""
    lines = flat(
        BOOT,
        cost(at(31, 23 + 59 / 60 + 59 / 3600), 10.0, 10.0),
        cost(at(1, 1 / 3600, 11), 0.5, 0.5),
    )
    now = at(1, 2, 11)
    b = inputs(lines, now=now, monthly_limit_usd=30.0).budget
    assert b is not None
    assert b.start == datetime(2026, 11, 1, tzinfo=UTC)
    assert b.spent_usd == pytest.approx(0.5)
    assert b.left_usd == pytest.approx(29.5)
    assert [d.date for d in b.days] == [date(2026, 11, 1)]
    assert b.pace_usd == pytest.approx(10.0)  # the pace reaches back into October
    status = status_for(lines, now=now, monthly_limit_usd=30.0)
    assert status["credit"]["resets"] == "1 Dec"
    assert status["credit"]["days_in_month"] == 30
    # One second earlier it was still October, with 10 of 30 gone.
    before = inputs(lines, now=at(31, 23.9999), monthly_limit_usd=30.0).budget
    assert before is not None
    assert before.spent_usd == pytest.approx(10.0)


def test_bench_windows_do_not_exclude_spend() -> None:
    """Tests cost real money: a bench window hides presses, never spend."""
    window = bench((at(12, 8), at(12, 10), "bench.py run"))
    b = inputs(SPEND, monthly_limit_usd=30.0, bench=window).budget
    assert b is not None
    assert b.spent_usd == pytest.approx(11.0)


def test_logs_that_start_after_the_first_say_the_spend_is_at_least() -> None:
    lines = flat(starting(at(10, 0)), cost(at(10, 9), 2.0, 2.0))
    b = inputs(lines, monthly_limit_usd=30.0).budget
    assert b is not None
    assert b.partial is True
    status = status_for(lines, monthly_limit_usd=30.0)
    assert ">or less<" in render(status)


def test_no_limit_shows_the_month_spend_and_no_battery() -> None:
    status = status_for(SPEND)
    c = status["credit"]
    assert c["mode"] == "spend"
    assert c["limit_usd"] is None and c["left_usd"] is None
    page = render(status)
    assert "limit not set" in page
    assert 'class="plot cbat"' not in page
    assert 'class="plot cspend"' in page


# --- OpenAI billed, through the Admin API -------------------------------------


def bucket_json(d: date, by_project: dict[str | None, float]) -> dict[str, Any]:
    start = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())
    return {
        "object": "bucket",
        "start_time": start,
        "end_time": start + 86400,
        "results": [
            {
                "object": "organization.costs.result",
                "amount": {"value": usd, "currency": "usd"},
                "line_item": None,
                "project_id": project,
                "organization_id": "org-abc",
            }
            for project, usd in by_project.items()
        ],
    }


def costs_page(buckets: list[dict[str, Any]], next_page: str | None = None) -> dict[str, Any]:
    return {
        "object": "page",
        "data": buckets,
        "has_more": next_page is not None,
        "next_page": next_page,
    }


def limit_json(
    cents: int, status: str = "enforcing", obj: str = "organization.spend_limit"
) -> dict[str, Any]:
    return {
        "object": obj,
        "threshold_amount": cents,
        "currency": "USD",
        "interval": "month",
        "enforcement": {"status": status},
    }


def test_parse_costs_sums_projects_and_reads_the_cursor() -> None:
    page = costs_page(
        [bucket_json(date(2026, 10, 11), {"proj_huxley": 1.25, "proj_other": 0.5, None: 0.25})],
        next_page="page_abc",
    )
    buckets, cursor = parse_costs(page)
    assert cursor == "page_abc"
    (b,) = buckets
    assert b.start == datetime(2026, 10, 11, tzinfo=UTC)
    assert b.total == pytest.approx(2.0)
    assert b.usd("proj_huxley") == pytest.approx(1.25)
    assert b.usd(None) == pytest.approx(2.0)
    with pytest.raises(ShapeError):
        parse_costs({"object": "list", "data": []})
    euro = bucket_json(date(2026, 10, 11), {None: 1.0})
    euro["results"][0]["amount"]["currency"] = "eur"
    with pytest.raises(ShapeError):
        parse_costs(costs_page([euro]))


def test_parse_limit_is_dollars_while_enforced() -> None:
    assert parse_limit(limit_json(3000)) == 30.0  # cents
    assert parse_limit(limit_json(0, "inactive")) is None
    assert parse_limit(limit_json(10000, obj="project.spend_limit")) == 100.0
    with pytest.raises(ShapeError):
        parse_limit({"object": "page"})


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class FakeAPI:
    """The Admin API's answers by path; an Exception is raised instead."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float = 0) -> FakeResponse:
        self.requests.append(request)
        url = urllib.parse.urlsplit(request.full_url)
        query = dict(urllib.parse.parse_qsl(url.query))
        key = url.path + (f"?page={query['page']}" if "page" in query else "")
        answer = self.answers[key]
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(json.dumps(answer).encode())


def http_error(code: int) -> urllib.error.HTTPError:
    # The message quotes the key, as a careless proxy might: it must not reach the log.
    return urllib.error.HTTPError(f"https://x/?{KEY}", code, f"Not Found {KEY}", None, None)  # type: ignore[arg-type]


COSTS = "/v1/organization/costs"
ORG_LIMIT = "/v1/organization/spend_limit"
PROJECT_LIMIT = "/v1/organization/projects/proj_huxley/spend_limit"


def test_fetch_costs_asks_for_60_utc_days_by_project_and_follows_pages() -> None:
    api = FakeAPI(
        {
            COSTS: costs_page([bucket_json(date(2026, 10, 10), {"proj_huxley": 1.0})], "p2"),
            COSTS + "?page=p2": costs_page(
                [bucket_json(date(2026, 10, 11), {"proj_huxley": 2.0})]
            ),
        }
    )
    buckets = fetch_costs(KEY, NOW, api)
    assert [b.total for b in buckets] == [1.0, 2.0]
    first = api.requests[0]
    assert first.get_header("Authorization") == f"Bearer {KEY}"
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(first.full_url).query)
    assert query["start_time"] == [str(int(datetime(2026, 8, 14, tzinfo=UTC).timestamp()))]
    assert query["bucket_width"] == ["1d"]
    assert query["limit"] == ["60"]
    assert query["group_by[]"] == ["project_id"]
    assert KEY not in first.full_url


def test_fetch_limit_org_and_project() -> None:
    api = FakeAPI(
        {ORG_LIMIT: limit_json(3000), PROJECT_LIMIT: limit_json(1500, obj="project.spend_limit")}
    )
    assert fetch_limit(KEY, None, api) == 30.0
    assert fetch_limit(KEY, "proj_huxley", api) == 15.0


def admin_with(days: dict[date, float], fetched_at: datetime = NOW, **kw: Any) -> Admin:
    """As the Costs API answers: a bucket for every day of the 60 up to the
    read, 0 where nothing was billed."""
    last = fetched_at.date()
    buckets = []
    for back in range(59, -1, -1):
        d = last - timedelta(days=back)
        start = datetime(d.year, d.month, d.day, tzinfo=UTC)
        usd = days.get(d, 0.0)
        buckets.append(Bucket(start, start + UTC_DAY, usd, (("proj_huxley", usd),) if usd else ()))
    return Admin(fetched_at=fetched_at, buckets=tuple(buckets), **kw)


def test_billed_days_replace_the_estimate_and_today_adds_the_estimate_since() -> None:
    admin = admin_with(
        {date(2026, 10, 1): 6.0, date(2026, 10, 5): 3.5, date(2026, 10, 12): 0.4},
        fetched_at=at(12, 8),
    )
    b = inputs(SPEND, monthly_limit_usd=30.0, admin=admin).budget
    assert b is not None
    days = {d.date: d for d in b.days}
    assert (days[date(2026, 10, 1)].usd, days[date(2026, 10, 1)].source) == (6.0, "billed")
    # A finished day with nothing billed is billed 0, whatever the logs guessed.
    assert (days[date(2026, 10, 10)].usd, days[date(2026, 10, 10)].source) == (0.0, "billed")
    # Today: 0.4 billed by 08:00, then the server's 1.0 at 09:00.
    assert days[date(2026, 10, 12)].usd == pytest.approx(1.4)
    assert days[date(2026, 10, 12)].source == "mixed"
    assert b.spent_usd == pytest.approx(6.0 + 3.5 + 1.4)
    assert b.source == "mixed"


def test_days_after_the_last_good_read_fall_back_to_the_estimate() -> None:
    admin = admin_with({date(2026, 10, 1): 6.0}, fetched_at=at(2, 1), costs_error="HTTP 404")
    b = inputs(SPEND, monthly_limit_usd=30.0, admin=admin).budget
    assert b is not None
    days = {d.date: d for d in b.days}
    assert days[date(2026, 10, 1)].source == "billed"
    assert days[date(2026, 10, 5)].source == "estimate"
    assert b.spent_usd == pytest.approx(6.0 + 3 + 2 + 1)


def test_a_limit_read_from_the_api_wins_over_the_config() -> None:
    b = inputs(SPEND, monthly_limit_usd=30.0, admin=admin_with({}, org_limit_usd=50.0)).budget
    assert b is not None
    assert (b.limit_usd, b.limit_source) == (50.0, "org")
    # No limit enforced on the account: the configured one.
    b = inputs(SPEND, monthly_limit_usd=30.0, admin=admin_with({})).budget
    assert b is not None
    assert (b.limit_usd, b.limit_source) == (30.0, "config")


def test_a_project_limit_counts_that_project_and_the_tighter_one_wins() -> None:
    org_day = Bucket(
        at(11, 0), at(11, 0) + UTC_DAY, 20.0, (("proj_huxley", 2.0), ("proj_other", 18.0))
    )
    admin = Admin(fetched_at=NOW, buckets=(org_day,), org_limit_usd=100.0, project_limit_usd=10.0)
    b = inputs(SPEND, admin=admin, openai_project_id="proj_huxley").budget
    assert b is not None
    # org: 100 - 20 = 80 left; project: 10 - (2 + the estimate) = less: it bites first.
    assert b.limit_source == "project"
    assert b.project_id == "proj_huxley"
    assert b.limit_usd == 10.0


# --- the reader: hourly, cached, never fatal ------------------------------------


def good_api() -> FakeAPI:
    return FakeAPI(
        {
            COSTS: costs_page([bucket_json(date(2026, 10, 11), {"proj_huxley": 2.0})]),
            ORG_LIMIT: limit_json(3000),
        }
    )


def test_the_reader_reads_hourly_and_caches_on_disk(tmp_path: Path) -> None:
    log = WatchLog(None)
    api = good_api()
    reader = AdminReader(tmp_path / "openai_admin.json", log, api)
    admin = reader.read(KEY, None, NOW)
    assert admin is not None
    assert admin.org_limit_usd == 30.0
    assert [b.total for b in admin.buckets] == [2.0]
    assert len(api.requests) == 2
    reader.read(KEY, None, NOW + timedelta(minutes=30))
    assert len(api.requests) == 2  # within the hour: the cache
    # A restart reads the cache, not the API.
    again = AdminReader(tmp_path / "openai_admin.json", log, api).read(
        KEY, None, NOW + timedelta(minutes=40)
    )
    assert again is not None and again.org_limit_usd == 30.0
    assert len(api.requests) == 2
    reader.read(KEY, None, NOW + REFRESH)
    assert len(api.requests) == 4
    assert log.events("watch.openai_admin_read")[0]["org_limit_usd"] == 30.0
    assert reader.read(None, None, NOW) is None  # no key: nothing read


@pytest.mark.parametrize(
    "failure",
    [http_error(404), http_error(401), urllib.error.URLError("no route"), TimeoutError()],
)
def test_a_failed_read_keeps_the_last_good_answer_and_logs_one_line(
    tmp_path: Path, failure: Exception
) -> None:
    log = WatchLog(None)
    path = tmp_path / "openai_admin.json"
    AdminReader(path, log, good_api()).read(KEY, None, NOW)
    broken = FakeAPI({COSTS: failure, ORG_LIMIT: failure})
    admin = AdminReader(path, log, broken).read(KEY, None, NOW + REFRESH)
    assert admin is not None
    assert [b.total for b in admin.buckets] == [2.0]  # the last good costs
    assert admin.org_limit_usd == 30.0  # and the last good limit
    expected = (
        f"HTTP {failure.code}"
        if isinstance(failure, urllib.error.HTTPError)
        else type(failure).__name__
    )
    assert admin.costs_error == expected
    (failed,) = log.events("watch.openai_costs_failed")
    assert failed["error"] == expected
    assert KEY not in json.dumps(log.records, default=str)


def test_a_404_from_the_start_falls_back_to_the_estimate_and_the_config(tmp_path: Path) -> None:
    log = WatchLog(None)
    api = FakeAPI({COSTS: http_error(404), ORG_LIMIT: http_error(404)})
    admin = AdminReader(tmp_path / "a.json", log, api).read(KEY, None, NOW)
    assert admin is not None and admin.buckets == ()
    b = inputs(SPEND, monthly_limit_usd=30.0, admin=admin).budget
    assert b is not None
    assert (b.limit_source, b.source) == ("config", "estimate")
    assert b.spent_usd == pytest.approx(11.0)


def test_an_odd_answer_is_a_shape_error_not_a_crash(tmp_path: Path) -> None:
    log = WatchLog(None)
    api = FakeAPI({COSTS: {"object": "page", "data": "nope"}, ORG_LIMIT: ["not", "an", "object"]})
    admin = AdminReader(tmp_path / "a.json", log, api).read(KEY, None, NOW)
    assert admin is not None
    assert admin.costs_error == "ShapeError"
    assert admin.limit_error == "org ShapeError"


# --- the whole cycle: the key never leaves secrets.toml ----------------------------


def test_the_admin_key_is_never_logged_served_or_cached(tmp_path: Path) -> None:
    cfg_path = setup(tmp_path, SPEND, tables="[openai]\nmonthly_limit_usd = 30\n")
    secrets = tmp_path / "secrets.toml"
    secrets.write_text(f'openai_admin_key = "{KEY}"\n')
    secrets.chmod(0o600)
    cfg = load_config(cfg_path)
    assert cfg.openai_admin_key == KEY
    assert KEY not in repr(cfg)
    log = WatchLog(cfg.log_file)
    w = Watcher(
        cfg_path, cfg, log, sender=FakeSender(), pinger=lambda url: None, admin_opener=good_api()
    )
    status = w.cycle(NOW)
    assert status["credit"]["limit_source"] == "org"
    assert status["credit"]["admin"] is True
    # An hour later the API fails, quoting the key in its error.
    w.admin_reader.opener = FakeAPI({COSTS: http_error(404), ORG_LIMIT: http_error(403)})
    status = w.cycle(NOW + REFRESH)
    assert status["credit"]["billed_error"] == "HTTP 404"
    state = tmp_path / "state"
    for name in (
        "huxley-watch.log",
        "status.json",
        "index.html",
        "openai_admin.json",
        "alerts.json",
    ):
        assert KEY not in (state / name).read_text(), name
    assert KEY not in json.dumps(status)
    _, body = w.current(NOW + REFRESH)
    assert KEY.encode() not in body


# --- the rules share the card's numbers ---------------------------------------------


def test_budget_item_opens_when_the_projection_hits_zero_before_the_reset() -> None:
    item = rule_budget(inputs(SPEND, monthly_limit_usd=30.0))
    assert item is not None
    assert (item.id, item.label, item.big, item.chip) == (
        "openai.budget",
        "Budget runs out",
        "18 Oct",
        "Raise limit or wait",
    )
    assert rule_budget(inputs(SPEND, monthly_limit_usd=100.0)) is None
    # Hysteresis: open, it clears only once the money lasts 20 % past the reset.
    # 19.2 days to the reset at 3.5 a day: 67.1 more than the 11 spent. A
    # limit of 75 runs out first; 80 lasts, not 20 % past (80.5 more); 95 does.
    was = frozenset({"openai.budget"})
    assert rule_budget(inputs(SPEND, monthly_limit_usd=75.0)) is not None
    assert rule_budget(inputs(SPEND, monthly_limit_usd=80.0)) is None
    assert rule_budget(inputs(SPEND, monthly_limit_usd=80.0, was_open=was)) is not None
    assert rule_budget(inputs(SPEND, monthly_limit_usd=95.0, was_open=was)) is None


def test_the_card_chip_is_the_budget_item() -> None:
    tight = status_for(SPEND, monthly_limit_usd=30.0)
    assert tight["credit"]["chip"] == "Raise limit or wait"
    assert tight["credit"]["runs_out"] == "18 Oct"
    assert '<span class="chip">Raise limit or wait</span>' in render(tight)
    roomy = status_for(SPEND, monthly_limit_usd=100.0)
    assert roomy["credit"]["chip"] is None
    assert 'class="chip' not in render(roomy)
    # Inside the alert's hysteresis band the item stays open, but the card
    # shows what the projection shows: it lasts, no chip, no $0 mark.
    band = status_for(SPEND, monthly_limit_usd=80.0, was_open=frozenset({"openai.budget"}))
    assert "openai.budget" in [x["id"] for x in band["items"]]
    assert band["credit"]["chip"] is None
    assert band["credit"]["runs_out"] is None


def refusal(ts: datetime, code: str) -> str:
    return line(
        "session.rx.error", ts, level="error", code=code, message="refused", error_type="requests"
    )


def test_budget_hit_from_a_spend_limit_refusal_clears_with_the_month() -> None:
    lines = flat(BOOT, refusal(at(31, 23), "project_spend_limit_exceeded"))
    item = rule_openai_limit(inputs(lines, now=at(31, 23.5)))
    assert item is not None
    assert (item.label, item.chip, item.severity) == ("Budget hit", "Raise limit or wait", "crit")
    assert "1 Nov" in item.why
    assert rule_openai_limit(inputs(lines, now=at(1, 0.2, 11))) is None  # it reset
    # A billed answer after it: the limit was raised.
    raised = flat(lines, cost(at(31, 23.2), 0.01, 0.01))
    assert rule_openai_limit(inputs(raised, now=at(31, 23.5))) is None
    # From a failed connect's traceback too.
    connect = flat(
        BOOT,
        connection_failed(
            at(12, 19),
            "openai.RateLimitError: Error code: 429 - organization_spend_limit_exceeded",
        ),
    )
    assert rule_openai_limit(inputs(connect)) is not None


def test_credit_gone_is_its_own_item() -> None:
    lines = flat(BOOT, refusal(at(12, 19), "credit_balance_exhausted"))
    item = rule_openai_quota(inputs(lines, monthly_limit_usd=30.0))
    assert item is not None
    assert (item.id, item.label, item.chip) == ("openai.quota", "Credit gone", "Top up")
    assert rule_openai_limit(inputs(lines)) is None  # credit, not the limit
    status = status_for(lines, monthly_limit_usd=30.0)
    assert status["credit"]["chip"] == "Top up"


def test_refused_for_money_hides_can_t_reach_and_budget_hit_hides_runs_out() -> None:
    fails = [
        connection_failed(
            at(12, 19) + timedelta(minutes=m),
            "openai.RateLimitError: Error code: 429 - project_spend_limit_exceeded",
        )
        for m in range(3)
    ]
    found = {x.id for x in evaluate(inputs(flat(SPEND, fails), monthly_limit_usd=30.0))}
    assert "openai.limit" in found
    assert "openai.down" not in found
    assert "openai.budget" not in found


def test_credit_counts_even_with_a_monthly_limit() -> None:
    """Prepaid credit and the month's budget are separate stops: $3.16 of
    credit ran out long before a $30 budget would (2026-10-12)."""
    kw: dict[str, Any] = {"balance_usd": 5.0, "balance_at": at(1, 0)}
    assert rule_balance(inputs(SPEND, **kw)) is not None
    assert rule_balance(inputs(SPEND, monthly_limit_usd=30.0, **kw)) is not None


def test_the_card_and_tile_show_the_credit_when_it_runs_out_first() -> None:
    kw: dict[str, Any] = {"balance_usd": 5.0, "balance_at": at(1, 0), "monthly_limit_usd": 30.0}
    status = status_for(SPEND, **kw)
    c = status["credit"]
    assert c["balance_left_usd"] is not None and c["balance_left_usd"] < c["left_usd"]
    t = status["tiles"]["openai"]
    assert t["balance_left_usd"] == c["balance_left_usd"]
    assert t["budget_left_usd"] == c["left_usd"]
    page = render(status)
    assert "credit left" in page
    assert ">OpenAI credit<" in page  # the tile leads with what runs out first


def test_without_a_balance_the_tile_shows_the_budget() -> None:
    status = status_for(SPEND, monthly_limit_usd=30.0)
    assert status["credit"]["balance_left_usd"] is None
    assert "credit left" not in render(status)


# --- the card: numbers, a battery, bars, no sentences --------------------------------


def test_status_json_carries_every_number_the_card_draws() -> None:
    c = status_for(SPEND, monthly_limit_usd=30.0)["credit"]
    assert c["mode"] == "budget"
    assert (c["limit_usd"], c["spent_usd"], c["left_usd"]) == (30.0, 11.0, 19.0)
    assert (c["pace_usd"], c["pace_days"]) == (3.5, 4)
    assert c["days_in_month"] == 31 and c["month"] == "October"
    assert len(c["days"]) == 12  # 1-12 Oct
    first = c["days"][0]
    assert first == {
        "date": "2026-10-01",
        "label": "1 Oct",
        "x": 0,
        "usd": 5.0,
        "source": "estimate",
        "left_usd": 25.0,
    }
    assert c["line"][0] == [0.0, 30.0]
    assert c["line"][-1] == [c["now_x"], 19.0]
    assert c["projection"] == [[c["now_x"], 19.0], [c["runs_out_x"], 0.0]]
    assert c["ahead"][0]["date"] == "2026-10-13"


def test_the_card_renders_numbers_a_battery_and_bars_with_tips() -> None:
    status = status_for(SPEND, monthly_limit_usd=30.0)
    page = render(status)
    card = page.split('id="credit">', 1)[1].split("</section>", 1)[0]
    assert '<div class="cv">$19.00</div>' in card
    assert "of $30, resets 1 Nov" in card
    assert 'class="cline"' in card and 'class="proj"' in card
    assert "$0 on 18 Oct" in card
    bars = re.findall(r'<div class="cs" data-tip="([^"]+)">', card)
    assert bars[0] == "1 Oct: $5.00 spent, server estimate"
    assert "12 Oct: $19.00 left, $1.00 spent, server estimate" in card
    # One series a chart: no legend box.
    assert 'class="legend"' not in card
    for text in visible_text(page):
        assert len(text.split()) <= 5, text


def test_the_tile_shows_the_budget_left() -> None:
    status = status_for(SPEND, monthly_limit_usd=30.0)
    assert status["tiles"]["openai"]["budget_left_usd"] == 19.0
    page = render(status)
    assert '<div class="val">$19.00</div><div class="sub">of $30 this month</div>' in page


def test_once_prints_the_budget() -> None:
    from huxley_watch.__main__ import once_summary

    text = once_summary(status_for(SPEND, monthly_limit_usd=30.0))
    assert (
        "openai October: $19.00 of $30 left, $11.00 used (config limit, estimate), hits $0 18 Oct"
        in text
    )
    assert "openai October: $11.00 used, no limit set" in once_summary(status_for(SPEND))


# --- critic round 1 (fresh agent on d21465e9): bugs (failed before the fix) and gaps


def test_a_failing_project_limit_keeps_the_org_limit_just_read(tmp_path: Path) -> None:
    api = FakeAPI(
        {
            COSTS: costs_page([bucket_json(date(2026, 10, 11), {"proj_huxley": 2.0})]),
            ORG_LIMIT: limit_json(3000),
            PROJECT_LIMIT: http_error(404),
        }
    )
    log = WatchLog(None)
    admin = AdminReader(tmp_path / "a.json", log, api).read(KEY, "proj_huxley", NOW)
    assert admin is not None
    assert admin.org_limit_usd == 30.0
    assert admin.limit_error == "project HTTP 404"
    # Costs fine, a limit failing: no costs-failed line, the error rides the read line.
    assert not log.events("watch.openai_costs_failed")
    assert log.events("watch.openai_admin_read")[0]["limit_error"] == "project HTTP 404"


@pytest.mark.parametrize(
    "cache",
    [
        "[]",
        "{not json",
        json.dumps(
            {
                "attempted_at": "2026-10-12T19:30:00",
                "fetched_at": "2026-10-12T19:30:00",
                "buckets": [],
            }
        ),
        json.dumps({"attempted_at": "2026-10-12T19:30:00+00:00", "buckets": [["x", 1, 2, {}]]}),
    ],
)
def test_a_damaged_or_naive_cache_never_breaks_a_cycle(tmp_path: Path, cache: str) -> None:
    path = tmp_path / "openai_admin.json"
    path.write_text(cache)
    log = WatchLog(None)
    admin = AdminReader(path, log, good_api()).read(KEY, None, NOW)
    assert admin is not None
    b = inputs(SPEND, monthly_limit_usd=30.0, admin=admin).budget  # comparisons don't raise
    assert b is not None


def test_a_cached_time_without_a_zone_is_read_as_utc(tmp_path: Path) -> None:
    path = tmp_path / "openai_admin.json"
    path.write_text(
        json.dumps(
            {
                "attempted_at": "2026-10-12T19:30:00",
                "fetched_at": "2026-10-12T19:30:00",
                "buckets": [],
            }
        )
    )
    api = good_api()
    admin = AdminReader(path, WatchLog(None), api).read(KEY, None, NOW)
    assert api.requests == []  # 30 min old: still fresh
    assert admin is not None
    assert admin.fetched_at == datetime(2026, 10, 12, 19, 30, tzinfo=UTC)


def test_a_read_in_the_future_or_another_project_reads_again(tmp_path: Path) -> None:
    path = tmp_path / "openai_admin.json"
    api = good_api()
    reader = AdminReader(path, WatchLog(None), api)
    reader.read(KEY, None, NOW + timedelta(hours=5))  # a clock that was ahead
    assert len(api.requests) == 2
    reader.read(KEY, None, NOW)
    assert len(api.requests) == 4  # attempted "in the future": not fresh
    api.answers[PROJECT_LIMIT] = limit_json(1000, obj="project.spend_limit")
    admin = reader.read(KEY, "proj_huxley", NOW + timedelta(minutes=1))
    assert len(api.requests) == 7  # a new project: costs, org, project
    assert admin is not None and admin.project_limit_usd == 10.0


def test_the_configured_limit_is_the_organization_s() -> None:
    """With a project set and the limits unreadable, the config's limit is
    compared with the whole organization's spend: never more left shown
    than OpenAI will allow."""
    org_day = Bucket(
        at(11, 0),
        at(11, 0) + UTC_DAY,
        25.0,
        (("proj_huxley", 1.0), ("proj_other", 24.0)),
    )
    admin = Admin(fetched_at=NOW, buckets=(org_day,), limit_error="org HTTP 404")
    b = inputs(SPEND, monthly_limit_usd=30.0, admin=admin, openai_project_id="proj_huxley").budget
    assert b is not None
    assert b.limit_source == "config" and b.project_id is None
    assert b.days[-2].usd == 25.0  # 11 Oct, billed for the whole organization


def test_a_project_limit_counts_only_that_project_s_spend() -> None:
    org_day = Bucket(
        at(11, 0),
        at(11, 0) + UTC_DAY,
        20.0,
        (("proj_huxley", 2.0), ("proj_other", 18.0)),
    )
    admin = Admin(fetched_at=NOW, buckets=(org_day,), org_limit_usd=100.0, project_limit_usd=10.0)
    b = inputs(flat(BOOT), admin=admin, openai_project_id="proj_huxley").budget
    assert b is not None
    assert b.spent_usd == pytest.approx(2.0)
    assert b.left_usd == pytest.approx(8.0)  # 10 - 2; the org would have 80 left


def test_the_key_never_follows_a_redirect() -> None:
    api = FakeAPI({ORG_LIMIT: limit_json(3000)})
    fetch_limit(KEY, None, api)
    (request,) = api.requests
    assert "Authorization" not in request.headers  # urllib copies these onto a redirect
    assert request.unredirected_hdrs["Authorization"] == f"Bearer {KEY}"


def test_a_day_that_has_barely_begun_does_not_lower_the_pace() -> None:
    days = flat(BOOT, *[cost(at(d, 9), 3.0, 3.0) for d in range(5, 12)])
    early = inputs(flat(days, cost(at(12, 0.1), 0.05, 0.05)), now=at(12, 0.15)).budget
    assert early is not None and early.pace_usd == pytest.approx(3.0)
    heavy = inputs(flat(days, cost(at(12, 9), 10.0, 10.0)), now=at(12, 10)).budget
    assert heavy is not None
    assert heavy.pace_usd == pytest.approx((10 + 6 * 3.0) / 7)  # over the mean: it counts


def test_a_day_read_just_after_midnight_is_not_settled() -> None:
    """Billing reports late: at 00:30 yesterday's bucket may be short. Until
    6 h after its end it is the larger of billed and the estimate."""
    lines = flat(BOOT, cost(at(11, 22), 4.0, 4.0))
    admin = admin_with({date(2026, 10, 11): 1.0}, fetched_at=at(12, 0.5))
    b = inputs(lines, now=at(12, 1), admin=admin).budget
    assert b is not None
    day = next(d for d in b.days if d.date == date(2026, 10, 11))
    assert (day.usd, day.source) == (4.0, "mixed")
    settled = admin_with({date(2026, 10, 11): 1.0}, fetched_at=at(12, 7))
    b = inputs(lines, now=at(12, 7.5), admin=settled).budget
    assert b is not None
    day = next(d for d in b.days if d.date == date(2026, 10, 11))
    assert (day.usd, day.source) == (1.0, "billed")


def test_money_already_gone_shows_the_day_it_hit_zero() -> None:
    status = status_for(SPEND, monthly_limit_usd=7.0)  # 5 on the 1st, 3 on the 5th
    c = status["credit"]
    assert c["left_usd"] < 0
    assert c["runs_out"] is None and c["projection"] == []
    assert c["ran_out"] == "5 Oct"
    assert c["chip"] == "Raise limit or wait"
    card = render(status).split('id="credit">', 1)[1]
    assert '<div class="cv">5 Oct</div><div class="cc">hit $0</div>' in card
    assert "left at reset" not in card


@pytest.mark.parametrize("value", ["inf", "nan", "0", "-5"])
def test_a_limit_that_isnt_a_positive_number_is_refused(tmp_path: Path, value: str) -> None:
    from huxley_watch.config import ConfigError

    cfg = setup(tmp_path, SPEND, tables=f"[openai]\nmonthly_limit_usd = {value}\n")
    with pytest.raises(ConfigError):
        load_config(cfg)


def test_an_open_balance_item_is_judged_to_the_end_when_a_limit_appears() -> None:
    kw: dict[str, Any] = {"balance_usd": 5.0, "balance_at": at(1, 0)}
    was = frozenset({"openai.balance"})
    # Still low: stays open (no false "fine"), though a limit is now known.
    assert rule_balance(inputs(SPEND, monthly_limit_usd=30.0, was_open=was, **kw)) is not None


def test_a_budget_that_cant_be_computed_costs_only_the_card() -> None:
    naive = Admin(
        fetched_at=datetime(2026, 10, 12, 19),
        buckets=(Bucket(at(11, 0), at(11, 0) + UTC_DAY, 1.0),),
    )
    i = inputs(SPEND, monthly_limit_usd=30.0, admin=naive)
    assert i.budget is None and i.budget_error == "TypeError"
    status = status_for(SPEND, monthly_limit_usd=30.0, admin=naive)
    assert status["credit"]["mode"] == "spend"
    assert "OpenAI budget" in render(status)

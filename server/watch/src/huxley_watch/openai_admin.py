"""OpenAI's Admin API, read at most once an hour with an admin key
(`openai_admin_key` in secrets.toml, an `sk-admin-...` key from
Organization settings > Admin keys).

- GET /v1/organization/costs: what OpenAI billed, 1-day buckets (UTC),
  the last 60 days, grouped by project.
- GET /v1/organization/spend_limit: the organization's monthly hard limit
  (cents; only while `enforcement.status` is "enforcing").
- GET /v1/organization/projects/{id}/spend_limit: a project's, when
  `[openai] project_id` is set.

The answer is cached in `<state_dir>/openai_admin.json` (never the key),
so a restart doesn't read again within the hour. A failed read (HTTP 404,
401, a timeout, an odd answer) never stops a cycle: the last good answer
stays, the page falls back to the server's estimate for days it doesn't
cover and to `monthly_limit_usd` for the limit, and the attempt logs one
line with the error's type, never its text (it could quote the key).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from huxley_watch.credit import (
    HISTORY_DAYS,
    Admin,
    Bucket,
    ShapeError,
    day_start,
    parse_costs,
    parse_limit,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from huxley_watch.wlog import WatchLog

API = "https://api.openai.com/v1"
REFRESH = timedelta(hours=1)
MAX_PAGES = 5
TIMEOUT_S = 20.0


def describe(exc: BaseException) -> str:
    """An error by its type ("HTTP 404", "URLError", "ShapeError"): the
    text can quote a URL or a key."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return type(exc).__name__


def get_json(url: str, key: str, opener: Callable[..., Any], timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}
    )
    with opener(request, timeout=timeout_s) as resp:
        body = resp.read(8_000_000)
    obj = json.loads(body)
    if not isinstance(obj, dict):
        raise ShapeError("not an object")
    return obj


def fetch_costs(
    key: str,
    now: datetime,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout_s: float = TIMEOUT_S,
) -> list[Bucket]:
    """The last HISTORY_DAYS UTC days of billed costs, by project."""
    start = day_start(now.astimezone(UTC).date()) - timedelta(days=HISTORY_DAYS - 1)
    params: list[tuple[str, str]] = [
        ("start_time", str(int(start.timestamp()))),
        ("bucket_width", "1d"),
        ("limit", str(HISTORY_DAYS)),
        ("group_by[]", "project_id"),
    ]
    buckets: list[Bucket] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        query = params + ([("page", cursor)] if cursor else [])
        page = get_json(
            f"{API}/organization/costs?{urllib.parse.urlencode(query)}", key, opener, timeout_s
        )
        got, cursor = parse_costs(page)
        buckets.extend(got)
        if cursor is None:
            break
    return buckets


def fetch_limit(
    key: str,
    project_id: str | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout_s: float = TIMEOUT_S,
) -> float | None:
    """The enforced monthly limit in USD (None: no limit enforced)."""
    path = (
        f"/organization/projects/{urllib.parse.quote(project_id, safe='')}/spend_limit"
        if project_id
        else "/organization/spend_limit"
    )
    return parse_limit(get_json(f"{API}{path}", key, opener, timeout_s))


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _ts(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _dump(admin: Admin, attempted: datetime, project_id: str | None) -> dict[str, Any]:
    return {
        "attempted_at": attempted.isoformat(),
        "project_id": project_id,
        "fetched_at": _iso(admin.fetched_at),
        "org_limit_usd": admin.org_limit_usd,
        "project_limit_usd": admin.project_limit_usd,
        "costs_error": admin.costs_error,
        "limit_error": admin.limit_error,
        "buckets": [
            [int(b.start.timestamp()), int(b.end.timestamp()), b.total, dict(b.projects)]
            for b in admin.buckets
        ],
    }


def _load(raw: dict[str, Any]) -> tuple[Admin, datetime | None, str | None]:
    buckets = tuple(
        Bucket(
            datetime.fromtimestamp(s, UTC),
            datetime.fromtimestamp(e, UTC),
            float(total),
            tuple(sorted((str(p), float(v)) for p, v in (projects or {}).items())),
        )
        for s, e, total, projects in raw.get("buckets") or []
    )

    def money(key: str) -> float | None:
        value = raw.get(key)
        return float(value) if isinstance(value, int | float) and value > 0 else None

    admin = Admin(
        fetched_at=_ts(raw.get("fetched_at")),
        buckets=buckets,
        org_limit_usd=money("org_limit_usd"),
        project_limit_usd=money("project_limit_usd"),
        costs_error=raw.get("costs_error") or None,
        limit_error=raw.get("limit_error") or None,
    )
    project = raw.get("project_id")
    return admin, _ts(raw.get("attempted_at")), str(project) if project else None


class AdminReader:
    """Reads the Admin API when its answer is an hour old (or the project
    changed), keeps the last good answer, and caches it on disk."""

    def __init__(
        self,
        path: Path,
        log: WatchLog,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.path = path
        self.log = log
        self.opener = opener
        self._admin: Admin | None = None
        self._attempted: datetime | None = None
        self._project: str | None = None
        self._loaded = False

    def _restore(self) -> None:
        self._loaded = True
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._admin, self._attempted, self._project = _load(raw)
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.log.warning("watch.openai_cache_unreadable", error=type(exc).__name__)

    def read(self, key: str | None, project_id: str | None, now: datetime) -> Admin | None:
        """The Admin API's answer for this cycle (None without a key)."""
        if key is None:
            return None
        if not self._loaded:
            self._restore()
        fresh = (
            self._attempted is not None
            and now - self._attempted < REFRESH
            and self._attempted <= now
            and self._project == project_id
        )
        if fresh and self._admin is not None:
            return self._admin
        return self._refresh(key, project_id, now)

    def _refresh(self, key: str, project_id: str | None, now: datetime) -> Admin:
        started = time.monotonic()
        old = self._admin or Admin()
        if self._project != project_id:
            old = replace(old, project_limit_usd=None)
        admin = old
        try:
            buckets = fetch_costs(key, now, self.opener)
            admin = replace(admin, fetched_at=now, buckets=tuple(buckets), costs_error=None)
        except Exception as exc:  # any failure: the estimate takes over
            admin = replace(admin, costs_error=describe(exc))
        try:
            org = fetch_limit(key, None, self.opener)
            proj = fetch_limit(key, project_id, self.opener) if project_id else None
            admin = replace(admin, org_limit_usd=org, project_limit_usd=proj, limit_error=None)
        except Exception as exc:  # the configured limit takes over
            admin = replace(admin, limit_error=describe(exc))
        self._admin, self._attempted, self._project = admin, now, project_id
        self._save(admin, now, project_id)
        fields = {
            "days": len(admin.buckets),
            "fresh_costs": admin.costs_error is None,
            "org_limit_usd": admin.org_limit_usd,
            "project_limit_usd": admin.project_limit_usd,
            "limit_error": admin.limit_error,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
        if admin.costs_error is not None:
            # One line per hourly attempt; spend falls back to the logs.
            self.log.warning("watch.openai_costs_failed", error=admin.costs_error, **fields)
        else:
            self.log.info("watch.openai_admin_read", **fields)
        return admin

    def _save(self, admin: Admin, attempted: datetime, project_id: str | None) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(_dump(admin, attempted, project_id)), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            self.log.warning("watch.openai_cache_write_failed", error=type(exc).__name__)

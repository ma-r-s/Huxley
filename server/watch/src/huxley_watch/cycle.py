"""One cycle: read the logs and the DB, run the rules, alert, ping the
dead-man check, rebuild the page. The loop and `--once` both call it.

Each step that talks to the outside (Telegram, the ping, the DB, the
disk) is fenced: a failure is logged by its type and the cycle goes on,
so the alert state is always saved and the page always rebuilt.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from huxley_watch import app
from huxley_watch.activity import presses, segments
from huxley_watch.alerts import (
    AlertState,
    Sender,
    deliver,
    load_state,
    plan,
    read_state,
    save_state,
)
from huxley_watch.bench import Bench, BenchReader
from huxley_watch.bookshelf import SHELF_FILE, read_shelf
from huxley_watch.config import Config, ConfigError, in_range, load_config
from huxley_watch.dayview import Archive
from huxley_watch.db import DbRead, read_db
from huxley_watch.events import LogReader
from huxley_watch.facts import server_alive, server_health_ok
from huxley_watch.openai_admin import AdminReader
from huxley_watch.page import render, render_fallback
from huxley_watch.rules import evaluate, make_inputs
from huxley_watch.views import build, fallback_status, mark_stale

if TYPE_CHECKING:
    from collections.abc import Callable

    from huxley_watch.activity import Segment
    from huxley_watch.rules import Inputs, Item
    from huxley_watch.wlog import WatchLog


def disk_pct(path: Path) -> float | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return 100 * usage.used / usage.total if usage.total else None


def ping(url: str, timeout_s: float = 10.0) -> str | None:
    """The dead-man check (healthchecks.io): None if it answered. Only the
    error's type comes back: its text can carry the URL, a secret."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            resp.read(64)
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except Exception as exc:  # InvalidURL, IncompleteRead, ...: not all OSError
        return type(exc).__name__
    return None


class Watcher:
    """Holds what survives between cycles: the config, the log cache, the
    alert state, the last status and page (which the HTTP server reads)."""

    def __init__(
        self,
        config_path: Path | None,
        cfg: Config,
        log: WatchLog,
        sender: Sender | None = None,
        pinger: Callable[[str], str | None] = ping,
        alive: Callable[[Config], bool | None] | None = None,
        admin_opener: Callable[..., Any] | None = None,
    ) -> None:
        self.config_path = config_path
        self.cfg = cfg
        self.log = log
        self._sender_override = sender
        self._ping = pinger
        self._alive = alive or _alive
        self._admin_opener = admin_opener or urllib.request.urlopen
        self.admin_reader = AdminReader(cfg.admin_file, log, self._admin_opener)
        self.reader = LogReader(cfg.logs_dir, timedelta(days=cfg.lookback_days), log)
        self.bench_reader = BenchReader(cfg.logs_dir / "bench.jsonl", log)
        self.state: AlertState = load_state(cfg.state_file, log)
        self.status: dict[str, Any] | None = None
        self.items: list[Item] = []
        self.html = b""
        self.json = b"{}"
        self.app: app.AppStatus | None = None
        self.app_json = b'{"schema": 1, "starting": true}'
        self.lock = threading.Lock()
        self.archive = Archive(log)
        self._notes_logged: set[tuple[str, str]] = set()
        self._db_problem: str | None = None
        self._log_notes()

    def _log_notes(self) -> None:
        for event, detail in self.cfg.notes:
            if (event, detail) not in self._notes_logged:
                # No secrets file is a choice (Telegram and the ping off);
                # an unreadable or open one is a mistake.
                if event == "watch.secrets_missing":
                    self.log.info(event, detail=detail)
                else:
                    self.log.warning(event, detail=detail)
                self._notes_logged.add((event, detail))
        offs = (
            (
                self.cfg.ntfy_topic is None
                and (self.cfg.telegram_token is None or self.cfg.telegram_chat_id is None),
                "watch.telegram_off",
                "no ntfy topic, bot token or chat id in secrets: alerts are only logged",
            ),
            (self.cfg.ping_url is None, "watch.ping_off", "no healthchecks_url"),
            (
                self.cfg.book_hours_per_month is None,
                "watch.book_budget_off",
                "no books.hours_per_month and no spotify.book_hours_per_month",
            ),
            (
                self.cfg.openai_admin_key is None,
                "watch.openai_admin_off",
                "no openai_admin_key in secrets: spend is the server's estimate, "
                "the limit openai.monthly_limit_usd",
            ),
        )
        for off, event, reason in offs:
            if off and (event, "") not in self._notes_logged:
                self.log.info(event, reason=reason)
                self._notes_logged.add((event, ""))

    def _reload(self) -> None:
        if self.config_path is None:
            return
        try:
            cfg = load_config(self.config_path)
        except (ConfigError, ValueError, TypeError) as exc:
            self.log.error("watch.config_invalid", error=str(exc)[:300], keeping="previous")
            return
        if cfg.logs_dir != self.cfg.logs_dir or cfg.lookback_days != self.cfg.lookback_days:
            self.reader = LogReader(cfg.logs_dir, timedelta(days=cfg.lookback_days), self.log)
            self.bench_reader = BenchReader(cfg.logs_dir / "bench.jsonl", self.log)
        if cfg.admin_file != self.cfg.admin_file:
            self.admin_reader = AdminReader(cfg.admin_file, self.log, self._admin_opener)
        self.cfg = cfg
        self._log_notes()

    @property
    def sender(self) -> Sender:
        if self._sender_override is not None:
            return self._sender_override
        return Sender(
            self.cfg.telegram_token,
            self.cfg.telegram_chat_id,
            ntfy_url=self.cfg.ntfy_url,
            page_url=self.cfg.page_url,
        )

    def _sync_state(self) -> None:
        """Adopt the state file if another process wrote it (a `--once`
        check run beside the service): otherwise this process would announce
        again what that one already said, from its stale memory. A file
        that is missing or can't be read is ignored: the memory stays, so a
        broken file never makes every cycle re-announce the open items."""
        got = read_state(self.cfg.state_file)
        if not isinstance(got, AlertState) or got == self.state:
            return
        self.log.info(
            "watch.state_reloaded",
            open_before=sorted(self.state.open),
            open_now=sorted(got.open),
            pending=len(got.pending),
        )
        self.state = got

    def _fenced(self, step: str, fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except Exception as exc:
            self.log.error("watch.step_failed", step=step, error=type(exc).__name__)
            return default

    def cycle(self, now: datetime | None = None) -> dict[str, Any]:
        started = time.monotonic()
        now = now or datetime.now(UTC)
        self._reload()
        cfg = self.cfg

        self._sync_state()
        result = self.reader.read(now)
        logs_problem = None
        if not cfg.logs_dir.is_dir():
            logs_problem = f"huxley-watch can't find the server's logs at {cfg.logs_dir}."
        elif not result.events:
            logs_problem = (
                f"The server's logs at {cfg.logs_dir} have nothing from the last "
                f"{cfg.lookback_days:g} days."
            )
        bench: Bench = self._fenced("bench", self.bench_reader.read, Bench())
        db: DbRead = self._fenced("db", lambda: read_db(cfg.db_path, now), DbRead(None, "error"))
        if db.problem != self._db_problem:
            if db.problem is not None:
                self.log.warning("watch.db_unavailable", path=str(cfg.db_path), error=db.problem)
            else:
                self.log.info("watch.db_available", path=str(cfg.db_path))
            self._db_problem = db.problem
        alive = self._fenced("server_alive", lambda: self._alive(cfg), None)
        health_ok = self._fenced("server_health", lambda: _health_ok(cfg), None) if alive else None
        disk_path = cfg.logs_dir if cfg.logs_dir.is_dir() else cfg.state_dir.parent
        # Hourly at most, cached; a failure inside is logged there and the
        # estimate takes over. Fenced too: nothing in it may cost a cycle.
        admin = self._fenced(
            "openai_admin",
            lambda: self.admin_reader.read(cfg.openai_admin_key, cfg.openai_project_id, now),
            None,
        )

        inputs = make_inputs(
            result.events,
            now,
            cfg.timezone,
            cfg.thresholds,
            walkie_mac=cfg.walkie_mac,
            server_alive=alive,
            health_ok=health_ok,
            was_open=frozenset(self.state.open),
            bench=bench,
            logs_problem=logs_problem,
            book_seconds=db.book_seconds,
            book_hours_per_month=cfg.book_hours_per_month,
            book_summaries=db.book_summaries,
            shelf=self._fenced("shelf", lambda: read_shelf(cfg.persona_dir / SHELF_FILE), None),
            book_renews_day=cfg.book_renews_day,
            book_since=cfg.book_since,
            balance_usd=cfg.balance_usd,
            balance_at=cfg.balance_at,
            admin=admin,
            monthly_limit_usd=cfg.monthly_limit_usd,
            openai_project_id=cfg.openai_project_id,
            disk_pct=self._fenced("disk", lambda: disk_pct(disk_path), None),
            alert_failures=self.state.failures,
            heard=result.heard,
        )
        if inputs.budget_error is not None:
            self.log.error("watch.step_failed", step="budget", error=inputs.budget_error)
        items = evaluate(inputs)
        quiet = in_range(now.astimezone(cfg.timezone).time(), cfg.quiet_start, cfg.quiet_end)

        state = plan(self.state, items, now, cfg.timezone, quiet, self.log)
        state = self._fenced(
            "deliver", lambda: deliver(state, self.sender, quiet, self.log), state
        )
        try:
            save_state(cfg.state_file, state)
        except Exception as exc:
            self.log.error(
                "watch.state_save_failed", path=str(cfg.state_file), error=type(exc).__name__
            )
        self.state = state

        ping_ok: bool | None = None
        if cfg.ping_url:
            url = cfg.ping_url
            error = self._fenced("ping", lambda: self._ping(url), "error")
            ping_ok = error is None
            if error is not None:
                host = urllib.parse.urlsplit(url).hostname
                self.log.warning("watch.ping_failed", host=host, error=error)

        days = self._fenced("archive", lambda: self.archive.bounds(cfg, now), None)
        w = inputs.walkie
        segs: list[Segment] | None = None
        try:
            segs = segments(result.events, now, bench, w.conns, w.spans)
            status = build(
                inputs,
                items,
                segs,
                presses(result.events, bench, w.conns),
                alerts_channel=self.sender.channel,
                interval_s=cfg.interval_s,
                archive=days,
            )
            page = render(status).encode("utf-8")
        except Exception as exc:  # one odd value must not cost the alerts or the page
            self.log.error("watch.step_failed", step="page", error=type(exc).__name__)
            status = fallback_status(items, now, cfg.timezone, cfg.interval_s, type(exc).__name__)
            page = render_fallback(status).encode("utf-8")
        body = json.dumps(status, ensure_ascii=False, indent=1).encode("utf-8")
        phone, phone_body = self._app(inputs, segs, now)
        with self.lock:
            self.status, self.items, self.html, self.json = status, items, page, body
            self.app, self.app_json = phone, phone_body
        self._write_files(page, body)

        self.log.info(
            "watch.cycle",
            open=[i.id for i in items],
            events=len(result.events),
            files=result.files,
            skipped_lines=result.skipped,
            walkie_online=inputs.walkie.online,
            server_alive=alive,
            bench_windows=len(bench.windows),
            quiet_hours=quiet,
            pending_alerts=len(state.pending),
            alert_failures=state.failures,
            ping_ok=ping_ok,
            db_ok=db.problem is None,
            **_budget_fields(inputs),
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return status

    def _app(
        self, inputs: Inputs, segs: list[Segment] | None, now: datetime
    ) -> tuple[app.AppStatus | None, bytes]:
        """The phone app's data (/app.json), from the same inputs and
        segments as the page. A failure costs only the app: it gets the
        error's type, the page and the alerts are already built."""
        started = time.monotonic()
        try:
            if segs is None:
                raise ValueError("segments")
            phone = app.build(inputs, segs, interval_s=self.cfg.interval_s)
        except Exception as exc:
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            self.log.error(
                "watch.step_failed",
                step="app",
                error=type(exc).__name__,
                at=f"{Path(frame.filename).name}:{frame.lineno}",
            )
            failed = {
                "schema": app.SCHEMA,
                "error": type(exc).__name__,
                "checked_at": now.isoformat(),
            }
            return None, json.dumps(failed).encode("utf-8")
        body = json.dumps(phone, ensure_ascii=False).encode("utf-8")
        self.log.debug(
            "watch.app_json_built",
            **app.summary(phone),
            bytes=len(body),
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return phone, body

    def current_app(self, now: datetime | None = None) -> bytes:
        """/app.json: flagged stale when the last cycle is too old."""
        with self.lock:
            phone, body = self.app, self.app_json
        if phone is None:
            return body
        flagged = app.mark_stale(phone, now or datetime.now(UTC))
        if flagged is phone:
            return body
        return json.dumps(flagged, ensure_ascii=False).encode("utf-8")

    def day(self, which: str, now: datetime | None = None) -> bytes | None:
        """The archive's day as JSON (web.py's /day/YYYY-MM-DD.json); None
        for a day outside the logs, or one that failed to build (logged)."""
        try:
            return self.archive.day(self.cfg, which, now)
        except Exception as exc:
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            self.log.error(
                "watch.step_failed",
                step="archive_day",
                day=which,
                error=type(exc).__name__,
                at=f"{Path(frame.filename).name}:{frame.lineno}",
            )
            return None

    def current(self, now: datetime | None = None) -> tuple[bytes, bytes]:
        """The page and JSON to serve now: rebuilt as "stopped checking"
        if the last cycle is too old (the loop died or hangs)."""
        with self.lock:
            status, page, body = self.status, self.html, self.json
        if status is None:
            return page, body
        flagged = mark_stale(status, now or datetime.now(UTC))
        if flagged is status:
            return page, body
        draw = render_fallback if "error" in flagged else render
        return (
            draw(flagged).encode("utf-8"),
            json.dumps(flagged, ensure_ascii=False, indent=1).encode("utf-8"),
        )

    def _write_files(self, page: bytes, body: bytes) -> None:
        """The page also lands in the state dir, for `--once` from cron."""
        try:
            self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
            (self.cfg.state_dir / "index.html").write_bytes(page)
            (self.cfg.state_dir / "status.json").write_bytes(body)
        except OSError as exc:
            self.log.warning("watch.page_write_failed", error=type(exc).__name__)


def _budget_fields(inputs: Inputs) -> dict[str, Any]:
    """The month's OpenAI budget on the cycle line: enough to see from the
    log alone why the card said what it said."""
    b = inputs.budget
    if b is None:
        return {}
    return {
        "budget_left_usd": round(b.left_usd, 2) if b.left_usd is not None else None,
        "budget_limit_usd": b.limit_usd,
        "budget_limit_source": b.limit_source,
        "month_spent_usd": round(b.spent_usd, 2),
        "spend_source": b.source,
        "pace_usd": round(b.pace_usd, 3) if b.pace_usd is not None else None,
    }


def _health_ok(cfg: Config) -> bool | None:
    """The server's own verdict from `/health` (same box: loopback is
    allowed). Only ever shown, never turned into "down": a server from
    before /health answers 426 and reads as None."""
    url = cfg.health_url or f"http://127.0.0.1:{cfg.server_port}/health"
    return server_health_ok(url)


def _alive(cfg: Config) -> bool | None:
    if not cfg.health_url and not cfg.check_pid:
        return None
    return server_alive(cfg.logs_dir, cfg.health_url, cfg.server_port)

"""The archive's days: /day/YYYY-MM-DD.json (T1.42).

One day as the page needs it: that day's strip (data and markup, the same
functions as today's strip on the page) and its list in time order
(archive.py's rows, daylist.py's markup). Built on request from that
day's logs only (daylog.py) and that day's recordings folders, never the
watcher's 40-day cache; past days are kept (a few) until their files
change.

Which days: from the oldest line any log file holds to today, local time.
"""

from __future__ import annotations

import json
import threading
import time as clock
from collections import OrderedDict
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from huxley_watch import archive, daylist
from huxley_watch.activity import segments
from huxley_watch.bench import BenchReader
from huxley_watch.daylog import AFTER, MARGIN, DayReader
from huxley_watch.facts import walkie_state
from huxley_watch.page import strip_html
from huxley_watch.views import strip

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from huxley_watch.config import Config
    from huxley_watch.wlog import WatchLog

SCHEMA = 1
KEEP_DAYS = 8  # past days kept built (each a few hundred KB at most)


def recording_files(root: Path | None, lo: datetime, hi: datetime) -> frozenset[str]:
    """The Ogg files in the recorder's folders for [lo, hi): it names them
    by UTC date (YYYY/MM/DD), so a local day spans two folders."""
    if root is None:
        return frozenset()
    out: set[str] = set()
    day = lo.astimezone(UTC).date()
    while day <= hi.astimezone(UTC).date():
        rel = f"{day:%Y/%m/%d}"
        try:
            names = [p.name for p in (root / rel).iterdir() if p.name.endswith(".ogg")]
        except OSError:
            names = []
        out.update(f"{rel}/{name}" for name in names)
        day += timedelta(days=1)
    return frozenset(out)


def covered_days(spans: Sequence[tuple[datetime, datetime]], tz: ZoneInfo) -> list[date]:
    """Every local date some log file has lines on."""
    days: set[date] = set()
    for first, last in spans:
        d, end = first.astimezone(tz).date(), last.astimezone(tz).date()
        while d <= end:
            days.add(d)
            d += timedelta(days=1)
    return sorted(days)


def neighbours(day: date, covered: Sequence[date], today: date) -> tuple[date | None, date | None]:
    """The picker's previous and next day: the nearest days with logs
    (today always counts), never past today."""
    known = sorted({*covered, today})
    before = [d for d in known if d < day]
    after = [d for d in known if day < d <= today]
    return (before[-1] if before else None), (after[0] if after else None)


def _clock(ts: datetime, day: date, tz: ZoneInfo, fmt: str = "%H:%M") -> str:
    """'14:05', or 'Sun 23:40' for a time on another day."""
    local = ts.astimezone(tz)
    return local.strftime(fmt) if local.date() == day else local.strftime(f"%a {fmt}")


def _min(ts: datetime, midnight: datetime) -> float:
    return round((ts - midnight).total_seconds() / 60, 2)


def shape(found: Sequence[archive.Row], midnight: datetime, tz: ZoneInfo) -> list[dict[str, Any]]:
    """The rows as JSON-ready dicts: minutes since midnight (`t` the row,
    `e` its end), local times, lengths."""
    day = midnight.date()
    out = []
    for r in found:
        end = r.end or (r.ts + timedelta(milliseconds=r.ms or 0))
        out.append(
            {
                "t": _min(r.ts, midnight),
                "e": _min(end, midnight),
                "at": _clock(r.ts, day, tz, "%H:%M:%S"),
                "kind": r.kind,
                "who": r.who,
                "text": r.text,
                "detail": r.detail,
                "audio": r.audio,
                "ms": r.ms,
                "end": _clock(r.end, day, tz) if r.end else None,
                "seconds": round((r.end - r.ts).total_seconds()) if r.end else None,
                "bench": r.bench,
            }
        )
    return out


class Archive:
    """Builds the days the page asks for; safe to call from the HTTP
    server's threads (one build at a time)."""

    def __init__(self, log: WatchLog) -> None:
        self.log = log
        self._lock = threading.Lock()  # one build at a time
        self._reader_lock = threading.Lock()  # quick: which reader
        self._reader: DayReader | None = None
        self._built: OrderedDict[date, tuple[tuple[Any, ...], bytes]] = OrderedDict()

    def _days(self, cfg: Config) -> tuple[DayReader, list[date]]:
        with self._reader_lock:
            if self._reader is None or self._reader.logs_dir != cfg.logs_dir:
                self._reader = DayReader(cfg.logs_dir, self.log)
            reader = self._reader
        spans = [(f.first, f.last) for f in reader.files()]
        return reader, covered_days(spans, cfg.timezone)

    def bounds(self, cfg: Config, now: datetime) -> dict[str, Any]:
        """For the picker on today's page: today, the first day, the previous.
        Never waits on a day being built (the cycle calls it)."""
        today = now.astimezone(cfg.timezone).date()
        _, covered = self._days(cfg)
        prev, _ = neighbours(today, covered, today)
        first = min([*covered, today])
        return {"today": today.isoformat(), "first": first.isoformat(), "prev": _iso(prev)}

    def day(self, cfg: Config, which: str, now: datetime | None = None) -> bytes | None:
        """The day's JSON, or None for a day outside the logs or not a date."""
        try:
            day = date.fromisoformat(which)
        except ValueError:
            return None
        if len(which) != 10:
            return None  # only YYYY-MM-DD
        now = now or datetime.now(UTC)
        today = now.astimezone(cfg.timezone).date()
        with self._lock:
            reader, covered = self._days(cfg)
            first = min([*covered, today])
            if not first <= day <= today:
                return None
            sig = self._signature(cfg, reader, day)
            kept = self._built.get(day)
            if day != today and kept is not None and kept[0] == sig:
                self._built.move_to_end(day)
                return kept[1]
            started = clock.monotonic()
            data = self.build(cfg, reader, day, now, covered)
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            kinds: dict[str, int] = {}
            for r in data["rows"]:
                kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
            self.log.info(
                "watch.archive_day",
                day=which,
                files=len(reader.opened),
                rows=len(data["rows"]),
                kinds=kinds,
                bytes=len(body),
                duration_ms=round((clock.monotonic() - started) * 1000),
            )
            if day != today:
                self._built[day] = (sig, body)  # one entry a day, replaced when stale
                self._built.move_to_end(day)
                while len(self._built) > KEEP_DAYS:
                    self._built.popitem(last=False)
            return body

    def _signature(self, cfg: Config, reader: DayReader, day: date) -> tuple[Any, ...]:
        """What a built day depends on: the files it reads (their lines past
        the read window can't change it), its recordings, bench.jsonl and
        the settings."""
        midnight = datetime.combine(day, time.min, cfg.timezone)
        lo, hi = midnight - MARGIN, midnight + timedelta(days=1) + AFTER
        files = tuple(
            (f.path, f.ino, f.first, min(f.last, hi))
            for f in reader.files()
            if f.last >= lo and f.first <= hi
        )
        recordings = recording_files(
            cfg.recordings_dir, midnight, midnight + timedelta(days=1) + MARGIN
        )
        bench = cfg.logs_dir / "bench.jsonl"
        try:
            st = bench.stat()
            bench_sig: tuple[int, int] | None = (st.st_size, st.st_mtime_ns)
        except OSError:
            bench_sig = None
        return (
            str(cfg.logs_dir),
            cfg.timezone.key,
            cfg.walkie_mac,
            str(cfg.recordings_dir),
            cfg.thresholds.silent_minutes,
            files,
            recordings,
            bench_sig,
        )

    def build(
        self,
        cfg: Config,
        reader: DayReader,
        day: date,
        now: datetime,
        covered: Sequence[date],
    ) -> dict[str, Any]:
        tz = cfg.timezone
        midnight = datetime.combine(day, time.min, tz)
        end = midnight + timedelta(days=1)
        today = now.astimezone(tz).date()
        events = reader.read(midnight, end)
        bench = BenchReader(cfg.logs_dir / "bench.jsonl", self.log).read()
        seen_until = min(now, end + MARGIN)
        walkie = walkie_state(
            [e for e in events if e.ts <= seen_until],
            seen_until,
            cfg.walkie_mac,
            timedelta(minutes=cfg.thresholds.silent_minutes),
        )
        until = min(now, end + AFTER)
        segs = segments(events, until, bench, walkie.conns, walkie.spans)
        day_strip = strip(midnight, now, segs, walkie.spans, bench.windows, today=day == today)

        files = recording_files(cfg.recordings_dir, midnight, end + MARGIN)
        found = [
            r
            for r in archive.rows(events, until, walkie.conns, walkie.spans, files, bench)
            if midnight <= r.ts < end or (r.end is not None and r.ts < end and r.end > midnight)
        ]
        listed = shape(found, midnight, tz)
        prev, nxt = neighbours(day, covered, today)
        return {
            "schema": SCHEMA,
            "date": day.isoformat(),
            "label": midnight.strftime("%a %d %b"),
            "is_today": day == today,
            "today": today.isoformat(),
            "prev": _iso(prev),
            "next": _iso(nxt),
            "recordings": len(files),
            "strip": day_strip,
            "strip_html": strip_html(day_strip),
            "rows": listed,
            "list_html": daylist.render(listed),
        }


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None

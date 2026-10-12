"""What the logs say right now: the walkie's connection, the speaker's
gaps, the server's runs. Pure functions over parsed events (plus one
read-only look at the server's instance lock); the rules (rules.py) and
the page's data (views.py) both read these."""

from __future__ import annotations

import os
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from huxley_watch.events import FIRMWARE_LOG

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date, datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from huxley_watch.bench import Bench
    from huxley_watch.events import Event

_RSSI = re.compile(r"\brssi=(-?\d+)")
_AUDIO = re.compile(r"\baudio=(\d+)")
_SILENT = re.compile(r"\bsilent_ms=(\d+)")
CHUNK_S = 0.02  # the speaker counts 20 ms chunks
MERGE_GAP = timedelta(minutes=2)  # reconnects closer than this are one stretch

Span = tuple["datetime", "datetime"]


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def merge(spans: Sequence[Span], gap: timedelta = MERGE_GAP) -> list[Span]:
    """Overlapping spans, or ones closer than `gap`, become one."""
    out: list[Span] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1] + gap:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


# --- the walkie ---------------------------------------------------------


@dataclass(frozen=True)
class WalkieState:
    online: bool
    # Online: when this stretch online began. Offline: when it went away.
    # None: never seen in the logs read.
    since: datetime | None
    version: str | None = None
    rssi: int | None = None
    conns: frozenset[str] = frozenset()  # the walkie's connection ids
    spans: tuple[Span, ...] = ()  # connected stretches, merged, oldest first

    @property
    def ever_seen(self) -> bool:
        return self.since is not None


def walkie_state(
    events: Sequence[Event],
    now: datetime,
    mac: str | None = None,
    silent: timedelta = timedelta(minutes=10),
    heard_map: Mapping[str, datetime] | None = None,
) -> WalkieState:
    """The walkie is the client whose `client.device` says shell=firmware
    (and, if configured, this MAC). A connection of it is open from
    `client_connected` until `client_disconnected` or an eviction. A new
    server process (`huxley_starting`) ends every connection at the last
    line heard from it; a persona swap or reload does not (the old app's
    `huxley_stopped` leaves the socket up). With nothing at all from its
    connection for `silent` (its speaker reports every 5 s), it ended at
    its last line, whatever the connection lines say. `heard_map` is the
    reader's last line per connection, kept past the pruning of the board's
    lines, so "seen" never moves backward."""
    opened: dict[str, datetime] = {}
    live: dict[str, datetime] = {}  # conn -> start
    heard: dict[str, datetime] = {}  # conn -> last line from it
    conns: set[str] = set()
    spans: list[Span] = []
    version: str | None = None
    rssi: int | None = None

    def close(conn: object, ts: datetime) -> None:
        if isinstance(conn, str) and conn in live:
            spans.append((live.pop(conn), ts))

    def last_heard(conn: str) -> datetime:
        seen = heard.get(conn, live.get(conn, now))
        if heard_map and conn in heard_map:
            seen = max(seen, min(heard_map[conn], now))
        return seen

    for e in events:
        name = e.name
        if name == "client_connected" and e.conn:
            opened[e.conn] = e.ts
        elif name == "client.device":
            if e.get("shell") != "firmware" or not e.conn:
                continue
            if mac and str(e.get("mac") or "").lower() != mac:
                continue
            live.setdefault(e.conn, opened.get(e.conn, e.ts))
            heard[e.conn] = e.ts
            conns.add(e.conn)
            version = str(e.get("version")) if e.get("version") else version
            value = _int(e.get("rssi"))
            rssi = value if value is not None else rssi
        elif name == "client.device.health" and e.conn in conns:
            # The walkie's minute line: it is alive on this connection.
            if e.conn in live:
                heard[e.conn] = e.ts
            value = _int(e.get("rssi"))
            rssi = value if value is not None else rssi
        elif name == "client_disconnected":
            close(e.conn, e.ts)
        elif name == "client_evicted":
            close(e.get("old_conn_id"), e.ts)
        elif name == "huxley_starting":
            for conn in list(live):
                close(conn, min(last_heard(conn), e.ts))
        elif name == FIRMWARE_LOG and e.conn in conns:
            if e.conn in live:
                heard[e.conn] = e.ts
            m = _RSSI.search(e.line)
            if m:
                rssi = int(m.group(1))

    online = False
    for conn, start in list(live.items()):
        last = last_heard(conn)
        if now - last > silent:
            spans.append((start, last))
        else:
            spans.append((start, now))
            online = True
    merged = merge(spans)
    frozen = frozenset(conns)
    if online:
        return WalkieState(True, merged[-1][0], version, rssi, frozen, tuple(merged))
    since = merged[-1][1] if merged else None
    return WalkieState(False, since, version, rssi, frozen, tuple(merged))


def wifi_label(rssi: int | None) -> str | None:
    if rssi is None:
        return None
    if rssi >= -67:
        return "good"
    if rssi >= -75:
        return "fair"
    return "weak"


def wifi_bars(rssi: int | None) -> int:
    if rssi is None:
        return 0
    if rssi > -55:
        return 4
    if rssi > -65:
        return 3
    if rssi > -75:
        return 2
    return 1


# --- the speaker's gaps (dev.py link's method) ---------------------------


@dataclass
class Bucket:
    """Half an hour of the speaker: seconds played (the board's spk.alive
    chunk counter), seconds of gaps heard (its spk.underrun silences)."""

    start: datetime  # local time
    played_s: float = 0.0
    gap_s: float = 0.0
    gaps: int = 0
    rssi: list[int] = field(default_factory=list)

    @property
    def share(self) -> float:
        total = self.played_s + self.gap_s
        return self.gap_s / total if total > 0 else 0.0


def half_hours(
    events: Sequence[Event],
    since: datetime,
    tz: ZoneInfo,
    conns: frozenset[str] | None = None,
    bench: Bench | None = None,
) -> list[Bucket]:
    """Same arithmetic as `clients/firmware/tools/dev.py link`: the
    played seconds are the step in the cumulative `audio=` counter
    between spk.alive lines (a reboot resets it: that step is skipped),
    the gaps the `silent_ms` of each spk.underrun. Lines before `since`
    only seed the counter, and so do lines inside a test window (their
    playing and gaps were the bench's, not his). T1.41's waiting cue is
    not an underrun (the firmware stopped counting it, 5d89504f)."""
    buckets: dict[datetime, Bucket] = {}
    last_chunks: int | None = None
    for e in events:
        if e.name != FIRMWARE_LOG or (conns is not None and e.conn not in conns):
            continue
        line = e.line
        chunks: int | None = None
        if line.startswith("spk.alive"):
            m = _AUDIO.search(line)
            chunks = int(m.group(1)) if m else None
        if e.ts < since or (bench is not None and bench.suppressed(e.ts)):
            last_chunks = chunks if chunks is not None else last_chunks
            continue
        local = e.ts.astimezone(tz)
        start = local.replace(minute=0 if local.minute < 30 else 30, second=0, microsecond=0)
        b = buckets.setdefault(start, Bucket(start))
        if chunks is not None:
            if last_chunks is not None and chunks >= last_chunks:
                b.played_s += (chunks - last_chunks) * CHUNK_S
            last_chunks = chunks
        elif line.startswith("spk.underrun"):
            m = _SILENT.search(line)
            b.gap_s += int(m.group(1)) / 1000 if m else 0
            b.gaps += 1
        else:
            m = _RSSI.search(line)
            if m:
                b.rssi.append(int(m.group(1)))
    return [buckets[k] for k in sorted(buckets)]


@dataclass(frozen=True)
class Gaps:
    played_s: float
    gap_s: float
    gaps: int

    @property
    def share(self) -> float:
        total = self.played_s + self.gap_s
        return self.gap_s / total if total > 0 else 0.0


def gaps_since(
    events: Sequence[Event],
    since: datetime,
    tz: ZoneInfo,
    conns: frozenset[str] | None,
    bench: Bench | None = None,
) -> Gaps:
    buckets = half_hours(events, since, tz, conns, bench)
    return Gaps(
        played_s=sum(b.played_s for b in buckets),
        gap_s=sum(b.gap_s for b in buckets),
        gaps=sum(b.gaps for b in buckets),
    )


# --- the server ---------------------------------------------------------


@dataclass(frozen=True)
class ServerState:
    running: bool | None  # None: nothing says either way
    started_at: datetime | None
    stopped_at: datetime | None
    starts: tuple[datetime, ...]
    unclean: tuple[datetime, ...] = ()  # starts with no stop since the previous start
    alive: bool | None = None  # the process itself (instance lock or /health)


def server_state(events: Sequence[Event], alive: bool | None = None) -> ServerState:
    """The server's runs from its lifecycle lines.

    `huxley_shutting_down` / `huxley_stopped` are logged by the app, so a
    persona swap or reload logs them too, then the new app's
    `huxley_app_ready` / `runtime.persona_swap_committed`: those mean it is
    running again. A start with no stop since the previous one is unclean
    (a crash, a kill, a power cut). `alive` (the process, checked outside
    the log) wins over the log when known."""
    running: bool | None = None
    started: datetime | None = None
    stopped: datetime | None = None
    stopped_since_start = False
    starts: list[datetime] = []
    unclean: list[datetime] = []
    last_seen: datetime | None = None
    for e in events:
        if e.name == "huxley_starting":
            if starts and not stopped_since_start:
                unclean.append(e.ts)
            running, started, stopped, stopped_since_start = True, e.ts, None, False
            starts.append(e.ts)
        elif e.name in ("huxley_shutting_down", "huxley_stopped"):
            if running is not False:
                stopped = e.ts
            running, stopped_since_start = False, True
        elif e.name in ("huxley_app_ready", "runtime.persona_swap_committed") and running is False:
            running, stopped, stopped_since_start = True, None, False
        last_seen = e.ts
    if alive is True:
        running, stopped = True, None
    elif alive is False and running is not False:
        running, stopped = False, last_seen  # gone without a word: its last line
    return ServerState(running, started, stopped, tuple(starts), tuple(unclean), alive)


def server_alive(logs_dir: Path, health_url: str | None = None, port: int = 8765) -> bool | None:
    """Is the server process up? `/health` if configured; else the pid in
    its instance lock (`logs/huxley-<port>.lock` for the configured port,
    kept after exit), which only means something when the watcher runs on
    the same box. Read only: the lock is never taken. None: no way to tell."""
    if health_url:
        try:
            with urllib.request.urlopen(health_url, timeout=5) as resp:
                status = int(resp.status)
            return 200 <= status < 300
        except Exception:  # any failure to answer is "not alive"
            return False
    try:
        text = (logs_dir / f"huxley-{port}.lock").read_text().strip()
    except OSError:
        return None
    if not text.isdigit():
        return None
    try:
        os.kill(int(text), 0)
    except PermissionError:
        return True  # exists, another user's
    except OSError:
        return False
    return True


@dataclass(frozen=True)
class BatteryState:
    """The walkie's cell, from its `system.battery` client events (F-0025)."""

    pct: int | None = None
    mv: int | None = None
    at: datetime | None = None  # when the last reading arrived
    wired: bool | None = None  # False: the board said battery.not_wired and never reported


def battery_state(events: Sequence[Event]) -> BatteryState:
    reading: Event | None = None
    not_wired = False
    for e in events:
        if e.name == "client.system.battery":
            reading = e
        elif e.name == FIRMWARE_LOG and str(e.get("line") or "").startswith("battery.not_wired"):
            not_wired = True
    if reading is not None:
        return BatteryState(_int(reading.get("pct")), _int(reading.get("mv")), reading.ts, True)
    return BatteryState(wired=False if not_wired else None)


def server_health_ok(url: str) -> bool | None:
    """The server's own verdict (`/health` "ok": persona up and the voice
    session connected). None when it doesn't answer, answers non-2xx (a
    server from before /health), or says nothing about it: never a guess."""
    import json

    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            if not 200 <= int(resp.status) < 300:
                return None
            body = json.loads(resp.read(65536))
    except Exception:
        return None
    ok = body.get("ok") if isinstance(body, dict) else None
    return ok if isinstance(ok, bool) else None


def book_cycle(
    now: datetime, tz: ZoneInfo, renews_day: int, since: date | None = None
) -> tuple[datetime, datetime]:
    """The audiobook allowance's current cycle: from the last renewal (local
    midnight on `renews_day`, clamped to short months) to the next one.
    `since` (when the allowance began) starts the first cycle later."""
    import calendar
    from datetime import datetime as _dt

    def renewal(year: int, month: int) -> datetime:
        day = min(renews_day, calendar.monthrange(year, month)[1])
        return _dt(year, month, day, tzinfo=tz)

    local = now.astimezone(tz)
    this = renewal(local.year, local.month)
    if this <= local:
        start = this
        y, m = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
        end = renewal(y, m)
    else:
        end = this
        y, m = (local.year - 1, 12) if local.month == 1 else (local.year, local.month - 1)
        start = renewal(y, m)
    if since is not None:
        began = _dt(since.year, since.month, since.day, tzinfo=tz)
        if began > start:
            start = began
    return start, end


def book_seconds_since(events: Sequence[Event], start: datetime, now: datetime) -> float:
    """Audiobook audio played since `start`, as Spotify bills it (1x content
    time; the walkie has no speed control): each finished book stream's
    `audio_ms`, plus the time a book still playing has run. Bench windows
    count too: a test that plays a book spends real hours."""
    used = 0.0
    open_at: datetime | None = None
    for e in events:
        if e.ts < start:
            continue
        if e.name == "spotify.play_started":
            ctx = str(e.get("context") or "")
            open_at = e.ts if ctx.startswith("spotify:show:") else None
        elif e.name == "spotify.stream_ended":
            if e.get("book"):
                used += max(0.0, float(e.get("audio_ms") or 0)) / 1000.0
            open_at = None
    if open_at is not None:
        used += max(0.0, (now - open_at).total_seconds())
    return used


APP_START = ("huxley_starting", "runtime.persona_swap_started", "runtime.reload_stopping_old_app")


def current_app(events: Sequence[Event]) -> Sequence[Event]:
    """The events since the running persona's skills were last set up:
    after the last server start, persona swap or reload."""
    for i in range(len(events) - 1, -1, -1):
        if events[i].name in APP_START:
            return events[i:]
    return events


def last(events: Sequence[Event], *names: str) -> Event | None:
    for i in range(len(events) - 1, -1, -1):
        if events[i].name in names:
            return events[i]
    return None

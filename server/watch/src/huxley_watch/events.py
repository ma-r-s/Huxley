"""Reading the Huxley server's logs: JSON lines in `<server cwd>/logs/`.

Files: `huxley.log` (the current run), `huxley_<YYYYMMDDTHHMMSS>[_N].log`
(earlier runs; the stamp is the run's last write, local time) and
`keep/*.log` (hand-kept copies, which duplicate archive lines: each line
counts once). Archives never change, so each is parsed once and cached;
the live file is read from where the last cycle stopped, whole lines
only, so a line being written is picked up next time instead of being
skipped as malformed. A complete line that isn't JSON is skipped and
counted, never fatal.

Only the events the rules, the page and the phone app use are kept (of
the transcripts, only his: Huxley's answers are most of a day's text and
the archive reads them from the day's file), and the board's
own log lines (`client.huxley.firmware_log`, most of the volume) only for
the last 13 hours and only their text (the gaps chart shows 12 hours, the
audio rule looks at 3). That keeps a 40-day window to tens of MB (the Pi
is short of memory).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from huxley_watch.wlog import WatchLog

FIRMWARE_LOG = "client.huxley.firmware_log"
# The board's lines worth keeping: the speaker's chunk counter and gaps
# (dev.py link's method), its health line (rssi) and boots.
FIRMWARE_PREFIXES = ("spk.alive", "spk.underrun", "info ", "ws.rx.stats", "boot ", "battery.")
FIRMWARE_KEEP = timedelta(hours=13)  # the gaps chart's 12 h (>= the rule's window)

# A tool that couldn't do what he asked (talks.py's "tool error"): a skill
# that raised (the coordinator's envelope), or a skill's own "couldn't".
TOOL_FAILURES = {
    "coord.tool_error": "failed",
    "spotify.unavailable": "unavailable",
    "spotify.book_not_found": "book not found",
    "spotify.search_failed": "search failed",
    "spotify.start_failed": "didn't start",
    "spotify.start_refused": "refused",
    "telegram.contact_not_found": "contact not found",
    "telegram.send_contact_not_found": "contact not found",
    "telegram.place_call_failed": "call failed",
    "telegram.send_failed": "message failed",
    "radio.unknown_station": "unknown station",
    "radio.stream_error": "radio failed",
    "audiobooks.stream_error": "book failed",
    "news.fetch_failed": "news failed",
    "search.error": "search failed",
}

KEEP_EVENTS = frozenset(
    {
        # server lifecycle and skills
        "huxley_starting",
        "huxley_ready",
        "huxley_shutting_down",
        "huxley_stopped",
        "huxley_app_ready",
        "runtime.persona_swap_started",
        "runtime.persona_swap_committed",
        "runtime.reload_stopping_old_app",
        "skill.setup_failed",
        # clients (the walkie is the one whose client.device says firmware)
        "client_connected",
        "client_disconnected",
        "client_evicted",
        "client.device",
        "client.device.hello",  # replaced `device` in firmware 4f8d4907+ (protocol schema 1)
        "client.device.health",  # the walkie's minute line
        "client.system.battery",
        FIRMWARE_LOG,
        # OpenAI
        "session_connected",
        "connection_failed",
        "app.reconnect_failed",
        "app.reconnect_succeeded",
        "session.rx.error",
        "session.response_refused",  # a refused answer, from an error or a failed response.done
        "cost.response_done",
        "cost.threshold_crossed",
        "cost.kill_switch_triggered",
        # Spotify (books, music) and Soloist
        "spotify.ws_state",
        "spotify.auth_state",
        "spotify.unavailable",
        "spotify.start_refused",
        "spotify.book_hours_out",
        "spotify.refusal_explained",
        "spotify.stream_ended",
        "spotify.play_started",  # its context says book (spotify:show:) or music
        # what the speaker played: streams and calls by id, and their kind
        "server.tx.stream_started",
        "server.tx.stream_ended",
        "server.tx.claim_started",
        "server.tx.claim_ended",
        "radio.stream_started",
        "audiobooks.stream_started",
        # him talking, Huxley answering (model_speaking is raised by
        # streams and calls too: an answer starts at coord.audio_start)
        "coord.ptt_start",
        "coord.ptt_stop",
        "coord.audio_start",
        "server.tx.model_speaking",
        # Telegram
        "telegram.credentials_missing",
        "telegram.transport.connected",
        # the phone app (app.py, talks.py): what he said (his transcripts
        # only, see LogReader), the tools that answered him and the ones
        # that failed, and what the music and books were
        "transcript",
        "session.tx.commit",  # a press's audio sent: its words will come
        "session.rx.tool_call",
        *TOOL_FAILURES,
        "spotify.track",
        "spotify.book_requested",
        "spotify.book_nav",
    }
)
# Fields never needed and sometimes large.
DROP_FIELDS = frozenset({"tools", "ua", "contacts", "skills"})


@dataclass(frozen=True, slots=True)
class Event:
    """One server log line the watcher cares about."""

    ts: datetime  # aware, UTC
    name: str
    f: Mapping[str, Any] = field(default_factory=dict)
    key: int = 0  # dedup key: a keep/ copy of an archive line is the same line

    def get(self, name: str, default: Any = None) -> Any:
        return self.f.get(name, default)

    @property
    def conn(self) -> str | None:
        value = self.f.get("conn_id")
        return str(value) if value is not None else None

    @property
    def line(self) -> str:
        """The board's own log line, for client.huxley.firmware_log."""
        return str(self.f.get("line") or "")


def parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def decode(raw: str) -> dict[str, Any] | None:
    """A log line as a dict, or None if it isn't a JSON object."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_line(raw: str, keep: frozenset[str] = KEEP_EVENTS) -> Event | None:
    """One log line -> Event, or None if it is malformed or not one the
    watcher keeps. Never raises."""
    obj = decode(raw)
    return to_event(obj, raw, keep) if obj is not None else None


def to_event(obj: dict[str, Any], raw: str, keep: frozenset[str] = KEEP_EVENTS) -> Event | None:
    name = obj.get("event")
    if not isinstance(name, str) or name not in keep:
        return None
    if name == FIRMWARE_LOG and not str(obj.get("line") or "").startswith(FIRMWARE_PREFIXES):
        return None
    ts = parse_ts(obj.get("timestamp"))
    if ts is None:
        return None
    if name == FIRMWARE_LOG:
        return Event(
            ts=ts,
            name=name,
            f={"line": str(obj.get("line")), "conn_id": obj.get("conn_id")},
            key=hash(raw.strip()),
        )
    if name == "client.device.hello":
        # The schema-1 hello (protocol.md "Device contract") replaced the
        # old `device` event: read as one, so every rule that knows the
        # walkie by `client.device` keeps knowing it.
        raw_net = obj.get("net")
        net: dict[str, Any] = raw_net if isinstance(raw_net, dict) else {}
        return Event(
            ts=ts,
            name="client.device",
            f={
                "shell": "firmware",
                "mac": obj.get("device_id"),
                "version": obj.get("version"),
                "rssi": net.get("rssi"),
                "board": obj.get("board"),
                "slot_state": obj.get("slot_state"),
                "conn_id": obj.get("conn_id"),
            },
            key=hash(raw.strip()),
        )
    fields: dict[str, Any] = {}
    for k, v in obj.items():
        if k in ("event", "timestamp", "level") or k in DROP_FIELDS:
            continue
        if k == "exception":
            # Tracebacks: only the last line says what happened (quota,
            # timeout, refused).
            fields[k] = str(v).strip().splitlines()[-1][:300] if v else ""
            continue
        fields[k] = v
    return Event(ts=ts, name=name, f=fields, key=hash(raw.strip()))


def _huxley_said(event: Event) -> bool:
    """Huxley's own transcripts: the archive reads them from the day's
    file (daylog.py); the 40 days kept here need only his words, which are
    short, and Huxley's answers are most of a day's text."""
    return event.name == "transcript" and event.get("role") != "user"


def make_events(lines: Iterable[str], keep: frozenset[str] = KEEP_EVENTS) -> list[Event]:
    """Test and tooling helper: parse lines, keep the relevant ones, in order
    (as the LogReader does with the default `keep`)."""
    out = [
        e
        for e in (parse_line(line, keep) for line in lines)
        if e is not None and not (keep is KEEP_EVENTS and _huxley_said(e))
    ]
    out.sort(key=lambda e: e.ts)
    return out


@dataclass
class _FileCache:
    ino: int
    mtime_ns: int
    offset: int  # bytes consumed (always at a line end)
    events: list[Event]


@dataclass
class ReadResult:
    events: list[Event]
    files: int
    skipped: int  # complete lines that were not JSON objects
    oldest: datetime | None  # earliest event kept: how far back the logs reach
    # The last line heard from each connection on its board's own log,
    # kept apart from the 13 h pruning of those lines, so "seen" never
    # moves backward as they age out.
    heard: dict[str, datetime] = field(default_factory=dict)


class LogReader:
    """Reads every log file in the window; keeps a per-file cache between
    cycles (a long-running watcher reads each archive once)."""

    def __init__(self, logs_dir: Path, lookback: timedelta, log: WatchLog) -> None:
        self.logs_dir = logs_dir
        self.lookback = lookback
        self._log = log
        self._cache: dict[Path, _FileCache] = {}
        self._skipped: dict[Path, int] = {}
        self._heard: dict[str, datetime] = {}
        self._missing_logged = False

    def _files(self, now: datetime) -> list[Path]:
        cutoff = now - self.lookback - timedelta(days=1)  # margin for clock and tz
        files: list[Path] = []
        for path in self.logs_dir.glob("huxley_*.log"):
            stamp = path.stem.removeprefix("huxley_")[: len("YYYYMMDDTHHMMSS")]
            try:
                written = datetime.strptime(stamp, "%Y%m%dT%H%M%S").astimezone(UTC)
            except ValueError:
                written = None  # not one of the server's names: read it anyway
            if written is None or written >= cutoff:
                files.append(path)
        for path in (self.logs_dir / "keep").glob("*.log"):
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            except OSError:
                continue
            if mtime >= cutoff:
                files.append(path)
        live = self.logs_dir / "huxley.log"
        if live.exists():
            files.append(live)
        return files

    def _read_file(self, path: Path, now: datetime) -> list[Event]:
        try:
            st = path.stat()
        except OSError:
            self._cache.pop(path, None)
            return []
        cached = self._cache.get(path)
        if cached is not None and cached.ino == st.st_ino and st.st_size >= cached.offset:
            if st.st_size == cached.offset:
                return cached.events
        else:
            cached = _FileCache(ino=st.st_ino, mtime_ns=st.st_mtime_ns, offset=0, events=[])
            self._skipped[path] = 0
        fw_cutoff = now - FIRMWARE_KEEP
        cutoff = now - self.lookback
        new: list[Event] = []
        skipped = 0
        offset = cached.offset
        try:
            # Line by line from a binary handle: a production log can be
            # hundreds of MB and the Pi has little memory to spare.
            with path.open("rb") as f:
                f.seek(offset)
                for raw_bytes in f:
                    if not raw_bytes.endswith(b"\n"):
                        break  # being written: read it next cycle
                    offset += len(raw_bytes)
                    raw = raw_bytes.decode("utf-8", errors="replace")
                    obj = decode(raw)
                    if obj is None:
                        if raw.strip():
                            skipped += 1
                        continue
                    if obj.get("event") == FIRMWARE_LOG:
                        self._hear(obj)
                    event = to_event(obj, raw)
                    if event is None or _huxley_said(event):
                        continue
                    if event.ts < cutoff or (event.name == FIRMWARE_LOG and event.ts < fw_cutoff):
                        continue
                    new.append(event)
        except OSError as exc:
            self._log.warning("watch.log_read_failed", path=str(path), error=type(exc).__name__)
            return cached.events
        self._skipped[path] = self._skipped.get(path, 0) + skipped
        cached.events = cached.events + new if new else cached.events
        cached.offset = offset
        cached.mtime_ns = st.st_mtime_ns
        self._cache[path] = cached
        return cached.events

    def _hear(self, obj: dict[str, Any]) -> None:
        conn, ts = obj.get("conn_id"), parse_ts(obj.get("timestamp"))
        if (
            isinstance(conn, str)
            and ts is not None
            and ts > self._heard.get(conn, ts - timedelta(1))
        ):
            self._heard[conn] = ts

    def read(self, now: datetime) -> ReadResult:
        if not self.logs_dir.is_dir():
            if not self._missing_logged:
                self._log.warning("watch.logs_missing", path=str(self.logs_dir))
                self._missing_logged = True
            return ReadResult(events=[], files=0, skipped=0, oldest=None)
        self._missing_logged = False
        files = self._files(now)
        for gone in set(self._cache) - set(files):
            del self._cache[gone]
            self._skipped.pop(gone, None)
        cutoff = now - self.lookback
        fw_cutoff = now - FIRMWARE_KEEP
        seen: set[tuple[datetime, int]] = set()
        events: list[Event] = []
        for path in files:
            kept: list[Event] = []
            for e in self._read_file(path, now):
                if e.ts < cutoff or (e.name == FIRMWARE_LOG and e.ts < fw_cutoff):
                    continue  # aged out: also dropped from the cache below
                kept.append(e)
                if (e.ts, e.key) in seen:
                    continue
                seen.add((e.ts, e.key))
                events.append(e)
            if path in self._cache and len(kept) != len(self._cache[path].events):
                self._cache[path].events = kept
        events.sort(key=lambda e: e.ts)
        self._heard = {c: t for c, t in self._heard.items() if t >= cutoff}
        return ReadResult(
            events=events,
            files=len(files),
            skipped=sum(self._skipped.get(p, 0) for p in files),
            oldest=events[0].ts if events else None,
            heard=dict(self._heard),
        )

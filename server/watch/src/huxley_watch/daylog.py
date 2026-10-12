"""One day of the server's logs, for the archive (T1.42).

The watcher's LogReader keeps 40 days of the few events the rules use;
the archive needs more of one day (transcripts, recordings, tool calls)
and only the day asked for. This reads only the files whose lines reach
that day, and in them only the lines that matter:

- before the day, from the run's start: what is still open at midnight
  (the walkie's connection, a stream, a call). A run's connections and
  streams end with it, so a run that ended before the day is never read;
- the day itself, from 15 minutes before midnight to 15 after: every line
  the day view uses, the board's own lines included (the walkie's
  silence says when it went away);
- up to 6 hours after: only the ends of streams and calls still open at
  midnight.

A line's time and event name are read from its bytes before any JSON is
decoded (the server writes `"timestamp"` and `"event"` last), so the
lines skipped cost almost nothing. Each file's first and last time are
cached by inode, size and mtime; a `keep/` copy of an archive line counts
once, as in the LogReader. A run can last weeks in one file, so where a
day's window begins in a file, and what was open there, is kept too (the
file only grows): asking for today again reads only today's lines.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from huxley_watch.events import KEEP_EVENTS, decode, parse_ts, to_event

if TYPE_CHECKING:
    from pathlib import Path

    from huxley_watch.events import Event
    from huxley_watch.wlog import WatchLog

MARGIN = timedelta(minutes=15)  # read in full around the day (the silence rule is 10 min)
AFTER = timedelta(hours=6)  # how long after the day a stream's or call's end is looked for

# What can still be open at midnight.
CONTEXT_EVENTS = frozenset(
    {
        "huxley_starting",
        "client_connected",
        "client.device",
        "client_disconnected",
        "client_evicted",
        "server.tx.stream_started",
        "server.tx.stream_ended",
        "server.tx.claim_started",
        "server.tx.claim_ended",
        "spotify.play_started",
        "radio.stream_started",
        "audiobooks.stream_started",
    }
)
# A stream's or call's end, and a recording's line (written when it ends:
# a call from 23:40 to 00:30 logs its legs at 00:30).
AFTER_EVENTS = frozenset(
    {
        "huxley_starting",
        "server.tx.stream_ended",
        "server.tx.claim_ended",
        "server.tx.stream_started",
        "recorder.segment",
    }
)
DAY_EVENTS = KEEP_EVENTS | {
    "transcript",
    "recorder.segment",
    "session.rx.tool_call",
}

_TS = b'"timestamp": "'
_EV = b'"event": "'


def _key(ts: datetime) -> bytes:
    """A UTC time as the first 19 bytes of the server's timestamps."""
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S").encode()


def line_key(raw: bytes) -> bytes | None:
    """The line's UTC time to the second, from its bytes. None if it has
    no `"timestamp"` the fast way (the caller decodes it instead)."""
    i = raw.rfind(_TS)
    if i < 0:
        return None
    start = i + len(_TS)
    end = raw.find(b'"', start)
    if end < 0 or raw[end - 1 : end] != b"Z" or end - start < 20:
        return None
    return raw[start : start + 19]


def line_event(raw: bytes) -> str | None:
    i = raw.rfind(_EV)
    if i < 0:
        return None
    start = i + len(_EV)
    end = raw.find(b'"', start)
    return raw[start:end].decode("ascii", errors="replace") if end > start else None


@dataclass(frozen=True)
class FileSpan:
    path: Path
    first: datetime
    last: datetime
    ino: int = 0


@dataclass
class _Start:
    """Where a day's window begins in a file, and the context lines before it."""

    offset: int
    events: list[Event] = field(default_factory=list)


CONTEXTS_KEPT = 32  # (file, day) starts remembered


class DayReader:
    """Reads the days the archive asks for. Holds only the per-file time
    index between calls; the events of a day are the caller's to keep."""

    def __init__(self, logs_dir: Path, log: WatchLog) -> None:
        self.logs_dir = logs_dir
        self._log = log
        self._index: dict[Path, tuple[tuple[int, int, int], FileSpan | None]] = {}
        self._starts: OrderedDict[tuple[Path, int, bytes], _Start] = OrderedDict()
        self._lock = threading.Lock()  # the index and starts: the cycle and the page share them
        self.opened: list[Path] = []  # files the last read went into (tests, logs)

    def _paths(self) -> list[Path]:
        if not self.logs_dir.is_dir():
            return []
        paths = sorted(self.logs_dir.glob("huxley_*.log"))
        paths += sorted((self.logs_dir / "keep").glob("*.log"))
        live = self.logs_dir / "huxley.log"
        if live.exists():
            paths.append(live)
        return paths

    def files(self) -> list[FileSpan]:
        """Every log file with its first and last line's time."""
        out: list[FileSpan] = []
        paths = self._paths()
        with self._lock:
            for gone in set(self._index) - set(paths):
                del self._index[gone]
            for path in paths:
                try:
                    st = path.stat()
                except OSError:
                    continue
                sig = (st.st_ino, st.st_size, st.st_mtime_ns)
                cached = self._index.get(path)
                if cached is None or cached[0] != sig:
                    cached = (sig, _span(path, st.st_size, st.st_ino))
                    self._index[path] = cached
                if cached[1] is not None:
                    out.append(cached[1])
        return out

    def first(self) -> datetime | None:
        """The oldest line any file holds: where the archive begins."""
        spans = self.files()
        return min(f.first for f in spans) if spans else None

    def read(self, lo: datetime, hi: datetime) -> list[Event]:
        """The events of [lo, hi) with the context described above, oldest
        first, each line once."""
        keys = (_key(lo - MARGIN), _key(hi + MARGIN), _key(hi + AFTER))
        wanted = [f for f in self.files() if f.last >= lo - MARGIN and f.first <= hi + AFTER]
        self.opened = [f.path for f in wanted]
        seen: set[tuple[datetime, int]] = set()
        out: list[Event] = []
        for f in wanted:
            where = (f.path, f.ino, keys[0])
            with self._lock:
                start = self._starts.get(where)
            try:
                found, start = _scan(f.path, keys, start)
            except OSError as exc:
                self._log.warning(
                    "watch.archive_read_failed", path=str(f.path), error=type(exc).__name__
                )
                continue
            if start is not None:
                with self._lock:
                    self._starts[where] = start
                    self._starts.move_to_end(where)
                    while len(self._starts) > CONTEXTS_KEPT:
                        self._starts.popitem(last=False)
            for e in found:
                if (e.ts, e.key) not in seen:
                    seen.add((e.ts, e.key))
                    out.append(e)
        out.sort(key=lambda e: e.ts)
        return out


def _scan(
    path: Path, keys: tuple[bytes, bytes, bytes], start: _Start | None
) -> tuple[list[Event], _Start | None]:
    """The file's events for the window, and where the window begins in it
    (None while the file hasn't reached it yet). With `start`, the lines
    before it are not read again."""
    before, day_end, after = keys
    found: list[Event] = list(start.events) if start else []
    offset = start.offset if start else 0
    with path.open("rb") as fh:
        fh.seek(offset)
        for raw in fh:
            at = offset
            offset += len(raw)
            if not raw.endswith(b"\n"):
                break  # being written
            key = line_key(raw)
            if key is None:
                obj = decode(raw.decode("utf-8", errors="replace"))
                ts = parse_ts(obj.get("timestamp")) if obj else None
                if ts is None:
                    continue
                key = _key(ts)
            if key > after:
                break  # a run's lines are in time order; 6 h is far past any skew
            if start is None and key >= before:
                start = _Start(at, list(found))  # the context so far, kept for next time
            name = line_event(raw)
            if key < before:
                keep = CONTEXT_EVENTS
            elif key <= day_end:
                keep = DAY_EVENTS
            else:
                keep = AFTER_EVENTS
            if name is not None and name not in keep:
                continue
            text = raw.decode("utf-8", errors="replace")
            obj = decode(text)
            if obj is None:
                continue
            event = to_event(obj, text, keep)
            if event is not None:
                found.append(event)
    return found, start


def _span(path: Path, size: int, ino: int = 0) -> FileSpan | None:
    """The first and last time in a file, from its head and its tail."""
    try:
        with path.open("rb") as fh:
            first = None
            for n, raw in enumerate(fh):
                first = _line_ts(raw)
                if first is not None or n > 50:
                    break
            fh.seek(max(0, size - 65536))
            tail = fh.read(65536)
    except OSError:
        return None
    if first is None:
        return None
    last = None
    for raw in reversed(tail.split(b"\n")):
        last = _line_ts(raw)
        if last is not None:
            break
    return FileSpan(path, first, last or first, ino)


def _line_ts(raw: bytes) -> datetime | None:
    obj = decode(raw.decode("utf-8", errors="replace"))
    return parse_ts(obj.get("timestamp")) if obj else None

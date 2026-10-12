"""Test windows: when the firmware session's tools drove the real walkie.

`clients/firmware/tools/bench.py` (and `dev.py talk/release/say/tone`)
press, play and fault the link on the walkie itself, so their presses,
answers, gaps and disconnects land in the same server log as grandpa's.
They append to `<logs>/bench.jsonl`, one JSON line per mark:
{"start": ISO UTC, "end": null | ISO UTC, "what": str}. A run writes its
start with "end": null, then the same start and what with the end (in a
`finally`), so lines are grouped by (start, what) and the one with an end
wins; a run that never wrote its end counts for at most 2 h, and no window
lasts more than 6 h (a longer one is capped, with a log line).

Two kinds, by `what`:
- "link test ...": a planned outage. The walkie's offline time and its
  gaps inside it raise no alert; the day strip still shows it offline,
  and hatches the window as "test".
- anything else ("bench.py ...", "dev.py ..."): the same, and its activity
  (presses, answers, streams, calls) is left out of use and the activity
  lanes; hatched as "bench".

The file is appended by another process: a malformed line is skipped and
logged once. No file: nothing is excluded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from huxley_watch.events import parse_ts

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from huxley_watch.wlog import WatchLog

OPEN_RUN_MAX = timedelta(hours=2)
WINDOW_MAX = timedelta(hours=6)  # longer is a mistake: it would silence alerts too long


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    what: str = ""

    @property
    def kind(self) -> str:
        return "test" if self.what.lower().startswith("link test") else "bench"


@dataclass(frozen=True)
class Bench:
    windows: tuple[Window, ...] = ()

    def in_bench(self, ts: datetime) -> bool:
        """Inside a window whose activity isn't grandpa's."""
        return any(w.start <= ts < w.end for w in self.windows if w.kind == "bench")

    def suppressed(self, ts: datetime) -> bool:
        """Inside any window: offline and gaps raise no alert."""
        return any(w.start <= ts < w.end for w in self.windows)

    def resume(self, ts: datetime) -> datetime:
        """When counting may start again: ts, or the end of the windows it
        falls in (chained when they overlap)."""
        moved = True
        while moved:
            moved = False
            for w in self.windows:
                if w.start <= ts < w.end:
                    ts, moved = w.end, True
        return ts

    def outside_bench(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """The parts of [start, end) outside every bench window."""
        pieces = [(start, end)]
        for w in self.windows:
            if w.kind != "bench":
                continue
            nxt: list[tuple[datetime, datetime]] = []
            for s, e in pieces:
                if w.end <= s or w.start >= e:
                    nxt.append((s, e))
                    continue
                if s < w.start:
                    nxt.append((s, w.start))
                if w.end < e:
                    nxt.append((w.end, e))
            pieces = nxt
        return pieces


@dataclass
class BenchReader:
    """Reads bench.jsonl each cycle; remembers which bad lines it logged."""

    path: Path
    log: WatchLog
    _logged: set[int] = field(default_factory=set)

    def read(self) -> Bench:
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return Bench()
        except OSError as exc:
            self.log.warning(
                "watch.bench_unreadable", path=str(self.path), error=type(exc).__name__
            )
            return Bench()
        runs: dict[tuple[datetime, str], datetime | None] = {}
        lines = text.splitlines()
        for n, raw in enumerate(lines):
            if not raw.strip():
                continue
            parsed = _parse(raw)
            if parsed is None:
                if n == len(lines) - 1 and not text.endswith("\n"):
                    continue  # the writer is mid-line: read it next cycle
                key = hash(raw)
                if key not in self._logged:
                    self._logged.add(key)
                    self.log.warning(
                        "watch.bench_line_skipped", path=str(self.path), line=raw[:200]
                    )
                continue
            start, end, what = parsed
            if end is not None:
                runs[(start, what)] = end
            else:
                runs.setdefault((start, what), None)
        windows = []
        for (start, what), end in runs.items():
            stop = end if end is not None else start + OPEN_RUN_MAX
            if stop - start > WINDOW_MAX:
                key = hash((start, what, "capped"))
                if key not in self._logged:
                    self._logged.add(key)
                    self.log.warning(
                        "watch.bench_window_capped",
                        start=start.isoformat(),
                        end=stop.isoformat(),
                        what=what[:200],
                        capped_hours=WINDOW_MAX.total_seconds() / 3600,
                    )
                stop = start + WINDOW_MAX
            windows.append(Window(start, stop, what))
        windows.sort(key=lambda w: w.start)
        return Bench(tuple(windows))


def _parse(raw: str) -> tuple[datetime, datetime | None, str] | None:
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    start = parse_ts(obj.get("start"))
    if start is None:
        return None
    end_raw = obj.get("end")
    end = parse_ts(end_raw) if end_raw is not None else None
    if end_raw is not None and (end is None or end <= start):
        return None
    return start, end, str(obj.get("what") or "")

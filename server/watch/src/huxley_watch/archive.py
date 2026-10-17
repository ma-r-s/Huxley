"""The archive (T1.42): one day as a single list in time order, read
from that day's logs (daylog.py). Pure.

Rows, each at its own time:
- what was said: `transcript` lines (him or Huxley, the text);
- the tools the model called: `session.rx.tool_call` (the name, its
  arguments kept short);
- recordings: `recorder.segment` lines with a file that still exists
  (T1.43 W3): a play row with its length, marked mic (his press), model
  (Huxley) or call (a call's two legs, his and the other side's, at the
  call's time);
- what played: a row per stream (music, books, radio: title, start, end)
  and per call (who, start, end).

Nothing is paired with anything: a recording sits at its time next to
whatever was said then. Only the walkie counts, as on the day strip: its
connections' recordings, and the rest while it was connected. Rows inside
a bench window are flagged (the page folds them away).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

from huxley_watch.activity import KIND_WINDOW, kind_of
from huxley_watch.events import parse_ts

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from huxley_watch.bench import Bench
    from huxley_watch.events import Event
    from huxley_watch.facts import Span

TAIL = timedelta(seconds=15)  # a line this soon after the walkie left is still its own
ARGS_MAX = 60  # characters of a tool's arguments shown
ORDER = {"stream": 0, "call": 1, "rec": 2, "say": 3, "tool": 4}  # within one instant


@dataclass(frozen=True)
class Row:
    ts: datetime
    kind: str  # say | tool | rec | stream | call
    who: str = ""  # say: him | huxley; rec: mic | model | call; stream: music | book | radio
    text: str = ""  # say: the transcript; tool: its name; stream: the title; call: who;
    # a call's recording: whose side ("Him", or the contact)
    detail: str = ""  # tool: its arguments, short
    audio: str | None = None  # rec: the file, relative to the recordings dir
    ms: int | None = None  # rec: its length
    end: datetime | None = None  # stream, call
    bench: bool = False
    live: bool = False  # stream, call: still open now (its end is now)


def within(ts: datetime, spans: Sequence[Span], tail: timedelta = timedelta(0)) -> bool:
    return any(a <= ts <= b + tail for a, b in spans)


def overlaps(start: datetime, end: datetime, spans: Sequence[Span]) -> bool:
    return any(a <= end and start <= b for a, b in spans)


def short_args(args: object) -> str:
    """A tool call's arguments in one short line: 'query: Adele, kind: artist'."""
    if isinstance(args, dict):
        text = ", ".join(f"{k}: {v}" for k, v in args.items() if v not in (None, "", [], {}))
    else:
        text = str(args or "")
    text = " ".join(text.split())
    return text if len(text) <= ARGS_MAX else text[: ARGS_MAX - 3].rstrip() + "..."


def rows(
    events: Sequence[Event],
    now: datetime,
    conns: frozenset[str],
    spans: Sequence[Span],
    files: frozenset[str],
    bench: Bench | None = None,
) -> list[Row]:
    """Every row of the events, in time order."""
    out: list[Row] = []
    for e in events:
        if e.name == "transcript" and e.get("role") in ("user", "assistant"):
            text = str(e.get("text") or "").strip()
            if text and within(e.ts, spans, TAIL):
                who = "him" if e.get("role") == "user" else "huxley"
                out.append(Row(e.ts, "say", who, text))
        elif e.name == "session.rx.tool_call" and e.get("name"):
            if within(e.ts, spans, TAIL):
                name, args = str(e.get("name")), short_args(e.get("args"))
                out.append(Row(e.ts, "tool", text=name, detail=args))
        elif e.name == "recorder.segment" and e.get("path") and e.conn in conns:
            rec = _recording(e, files)
            if rec is not None:
                out.append(rec)
    out += plays(events, now, spans)
    if bench is not None:
        out = [replace(r, bench=bench.in_bench(r.ts)) for r in out]
    out.sort(key=lambda r: (r.ts, ORDER[r.kind]))
    return out


def _recording(e: Event, files: frozenset[str]) -> Row | None:
    source = str(e.get("source") or "")
    start = parse_ts(e.get("start"))
    audio = str(e.get("path")).removeprefix("recordings/")
    if source not in ("mic", "model", "claim") or start is None or audio not in files:
        return None  # a timeline-only segment, or a file deleted or never finished
    ms = e.get("duration_ms")
    length = ms if isinstance(ms, int) and not isinstance(ms, bool) else None
    if source == "claim":  # the other side of a call
        return Row(start, "rec", "call", str(e.get("label") or "?"), audio=audio, ms=length)
    if e.get("claim_id"):  # his side of a call
        return Row(start, "rec", "call", "Him", audio=audio, ms=length)
    return Row(start, "rec", source, audio=audio, ms=length)


def plays(events: Sequence[Event], now: datetime, spans: Sequence[Span]) -> list[Row]:
    """A row per stream and per call, paired as on the day strip: streams
    by `stream_id` (said again while open: the same play; ended then
    started again: two), the kind from the skill's line right after the
    start; calls are claims, titled with the contact. Open at a server
    restart: ended at the old process's last line; still open: now."""
    out: list[Row] = []
    open_: dict[str, tuple[datetime, str]] = {}
    kinds: dict[str, str] = {}
    unnamed: list[tuple[str, datetime]] = []
    call: tuple[datetime, str] | None = None
    previous: datetime | None = None

    def stream(sid: str, end: datetime) -> None:
        start, label = open_.pop(sid)
        out.append(Row(start, "stream", kinds.get(sid, "music"), label, end=end))

    for e in events:
        if e.name == "server.tx.stream_started" and e.get("stream_id"):
            sid = str(e.get("stream_id"))
            if sid not in open_:
                open_[sid] = (e.ts, str(e.get("label") or ""))
                if sid not in kinds:
                    unnamed.append((sid, e.ts))
        elif e.name == "server.tx.stream_ended" and str(e.get("stream_id")) in open_:
            stream(str(e.get("stream_id")), e.ts)
        elif e.name == "server.tx.claim_started" and call is None:
            call = (e.ts, str(e.get("title") or e.get("skill") or ""))
        elif e.name == "server.tx.claim_ended" and call is not None:
            out.append(Row(call[0], "call", text=call[1], end=e.ts))
            call = None
        elif e.name == "huxley_starting":
            cut = previous or e.ts
            for sid in list(open_):
                stream(sid, cut)
            if call is not None:
                out.append(Row(call[0], "call", text=call[1], end=cut))
                call = None
            unnamed.clear()
        else:
            kind = kind_of(e)
            if kind is not None:
                while unnamed and e.ts - unnamed[-1][1] > KIND_WINDOW:
                    unnamed.pop()
                if unnamed:
                    kinds[unnamed.pop()[0]] = kind
                    unnamed.clear()
        previous = e.ts
    for sid in list(open_):
        start, label = open_.pop(sid)
        out.append(Row(start, "stream", kinds.get(sid, "music"), label, end=now, live=True))
    if call is not None:
        out.append(Row(call[0], "call", text=call[1], end=now, live=True))
    # Only what played to the walkie (one client at a time).
    return [r for r in out if r.end is not None and overlaps(r.ts, r.end, spans)]

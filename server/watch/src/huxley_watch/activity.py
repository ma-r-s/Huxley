"""What happened on the walkie, as time spans: him talking (a press),
Huxley speaking, and what the speaker played (music, books, radio,
calls). Pure, from the log events; bench windows cut out.

Huxley speaking runs from `coord.audio_start` (the model's audio for a
turn, an injected one included) to the next `server.tx.model_speaking`
false. That flag alone is not an answer: streams and calls raise it too
(a song would draw as Huxley talking for its whole length).

Streams are paired by `stream_id` (`server.tx.stream_started` ->
`server.tx.stream_ended`). The coordinator says `stream_started` again
for a stream already playing (a client back within its grace period),
which changes nothing; an `ended` then a `started` with the same id is a
pause (a reminder or a call parked it) and becomes two blocks, only the
played time counted. The kind comes from the skill's own line, logged
right after the first start (a few ms, up to a second or so): a
`spotify.play_started` whose context is a `spotify:show:` is a book, any
other Spotify play is music, `radio.stream_started` is radio and
`audiobooks.stream_started` a book. Calls are claims
(`server.tx.claim_started` -> `server.tx.claim_ended`, titled with the
contact).

Only the walkie counts: presses from its connections, and everything
else (answers, streams, calls) while it was connected, since with one
client at a time anything outside those stretches played to another
client or to nobody.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from huxley_watch.bench import Bench
    from huxley_watch.events import Event
    from huxley_watch.facts import Span

KINDS = ("music", "book", "radio", "call")
KIND_WINDOW = timedelta(seconds=10)  # a skill's line names the stream started this recently
PRESS_MAX = timedelta(minutes=2)  # a press with no release counts this long at most


@dataclass(frozen=True)
class Segment:
    kind: str  # talk | answer | music | book | radio | call
    start: datetime
    end: datetime
    label: str = ""
    origin: datetime | None = None  # the start before any cut: one call cut in two is one call

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def kind_of(e: Event) -> str | None:
    if e.name == "spotify.play_started":
        return "book" if str(e.get("context") or "").startswith("spotify:show:") else "music"
    if e.name == "radio.stream_started":
        return "radio"
    if e.name == "audiobooks.stream_started":
        return "book"
    return None


def _clip(segs: list[Segment], spans: Sequence[Span]) -> list[Segment]:
    out: list[Segment] = []
    for s in segs:
        for a, b in spans:
            lo, hi = max(s.start, a), min(s.end, b)
            if hi > lo:
                out.append(Segment(s.kind, lo, hi, s.label, s.origin))
    return out


def segments(
    events: Sequence[Event],
    now: datetime,
    bench: Bench | None = None,
    conns: frozenset[str] | None = None,
    spans: Sequence[Span] | None = None,
) -> list[Segment]:
    """Every span, oldest first. `conns`: the walkie's connections (only
    their presses count); `spans`: when it was connected (everything else
    is clipped to them); bench windows are cut out. Something still open
    is cut at `now`; one open when a new server process starts is cut at
    the last line before it (the old process died with it playing)."""
    out: list[Segment] = []
    press: datetime | None = None
    speaking: datetime | None = None
    streams: dict[str, tuple[datetime, str]] = {}  # open: id -> (start, label)
    kinds: dict[str, str] = {}
    unnamed: list[tuple[str, datetime]] = []  # streams waiting for their kind
    call: tuple[datetime, str] | None = None
    previous: datetime | None = None

    def stream_kind(sid: str) -> str:
        return kinds.get(sid, "music")

    for e in events:
        name = e.name
        if name == "huxley_starting":
            cut_at = previous or e.ts
            if press is not None:
                out.append(Segment("talk", press, min(cut_at, press + PRESS_MAX)))
            if speaking is not None:
                out.append(Segment("answer", speaking, cut_at))
            for sid, (start, label) in streams.items():
                out.append(Segment(stream_kind(sid), start, cut_at, label))
            if call is not None:
                out.append(Segment("call", call[0], cut_at, call[1]))
            press = speaking = call = None
            streams.clear()
            unnamed.clear()
        elif (
            name in ("coord.ptt_start", "coord.ptt_stop")
            and conns is not None
            and e.conn not in conns
        ):
            pass  # another client's press (the PWA, the phone app)
        elif name == "coord.ptt_start":
            if press is not None:
                out.append(Segment("talk", press, min(e.ts, press + PRESS_MAX)))
            press = e.ts
        elif name == "coord.ptt_stop" and press is not None:
            out.append(Segment("talk", press, e.ts))
            press = None
        elif name == "coord.audio_start":
            if speaking is None:
                speaking = e.ts
        elif name == "server.tx.model_speaking":
            # The flag says the speaker is owned (a stream or a call raises
            # it too), so only its fall ends an answer begun above.
            if not e.get("value") and speaking is not None:
                out.append(Segment("answer", speaking, e.ts))
                speaking = None
        elif name == "server.tx.stream_started" and e.get("stream_id"):
            sid = str(e.get("stream_id"))
            if sid not in streams:  # said again for a client coming back: the same play
                streams[sid] = (e.ts, str(e.get("label") or ""))
                if sid not in kinds:
                    unnamed.append((sid, e.ts))
        elif name == "server.tx.stream_ended" and str(e.get("stream_id")) in streams:
            sid = str(e.get("stream_id"))
            start, label = streams.pop(sid)
            out.append(Segment(stream_kind(sid), start, e.ts, label))
        elif name == "server.tx.claim_started":
            if call is None:
                call = (e.ts, str(e.get("title") or e.get("skill") or ""))
        elif name == "server.tx.claim_ended" and call is not None:
            out.append(Segment("call", call[0], e.ts, call[1]))
            call = None
        else:
            kind = kind_of(e)
            if kind is not None:
                # The newest stream still waiting for its kind, if recent.
                while unnamed and e.ts - unnamed[-1][1] > KIND_WINDOW:
                    unnamed.pop()
                if unnamed:
                    sid, _ = unnamed.pop()
                    kinds[sid] = kind
                    unnamed.clear()
        previous = e.ts

    if press is not None:
        out.append(Segment("talk", press, min(now, press + PRESS_MAX)))
    if speaking is not None:
        out.append(Segment("answer", speaking, now))
    for sid, (start, label) in streams.items():
        out.append(Segment(stream_kind(sid), start, now, label))
    if call is not None:
        out.append(Segment("call", call[0], now, call[1]))

    out = [Segment(s.kind, s.start, s.end, s.label, s.start) for s in out]
    if spans is not None:
        talk = [s for s in out if s.kind == "talk"]
        out = talk + _clip([s for s in out if s.kind != "talk"], spans)
    if bench is not None and bench.windows:
        cut: list[Segment] = []
        for s in out:
            for a, b in bench.outside_bench(s.start, s.end):
                cut.append(Segment(s.kind, a, b, s.label, s.origin))
        out = cut
    out = [s for s in out if s.end > s.start]
    out.sort(key=lambda s: s.start)
    return out


def presses(
    events: Sequence[Event], bench: Bench | None = None, conns: frozenset[str] | None = None
) -> list[datetime]:
    """When he pressed: the walkie's presses, bench presses left out."""
    return [
        e.ts
        for e in events
        if e.name == "coord.ptt_start"
        and (conns is None or e.conn in conns)
        and (bench is None or not bench.in_bench(e.ts))
    ]

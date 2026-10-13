"""What he asked and how it went (T1.46, the phone app's Talks tab): each
request, what he said, what answered it, and whether it was a struggle.
Pure, from the events the LogReader keeps.

A request is a press on the walkie that reached the model
(`coord.ptt_stop committed=true`) or was dropped as silent (`reason=silent`:
he pressed and nothing was heard). A tap too short to commit is not one.

His words: transcripts carry no turn, so each `transcript role=user` goes
to the oldest committed press still waiting for one (each committed press
is one input item, transcribed in order). On the 2026-10-12 logs this
paired 304 of 304 presses, 1.2 s after the release at the median, 5.5 s
at p99 and 19.3 s at most; a press waits 60 s. Every press counts in the
pairing, bench and other clients' too, so one never takes another's words;
only the walkie's, outside bench windows, are his.

A request is a struggle when any of these happens:

- tool error: a tool raised (`coord.tool_error`) or a skill said it couldn't
  (events.TOOL_FAILURES), within 60 s of the release and before the next press;
- not heard: a silent press, a commit that came back empty, or his
  transcript empty or a known Whisper hallucination ("Subtitulos ... Amara.org");
- no answer: no model audio, tool call, stream or call within 20 s of the
  release (a new press first counts: that turn will never answer);
- corrected: his next request begins with "no", or says "esa no", "otra"...,
  within 60 s of this one's last answer;
- asked again: his next request calls the same tool with a similar query
  within 90 s. Skipping tracks (`music_control` next or previous) and
  `set_volume` repeat by design and never count, here or as a correction.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from huxley_watch.events import TOOL_FAILURES

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from huxley_watch.bench import Bench
    from huxley_watch.events import Event

ANSWERS = frozenset(
    {
        "coord.audio_start",
        "session.rx.tool_call",
        "server.tx.stream_started",
        "server.tx.claim_started",
    }
)
TRANSCRIPT_WAIT = timedelta(seconds=60)  # his words arrive this soon after the release
ANSWER_WINDOW = timedelta(seconds=60)  # what comes later belongs to something else
NO_ANSWER = timedelta(seconds=20)
CORRECTION_WINDOW = timedelta(seconds=60)
REPEAT_WINDOW = timedelta(seconds=90)
COMMIT_EMPTY = "input_audio_buffer_commit_empty"
COMMIT_EMPTY_WINDOW = timedelta(seconds=5)
SIMILAR = 0.75  # difflib ratio between two queries that are "the same ask"

# Reasons, in the order the app names them.
REASONS = ("tool_error", "not_heard", "no_answer", "corrected", "asked_again")

# Whisper's hallucinations on silence or noise (Spanish), as folded text.
_GARBLED = (
    "amara.org",
    "subtitulos por",
    "subtitulos realizados",
    "gracias por ver",
    "suscribete",
    "thanks for watching",
)
_CORRECTION = re.compile(
    r"^no\b|\b(esa|ese|eso) no\b|\bno (es|era) (esa|ese|eso|asi)\b|\b(otra|otro)\b|\bequivoca"
)
_QUERY_KEYS = ("query", "title", "station", "name", "book_id", "action")


@dataclass(frozen=True)
class ToolUse:
    ts: datetime
    name: str
    args: Any = None

    @property
    def query(self) -> str:
        """What was asked of the tool, in one short line ('Adele')."""
        return query_of(self.args)


@dataclass
class Request:
    """One press and what followed it, up to the next press."""

    start: datetime
    stop: datetime
    conn: str | None
    committed: bool  # sent to the model; False: a silent press, dropped
    heard: bool = True  # False: silent, or the commit came back empty
    said: str | None = None  # his words; None: no transcript (yet)
    answered: datetime | None = None  # the first answer
    responded: datetime | None = None  # the last answer before the next press
    tools: list[ToolUse] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)  # "play_music failed", "not found"
    next_start: datetime | None = None  # the next press, any client


@dataclass(frozen=True)
class Verdict:
    request: Request
    reasons: tuple[str, ...] = ()  # empty: it went fine
    detail: str = ""  # the first reason's evidence: the failure, the correction, the tool

    @property
    def struggle(self) -> bool:
        return bool(self.reasons)


def fold(text: str) -> str:
    """Lower case, accents off: 'Subt\u00edtulos' -> 'subtitulos'."""
    plain = unicodedata.normalize("NFKD", text)
    return "".join(c for c in plain if not unicodedata.combining(c)).lower()


def words(text: str) -> str:
    """Folded, punctuation dropped, spaces collapsed: 'No! Esa no.' -> 'no esa no'."""
    return " ".join(re.findall(r"[a-z0-9]+", fold(text)))


def query_of(args: Any) -> str:
    """A tool's main argument ('Adele', 'blu', 'pause'), else all of them."""
    if isinstance(args, dict):
        for key in _QUERY_KEYS:
            value = args.get(key)
            if value not in (None, "", [], {}):
                return str(value)
        return " ".join(str(v) for _, v in sorted(args.items()) if v not in (None, "", [], {}))
    return str(args or "")


def garbled(text: str) -> bool:
    """Empty, no letters, or a known hallucination."""
    folded = fold(text)
    return (
        not words(folded)
        or not any(c.isalpha() for c in folded)
        or any(g in folded for g in _GARBLED)
    )


def correction(text: str) -> bool:
    return bool(_CORRECTION.search(words(text)))


def by_design(tool: ToolUse) -> bool:
    """Tools asked again on purpose: the next song, louder."""
    if tool.name == "set_volume":
        return True
    action = tool.args.get("action") if isinstance(tool.args, dict) else None
    return tool.name == "music_control" and action in ("next", "previous")


def similar(a: ToolUse, b: ToolUse) -> bool:
    if a.name != b.name:
        return False
    qa, qb = words(a.query), words(b.query)
    if qa == qb:
        return True
    if qa and qb and (qa in qb or qb in qa):
        return True
    return SequenceMatcher(None, qa, qb).ratio() >= SIMILAR


def _failure(e: Event) -> str:
    label = TOOL_FAILURES[e.name]
    if e.name == "coord.tool_error":
        return f"{e.get('tool') or 'tool'} {label}"
    if e.name == "spotify.unavailable" and e.get("reason"):
        return str(e.get("reason")).replace("_", " ")
    return label


def requests(events: Sequence[Event]) -> list[Request]:
    """Every request in the events, any client, oldest first."""
    out: list[Request] = []
    press: tuple[datetime, str | None] | None = None
    waiting: list[Request] = []  # committed, no transcript yet

    def open_request(ts: datetime) -> Request | None:
        """The latest request, if `ts` is still its turn."""
        if not out:
            return None
        r = out[-1]
        if ts < r.stop or r.next_start is not None or ts - r.stop > ANSWER_WINDOW:
            return None
        return r

    for e in events:
        name = e.name
        if name == "huxley_starting":
            press = None
            waiting.clear()  # a new process: the old one's words never come
        elif name == "coord.ptt_start":
            if out and out[-1].next_start is None:
                out[-1].next_start = e.ts
            press = (e.ts, e.conn)
        elif name == "coord.ptt_stop" and press is not None:
            committed = e.get("committed") is True
            if committed or e.get("reason") == "silent":
                r = Request(press[0], e.ts, press[1], committed, heard=committed)
                out.append(r)
                if committed:
                    waiting.append(r)
            press = None
        elif name == "session.rx.error" and e.get("code") == COMMIT_EMPTY:
            for r in reversed(waiting):
                if timedelta(0) <= e.ts - r.stop <= COMMIT_EMPTY_WINDOW:
                    r.heard = False
                    waiting.remove(r)
                    break
        elif name == "transcript":
            if e.get("role") != "user":
                continue
            waiting[:] = [r for r in waiting if e.ts - r.stop <= TRANSCRIPT_WAIT]
            if waiting and waiting[0].stop <= e.ts:
                waiting.pop(0).said = str(e.get("text") or "").strip()
        elif name in ANSWERS or name in TOOL_FAILURES:
            turn = open_request(e.ts)
            if turn is None:
                continue
            if name in TOOL_FAILURES:
                turn.failures.append(_failure(e))
                continue
            turn.answered = turn.answered or e.ts
            turn.responded = e.ts
            if name == "session.rx.tool_call" and e.get("name"):
                turn.tools.append(ToolUse(e.ts, str(e.get("name")), e.get("args")))
    return out


def his(
    found: Sequence[Request], conns: frozenset[str], bench: Bench | None = None
) -> list[Request]:
    """Only the walkie's requests, bench runs left out."""
    return [r for r in found if r.conn in conns and (bench is None or not bench.in_bench(r.start))]


def judge(found: Sequence[Request], now: datetime) -> list[Verdict]:
    """A verdict per request (his, in order: the next one is his next ask)."""
    out: list[Verdict] = []
    for k, r in enumerate(found):
        nxt = found[k + 1] if k + 1 < len(found) else None
        reasons: list[tuple[str, str]] = []
        if r.failures:
            reasons.append(("tool_error", r.failures[0]))
        if not r.heard:
            reasons.append(("not_heard", "silent" if not r.committed else "nothing heard"))
        elif r.said is not None and garbled(r.said):
            reasons.append(("not_heard", r.said or "empty"))
        if r.committed and r.heard:
            closed = r.next_start is not None or now - r.stop >= NO_ANSWER
            if closed and (r.answered is None or r.answered - r.stop > NO_ANSWER):
                reasons.append(("no_answer", ""))
        own = [t for t in r.tools if not by_design(t)]
        skip_only = bool(r.tools) and not own
        anchor = r.responded or r.answered
        if (
            nxt is not None
            and anchor is not None
            and not skip_only
            and nxt.said
            and timedelta(0) <= nxt.start - anchor <= CORRECTION_WINDOW
            and correction(nxt.said)
        ):
            reasons.append(("corrected", nxt.said))
        if nxt is not None:
            again = next(
                (
                    a
                    for a in own
                    for b in nxt.tools
                    if similar(a, b) and timedelta(0) <= b.ts - a.ts <= REPEAT_WINDOW
                ),
                None,
            )
            if again is not None:
                reasons.append(("asked_again", again.name))
        reasons.sort(key=lambda kv: REASONS.index(kv[0]))
        codes = tuple(dict.fromkeys(code for code, _ in reasons))
        out.append(Verdict(r, codes, reasons[0][1] if reasons else ""))
    return out

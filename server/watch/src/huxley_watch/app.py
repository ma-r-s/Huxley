"""The phone app's data (T1.46): `/app.json`, how grandpa uses the walkie.

Built in the same cycle, from the same Inputs and segments as the page
(views.py): no second reader of the logs. `AppStatus` below is the schema,
`schema: 1`; server/watch/README.md ("The phone app") documents it. The
app (static/app.html) renders only from it, plus the archive's
`/day/YYYY-MM-DD.json` when a day is opened.

The honesty rules (the design critic's, binding):

- "His usual" needs USUAL_DAYS days with the walkie mostly online in the
  daytime (ONLINE_SHARE of 07:00-22:00, bench windows left out) among the
  last USUAL_WINDOW days with full logs. Until then `used.state` is
  "learning" with the day count, never a range or a percentage.
- Quiet is not unwell: each day says whether the walkie was mostly offline
  in the daytime and its lowest battery reading; today carries the device
  state. He is compared only with his own range; nothing infers a mood.
- Struggles are talks.py's rules, every one of them named in the reasons.
- Times are his (the persona's timezone); `utc_offset_min` lets the phone
  label them when its own zone differs.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Literal, TypedDict

from huxley_watch import talks
from huxley_watch.facts import merge
from huxley_watch.text import short_when

if TYPE_CHECKING:
    from collections.abc import Sequence

    from huxley_watch.activity import Segment
    from huxley_watch.events import Event
    from huxley_watch.rules import Inputs

SCHEMA = 1
USUAL_DAYS = 14  # days with the walkie mostly online before "his usual" exists
USUAL_WINDOW = 28  # the days it is read from (yesterday back)
ONLINE_SHARE = 0.5  # of the daytime: a day under it was "mostly offline"
FIRST_USE_DAYS = 5  # days with a press before "usually first uses it at"
TREND_DAYS = 7
FAVOURITE_DAYS = 30
TALK_DAYS = 40  # the conversations list goes back this far (the logs read)
PLAYING_SLACK = timedelta(seconds=5)  # a segment ending this close to now is still playing
TALKED = timedelta(seconds=20)  # a call this long was a talk; shorter, a call that didn't connect
OFFLINE_SHOWN = 5  # % of today's daytime offline before the "used today" card says so
MEDIA = ("music", "book", "radio")

Span = tuple[datetime, datetime]


# --- the schema ----------------------------------------------------------


class LastUsed(TypedDict):
    at: str  # ISO UTC: the press
    clock: str  # his time: '14:05', 'Sat 21:10', 'Oct 3'
    said: str | None  # his words, when transcribed (never a hallucination)
    heard: bool  # False: a silent press, an empty commit, or no real words
    tool: str | None  # else the first tool that answered
    query: str | None  # and what it was asked


class Used(TypedDict):
    minutes: float  # used today so far (anything playing, him or Huxley talking)
    state: Literal["learning", "range"]
    days: int  # days counted toward his usual (mostly online, full logs)
    needed: int  # USUAL_DAYS
    learning_day: int | None  # "day N of 14" while learning
    by: str  # the clock time the range is for: his usual by now
    lo: float | None  # his usual by now: 25th to 75th percentile, minutes
    hi: float | None
    mid: float | None
    position: Literal["below", "within", "above"] | None
    offline_pct: int | None  # of today's daytime so far, once OFFLINE_SHOWN or more


class Person(TypedDict):
    name: str
    last_at: str  # ISO UTC: the last call's start
    clock: str  # his time
    days_ago: int  # calendar days, his time (0: today)
    minutes: float  # the last call's length
    talked: bool  # it lasted TALKED or more ("talked"); else "called"
    calls_30d: int


class Enjoying(TypedDict):
    kind: Literal["music", "book", "radio"]
    title: str  # the track, the book, the station
    by: str | None  # the artist or author
    chapter: str | None  # a book's chapter, by name
    chapter_index: int | None  # 1-based
    chapters: int | None
    playing: bool
    since: str  # his time
    until: str | None  # when it ended, his time (None while playing)
    at: str  # ISO UTC: the end, or now


class Device(TypedDict):
    online: bool | None  # None: never seen in the logs read
    since: str | None  # online since / offline since, his time
    battery_pct: int | None
    battery_at: str | None
    battery_stale: bool
    battery_low: bool
    battery_low_pct: float  # the watcher's threshold (thresholds.battery_low_pct)


class Day(TypedDict):
    date: str  # YYYY-MM-DD, his calendar
    label: str  # 'Tue 13'
    today: bool
    logs: Literal["full", "partial", "none"]  # partial: the logs begin that day
    minutes: float  # used
    online_share: float | None  # of the daytime (so far, today), bench left out
    offline: bool  # mostly offline in the daytime: hatched, never "low use"
    battery_min: int | None  # the day's lowest reading
    requests: int
    struggles: int


class Usual(TypedDict):
    lo: float
    hi: float
    mid: float
    days: int


class Favourite(TypedDict):
    name: str
    minutes: float
    chapter: str | None
    chapter_index: int | None
    chapters: int | None


class Favourites(TypedDict):
    days: int
    artist: Favourite | None
    book: Favourite | None
    station: Favourite | None


class FirstUse(TypedDict):
    clock: str | None  # the median first press, his time; None under FIRST_USE_DAYS
    days: int
    needed: int


class Struggle(TypedDict):
    at: str  # his time, 'HH:MM'
    t: float  # minutes since his midnight: where the day's transcript opens
    said: str | None
    tool: str | None
    query: str | None
    reasons: list[str]  # talks.REASONS codes, in that order
    detail: str


class Today(TypedDict):
    date: str
    last_used: LastUsed | None
    used: Used
    people: list[Person]
    enjoying: Enjoying | None
    device: Device


class Trends(TypedDict):
    days: list[Day]  # TREND_DAYS, oldest first
    usual_day: Usual | None  # a whole day's usual, once there is one
    favourites: Favourites
    first_use: FirstUse


class Talks(TypedDict):
    requests_today: int
    struggles: list[Struggle]  # today's, oldest first
    days: list[Day]  # today first, back to the logs' first day


class AppStatus(TypedDict):
    schema: int
    checked_at: str  # ISO UTC
    checked: str  # his time, 'HH:MM'
    interval_s: float
    stale: bool
    timezone: str
    utc_offset_min: int
    today: Today
    trends: Trends
    talks: Talks


# --- helpers ---------------------------------------------------------------


def _midnight(d: date, i: Inputs) -> datetime:
    return datetime.combine(d, time.min, i.tz)


def _overlap(spans: Sequence[Span], lo: datetime, hi: datetime) -> float:
    """Seconds of `spans` inside [lo, hi)."""
    total = 0.0
    for a, b in spans:
        s, e = max(a, lo), min(b, hi)
        if e > s:
            total += (e - s).total_seconds()
    return total


def used_seconds(segments: Sequence[Segment], lo: datetime, hi: datetime) -> float:
    """Time anything happened for him in [lo, hi): him talking, Huxley
    answering, music, books, radio, calls; overlaps counted once."""
    spans = [(max(s.start, lo), min(s.end, hi)) for s in segments if s.end > lo and s.start < hi]
    return sum((b - a).total_seconds() for a, b in merge(spans, timedelta(0)))


def online_share(i: Inputs, d: date) -> float | None:
    """How much of the day's daytime (so far, and since the logs begin) the
    walkie was connected, bench windows left out of both sides. None before
    the daytime starts or the logs do."""
    lo = datetime.combine(d, i.th.day_start, i.tz)
    if i.logs_from is not None:
        lo = max(lo, i.logs_from)
    hi = min(datetime.combine(d, i.th.day_end, i.tz), i.now)
    if hi <= lo:
        return None
    window = i.bench.outside_bench(lo, hi)
    total = sum((b - a).total_seconds() for a, b in window)
    if total <= 0:
        return None
    online = sum(_overlap(i.walkie.spans, a, b) for a, b in window)
    return round(online / total, 3)


def _quartiles(values: Sequence[float]) -> tuple[float, float, float]:
    q1, q2, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return round(q1, 1), round(q2, 1), round(q3, 1)


def _hm(minutes: float) -> str:
    m = round(minutes)
    return f"{m // 60 % 24:02d}:{m % 60:02d}"


def _clock(ts: datetime, i: Inputs) -> str:
    return short_when(ts, i.now, i.tz)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# --- the parts ---------------------------------------------------------------


def _days(
    i: Inputs, segments: Sequence[Segment], verdicts: Sequence[talks.Verdict], back: int
) -> list[tuple[Day, list[talks.Verdict]]]:
    """Today and `back - 1` days before it, oldest first, each with its verdicts."""
    today = i.now.astimezone(i.tz).date()
    readings: list[tuple[datetime, int]] = []
    for e in i.events:
        if e.name == "client.system.battery" and (pct := _int(e.get("pct"))) is not None:
            readings.append((e.ts, pct))
    out: list[tuple[Day, list[talks.Verdict]]] = []
    for n in range(back - 1, -1, -1):
        d = today - timedelta(days=n)
        lo = _midnight(d, i)
        hi = min(lo + timedelta(days=1), i.now)
        if i.logs_from is None or i.logs_from >= lo + timedelta(days=1):
            logs: Literal["full", "partial", "none"] = "none"
        else:
            logs = "full" if i.logs_from <= lo else "partial"
        share = online_share(i, d) if logs != "none" else None
        mine = [v for v in verdicts if lo <= v.request.start < hi]
        lows = [pct for ts, pct in readings if lo <= ts < hi]
        day: Day = {
            "date": d.isoformat(),
            "label": lo.strftime("%a %d"),
            "today": d == today,
            "logs": logs,
            "minutes": round(used_seconds(segments, lo, hi) / 60, 1),
            "online_share": share,
            "offline": share is not None and share < ONLINE_SHARE,
            "battery_min": min(lows) if lows else None,
            "requests": len(mine),
            "struggles": sum(1 for v in mine if v.struggle),
        }
        out.append((day, mine))
    return out


def _qualifying(days: Sequence[tuple[Day, list[talks.Verdict]]]) -> list[Day]:
    """The days his usual is read from: before today, within USUAL_WINDOW,
    full logs, the walkie mostly online."""
    past = [d for d, _ in days if not d["today"]][-USUAL_WINDOW:]
    return [
        d
        for d in past
        if d["logs"] == "full" and d["online_share"] is not None and not d["offline"]
    ]


def _used(i: Inputs, segments: Sequence[Segment], qualifying: Sequence[Day], today: Day) -> Used:
    local_now = i.now.astimezone(i.tz)
    today_lo = _midnight(local_now.date(), i)
    minutes = round(used_seconds(segments, today_lo, i.now) / 60, 1)
    n = len(qualifying)
    by = local_now.strftime("%H:%M")
    share = today["online_share"]
    away = round((1 - share) * 100) if share is not None else None
    offline_pct = away if away is not None and away >= OFFLINE_SHOWN else None
    if n < USUAL_DAYS:
        return {
            "minutes": minutes,
            "state": "learning",
            "days": n,
            "needed": USUAL_DAYS,
            "learning_day": min(USUAL_DAYS, n + 1),
            "by": by,
            "lo": None,
            "hi": None,
            "mid": None,
            "position": None,
            "offline_pct": offline_pct,
        }
    values = []
    for d in qualifying:
        day = date.fromisoformat(d["date"])
        lo = _midnight(day, i)
        values.append(
            used_seconds(segments, lo, datetime.combine(day, local_now.time(), i.tz)) / 60
        )
    q1, q2, q3 = _quartiles(values)
    position: Literal["below", "within", "above"] = (
        "below" if minutes < q1 else "above" if minutes > q3 else "within"
    )
    return {
        "minutes": minutes,
        "state": "range",
        "days": n,
        "needed": USUAL_DAYS,
        "learning_day": None,
        "by": by,
        "lo": q1,
        "hi": q3,
        "mid": q2,
        "position": position,
        "offline_pct": offline_pct,
    }


def _last_used(i: Inputs, mine: Sequence[talks.Request]) -> LastUsed | None:
    if not mine:
        return None
    r = mine[-1]
    tool = r.tools[0] if r.tools else None
    words = r.said if r.said and not talks.garbled(r.said) else None
    return {
        "at": r.start.isoformat(),
        "clock": _clock(r.start, i),
        "said": words,
        "heard": r.heard and (r.said is None or words is not None),
        "tool": tool.name if tool else None,
        "query": tool.query or None if tool else None,
    }


def _people(i: Inputs, segments: Sequence[Segment]) -> list[Person]:
    """Each person he talked to, the last call first."""
    calls: dict[tuple[str, datetime | None], list[Segment]] = {}
    for s in segments:
        if s.kind == "call":
            calls.setdefault(((s.label or "?").strip().title(), s.origin), []).append(s)
    last: dict[str, tuple[datetime, float]] = {}
    recent: dict[str, int] = {}
    month = i.now - timedelta(days=FAVOURITE_DAYS)
    for (name, _), parts in calls.items():
        start = min(p.start for p in parts)
        seconds = sum(p.seconds for p in parts)
        if name not in last or start > last[name][0]:
            last[name] = (start, seconds)
        if start >= month:
            recent[name] = recent.get(name, 0) + 1
    today = i.now.astimezone(i.tz).date()
    people: list[Person] = [
        {
            "name": name,
            "last_at": start.isoformat(),
            "clock": _clock(start, i),
            "days_ago": (today - start.astimezone(i.tz).date()).days,
            "minutes": round(seconds / 60, 2),  # a 9 s call stays 9 s
            "talked": seconds >= TALKED.total_seconds(),
            "calls_30d": recent.get(name, 0),
        }
        for name, (start, seconds) in last.items()
    ]
    people.sort(key=lambda p: p["last_at"], reverse=True)
    return people


def _tracks(events: Sequence[Event]) -> list[tuple[datetime, str, str | None]]:
    """Spotify's track changes: (when, title, first artist or None)."""
    out = []
    for e in events:
        if e.name == "spotify.track" and e.get("title"):
            artists = str(e.get("artists") or "").strip()
            out.append((e.ts, str(e.get("title")), artists.split(", ")[0] if artists else None))
    return out


def _track_spans(
    seg: Segment, tracks: Sequence[tuple[datetime, str, str | None]]
) -> list[tuple[datetime, datetime, str, str | None]]:
    """What played inside a music segment: the track playing when it began
    (a resume goes on with it) and each change after, to the segment's end."""
    before = [t for t in tracks if t[0] <= seg.start]
    inside = [t for t in tracks if seg.start < t[0] < seg.end]
    marks = ([(seg.start, *before[-1][1:])] if before else []) + list(inside)
    out = []
    for k, (ts, title, artist) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else seg.end
        out.append((max(ts, seg.start), end, title, artist))
    return out


class _Chapter(TypedDict):
    chapter: str | None
    chapter_index: int | None
    chapters: int | None


def _chapters(events: Sequence[Event]) -> dict[str, _Chapter]:
    """Each book's latest chapter as the Spotify skill logged it (at a
    start, a move, an end), by title. The index is 1-based."""
    books: dict[str, _Chapter] = {}
    totals: dict[str, int] = {}
    current: str | None = None
    for e in events:
        if e.name == "spotify.book_requested" and e.get("title"):
            if (n := _int(e.get("chapters"))) is not None:
                totals[str(e.get("title"))] = n
            continue
        if e.name not in ("spotify.play_started", "spotify.book_nav", "spotify.stream_ended"):
            continue
        title = e.get("book")
        if e.name == "spotify.play_started":
            current = str(title) if title else None
        if e.name == "spotify.book_nav" and not title:
            title = current  # logged without the book before T1.46
        if not title:
            continue
        index = _int(e.get("chapter_index"))
        chapters = _int(e.get("chapters"))
        if chapters is not None:
            totals[str(title)] = chapters
        if e.get("chapter") or index is not None:
            books[str(title)] = {
                "chapter": str(e.get("chapter")) if e.get("chapter") else None,
                "chapter_index": index + 1 if index is not None else None,
                "chapters": None,
            }
    for title, total in totals.items():
        books.setdefault(title, {"chapter": None, "chapter_index": None, "chapters": None})
        books[title]["chapters"] = total
    return books


def _enjoying(
    i: Inputs,
    segments: Sequence[Segment],
    tracks: Sequence[tuple[datetime, str, str | None]],
    chapters: dict[str, _Chapter],
) -> Enjoying | None:
    media = [s for s in segments if s.kind in MEDIA]
    if not media:
        return None
    seg = max(media, key=lambda s: s.end)
    playing = i.now - seg.end <= PLAYING_SLACK
    title, by = seg.label, None
    chapter: _Chapter = {"chapter": None, "chapter_index": None, "chapters": None}
    if seg.kind == "music":
        spans = _track_spans(seg, tracks)
        if spans:
            _, _, title, by = spans[-1]
    elif seg.kind == "book":
        chapter = chapters.get(seg.label, chapter)
    kind: Literal["music", "book", "radio"] = (
        "music" if seg.kind == "music" else "book" if seg.kind == "book" else "radio"
    )
    return {
        "kind": kind,
        "title": title or {"music": "Music", "book": "Book", "radio": "Radio"}[kind],
        "by": by,
        "chapter": chapter["chapter"],
        "chapter_index": chapter["chapter_index"],
        "chapters": chapter["chapters"],
        "playing": playing,
        "since": _clock(seg.start, i),
        "until": None if playing else _clock(seg.end, i),
        "at": (i.now if playing else seg.end).isoformat(),
    }


def _favourites(
    i: Inputs,
    segments: Sequence[Segment],
    tracks: Sequence[tuple[datetime, str, str | None]],
    chapters: dict[str, _Chapter],
) -> Favourites:
    since = i.now - timedelta(days=FAVOURITE_DAYS)
    artists: dict[str, float] = {}
    books: dict[str, float] = {}
    stations: dict[str, float] = {}
    for s in segments:
        if s.end <= since:
            continue
        clipped_start = max(s.start, since)
        if s.kind == "music":
            for a, b, _, artist in _track_spans(s, tracks):
                a = max(a, clipped_start)
                if artist and b > a:
                    artists[artist] = artists.get(artist, 0.0) + (b - a).total_seconds()
        elif s.kind in ("book", "radio") and s.label:
            into = books if s.kind == "book" else stations
            into[s.label] = into.get(s.label, 0.0) + (s.end - clipped_start).total_seconds()

    def top(counts: dict[str, float], book: bool = False) -> Favourite | None:
        if not counts:
            return None
        name = max(counts, key=lambda k: (counts[k], k))
        ch = chapters.get(name) if book else None
        return {
            "name": name,
            "minutes": round(counts[name] / 60, 1),
            "chapter": ch["chapter"] if ch else None,
            "chapter_index": ch["chapter_index"] if ch else None,
            "chapters": ch["chapters"] if ch else None,
        }

    return {
        "days": FAVOURITE_DAYS,
        "artist": top(artists),
        "book": top(books, book=True),
        "station": top(stations),
    }


def _first_use(
    i: Inputs, days: Sequence[tuple[Day, list[talks.Verdict]]], qualifying: Sequence[Day]
) -> FirstUse:
    ok = {d["date"] for d in qualifying}
    firsts = []
    for d, verdicts in days:
        if d["date"] in ok and verdicts:
            first = verdicts[0].request.start.astimezone(i.tz)
            firsts.append(first.hour * 60 + first.minute + first.second / 60)
    clock = _hm(statistics.median(firsts)) if len(firsts) >= FIRST_USE_DAYS else None
    return {"clock": clock, "days": len(firsts), "needed": FIRST_USE_DAYS}


def _device(i: Inputs) -> Device:
    w, b = i.walkie, i.battery
    return {
        "online": w.online if w.ever_seen else None,
        "since": _clock(w.since, i) if w.since else None,
        "battery_pct": b.pct,
        "battery_at": _clock(b.at, i) if b.at else None,
        "battery_stale": b.at is not None and i.now - b.at > timedelta(minutes=30),
        "battery_low": b.pct is not None and b.pct < i.th.battery_low_pct,
        "battery_low_pct": i.th.battery_low_pct,
    }


def _struggles(i: Inputs, verdicts: Sequence[talks.Verdict]) -> list[Struggle]:
    midnight = _midnight(i.now.astimezone(i.tz).date(), i)
    out: list[Struggle] = []
    for v in verdicts:
        r = v.request
        if not v.struggle or r.start < midnight:
            continue
        tool = r.tools[0] if r.tools else None
        out.append(
            {
                "at": r.start.astimezone(i.tz).strftime("%H:%M"),
                "t": round((r.start - midnight).total_seconds() / 60, 2),
                "said": r.said if r.said and not talks.garbled(r.said) else None,
                "tool": tool.name if tool else None,
                "query": tool.query or None if tool else None,
                "reasons": list(v.reasons),
                "detail": v.detail,
            }
        )
    return out


# --- the whole thing ------------------------------------------------------------


def build(i: Inputs, segments: Sequence[Segment], *, interval_s: float) -> AppStatus:
    """Everything the app draws, from the cycle's Inputs and segments."""
    local_now = i.now.astimezone(i.tz)
    mine = talks.his(talks.requests(i.events), i.walkie.conns, i.bench)
    verdicts = talks.judge(mine, i.now)
    logs_days = (
        (local_now.date() - i.logs_from.astimezone(i.tz).date()).days + 1 if i.logs_from else 1
    )
    days = _days(i, segments, verdicts, max(TREND_DAYS, min(TALK_DAYS, logs_days)))
    qualifying = _qualifying(days)
    tracks = _tracks(i.events)
    chapters = _chapters(i.events)
    usual_day: Usual | None = None
    if len(qualifying) >= USUAL_DAYS:
        lo, mid, hi = _quartiles([d["minutes"] for d in qualifying])
        usual_day = {"lo": lo, "hi": hi, "mid": mid, "days": len(qualifying)}
    talk_days = [d for d, _ in days if d["logs"] != "none" or d["today"]]
    offset = local_now.utcoffset()
    return {
        "schema": SCHEMA,
        "checked_at": i.now.isoformat(),
        "checked": local_now.strftime("%H:%M"),
        "interval_s": interval_s,
        "stale": False,
        "timezone": i.tz.key,
        "utc_offset_min": round(offset.total_seconds() / 60) if offset else 0,
        "today": {
            "date": local_now.date().isoformat(),
            "last_used": _last_used(i, mine),
            "used": _used(i, segments, qualifying, days[-1][0]),
            "people": _people(i, segments),
            "enjoying": _enjoying(i, segments, tracks, chapters),
            "device": _device(i),
        },
        "trends": {
            "days": [d for d, _ in days[-TREND_DAYS:]],
            "usual_day": usual_day,
            "favourites": _favourites(i, segments, tracks, chapters),
            "first_use": _first_use(i, days, qualifying),
        },
        "talks": {
            "requests_today": days[-1][0]["requests"],
            "struggles": _struggles(i, verdicts),
            "days": list(reversed(talk_days)),
        },
    }


def mark_stale(status: AppStatus, now: datetime) -> AppStatus:
    """Flagged when the last cycle is older than about three intervals, as
    /status.json is (views.mark_stale)."""
    try:
        checked = datetime.fromisoformat(status["checked_at"])
    except (KeyError, ValueError):
        return status
    if now - checked <= timedelta(seconds=3 * float(status["interval_s"] or 300) + 60):
        return status
    flagged = status.copy()
    flagged["stale"] = True
    return flagged


def summary(status: AppStatus) -> dict[str, object]:
    """The counts for the watcher's log line (watch.app_json_built)."""
    used = status["today"]["used"]
    return {
        "requests_today": status["talks"]["requests_today"],
        "struggles_today": len(status["talks"]["struggles"]),
        "usual": used["state"],
        "usual_days": used["days"],
        "people": len(status["today"]["people"]),
        "talk_days": len(status["talks"]["days"]),
    }

"""A day as a conversation, for the phone app's chat.

The archive's rows (archive.Row, shaped by dayview.shape) come one fact at
a time: his recording, then the tool, then the walkie's words, then his
words, a second later. Read that way, nobody knows who said what (Mario,
2026-10-17: "I don't know who said what because I have to remember the
icon"). Here they become items, oldest first:

- a turn: his press (his words with his recording) and the walkie's
  answer (its words with its recording, and what it did, in plain words);
  the walkie speaking on its own (a reminder, a call announced) is a turn
  with no press;
- an event: music, a book, the radio or a call, from when to when (or
  still going). The radio pausing for a question and coming back is one
  event, not one per question.

Pairing is by time, the way the server logs them. His words are
transcribed after his release and reach the log up to ~20 s later,
sometimes after the walkie's answer: they go to the oldest press already
released and still without words (two quick presses keep their own
words). The walkie's first sign (a tool, its words or its recording)
answers the latest press if it comes within FIRST_ANSWER_S of the release;
later, or more than AGAIN_S after the walkie last spoke in the turn, it is
speaking on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from huxley_watch.daylist import audio_url

# His words reach the log up to ~20 s after his release (2026-10-12: 19.3 s
# at most over 304 presses).
HIS_WORDS_S = 30.0
# Until then a press without words is still being written down, not
# "didn't catch it".
PENDING_S = 20.0
# The walkie's answer starts within seconds of the release (a filler while
# a search runs); its first sign this long after is something else.
FIRST_ANSWER_S = 25.0
# The walkie speaking again this long after it last spoke or acted in a
# turn is speaking on its own (a call announced 37 s after his request was
# read as part of the answer, 2026-10-17).
AGAIN_S = 20.0
# The same station, book or artist coming back this soon after it stopped
# is the same listening: it paused for a question.
RESUME_S = 90.0


@dataclass
class Side:
    text: str = ""
    clips: list[dict[str, Any]] = field(default_factory=list)  # {"audio", "ms"}

    def json(self) -> dict[str, Any]:
        return {"text": self.text or None, "clips": self.clips}


@dataclass
class Turn:
    t: float  # minutes since midnight
    at: str
    released: float | None = None  # a press: when he let go, minutes
    him: Side | None = None
    walkie: Side | None = None
    actions: list[dict[str, str]] = field(default_factory=list)
    bench: bool = False
    answered: float | None = None  # when the walkie last spoke or acted in it, minutes
    id: str = ""

    def json(self, now_t: float | None) -> dict[str, Any]:
        pending = False
        heard = True
        if self.him is not None and not self.him.text and self.walkie is None:
            since = self.released if self.released is not None else self.t
            if now_t is not None and (now_t - since) * 60 < PENDING_S:
                pending = True  # his words may still come
            else:
                heard = False
        return {
            "type": "turn",
            "id": self.id,
            "t": self.t,
            "at": self.at,
            "him": self.him.json() if self.him else None,
            "walkie": self.walkie.json() if self.walkie else None,
            "actions": self.actions,
            "heard": heard,
            "pending": pending,
            "bench": self.bench,
        }


def _hm(at: str) -> str:
    """'13:13:04' -> '13:13'; 'Fri 23:50:00' (the day before) -> 'Fri 23:50'."""
    *day, hms = at.split(" ")
    return " ".join([*day, hms[:5]])


def _clip(r: dict[str, Any]) -> dict[str, Any]:
    return {"audio": audio_url(r["audio"]), "ms": r["ms"]}


def _args(detail: str) -> dict[str, str]:
    """`short_args`' 'query: Leo Dan, kind: artist' back into fields."""
    out: dict[str, str] = {}
    for part in detail.split(", "):
        key, sep, value = part.partition(": ")
        if sep:
            out[key.strip()] = value.strip()
    return out


def _title(text: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in text.split())


def action(name: str, detail: str) -> dict[str, str]:
    """What the walkie did, as a person would say it, with an icon name."""
    a = _args(detail)
    q = a.get("query") or a.get("title") or a.get("name") or ""
    simple = {
        "get_weather": ("cloud", "Checked the weather"),
        "get_news": ("news", "Read the news"),
        "get_time": ("clock", "Said the time"),
        "get_current_time": ("clock", "Said the time"),
        "list_contacts": ("phone", "Listed his contacts"),
        "remove_contact": ("phone", "Removed a contact"),
        "my_phone_number": ("phone", "Read his own number"),
        "get_progress": ("book", "Said how far he is in the book"),
        "list_stations": ("radio", "Listed the stations"),
        "stop_radio": ("radio", "Stopped the radio"),
        "resume_radio": ("radio", "Resumed the radio"),
        "whats_playing": ("music", "Said what's playing"),
        "read_messages": ("message", "Read his messages"),
        "list_reminders": ("bell", "Listed his reminders"),
        "check_battery": ("battery", "Checked the battery"),
        "get_battery": ("battery", "Checked the battery"),
    }
    if name in simple:
        icon, label = simple[name]
        return {"icon": icon, "label": label}
    if name == "play_station":
        return {"icon": "radio", "label": f"Turned on {_title(a.get('station', 'the radio'))}"}
    if name == "play_music":
        what = f"Played {q}" if q else "Played music"
        return {"icon": "music", "label": f"{what} on Spotify"}
    if name == "music_control":
        verbs = {
            "pause": "Paused",
            "resume": "Resumed",
            "next": "Skipped to the next",
            "previous": "Went back",
            "stop": "Stopped",
        }
        return {"icon": "music", "label": verbs.get(a.get("action", ""), "Changed what's playing")}
    if name == "add_contact":
        return {
            "icon": "phone",
            "label": f"Saved {_title(q)} as a contact" if q else "Saved a contact",
        }
    if name == "set_timer":
        return {"icon": "bell", "label": "Set a timer"}
    if name in ("search_audiobooks", "play_audiobook", "audiobook_control"):
        what = {
            "search_audiobooks": "Looked for audiobooks",
            "play_audiobook": "Played an audiobook",
            "audiobook_control": "Moved in the audiobook",
        }[name]
        return {"icon": "book", "label": f"{what}: {q}" if q else what}
    if name == "play_book":
        return {"icon": "book", "label": f"Played the book {q}" if q else "Continued his book"}
    if name == "find_books":
        return {"icon": "book", "label": f"Looked for books: {q}" if q else "Suggested books"}
    if name == "book_control":
        how = a.get("action", "").replace("_", " ")
        return {
            "icon": "book",
            "label": f"Moved in the book ({how})" if how else "Moved in the book",
        }
    if name == "set_volume":
        level = a.get("level")
        return {
            "icon": "volume",
            "label": f"Set the volume to {level}" if level else "Changed the volume",
        }
    if name == "search_the_web":
        return {"icon": "search", "label": f"Searched the web: {q}" if q else "Searched the web"}
    if name == "call_contact":
        return {"icon": "phone", "label": f"Called {_title(q)}" if q else "Placed a call"}
    if name in ("send_message", "send_telegram"):
        return {
            "icon": "message",
            "label": f"Sent a message to {_title(q)}" if q else "Sent a message",
        }
    if name == "phone_number":
        return {"icon": "phone", "label": f"Read {_title(q)}'s number" if q else "Read his number"}
    if name in ("add_reminder", "set_reminder"):
        what = a.get("message") or a.get("text") or a.get("title") or ""
        return {"icon": "bell", "label": f"Set a reminder: {what}" if what else "Set a reminder"}
    if name in ("cancel_reminder", "delete_reminder"):
        return {"icon": "bell", "label": "Cancelled a reminder"}
    if name == "book_opinion":
        return {"icon": "book", "label": "Noted what he thought of the book"}
    label = name.replace("_", " ").capitalize()
    return {"icon": "tool", "label": f"{label}: {detail}" if detail else label}


def _event(r: dict[str, Any]) -> dict[str, Any]:
    kind = "call" if r["kind"] == "call" else r["who"]
    title = (r["text"] or "").strip()
    live = bool(r.get("live"))
    return {
        "type": "event",
        "id": "",
        "kind": kind,
        "title": _title(title) if kind == "call" else title,
        "t": r["t"],
        "e": r["e"],
        "at": _hm(r["at"]),
        "end": None if live else r["end"],
        "seconds": None if live else r["seconds"],
        "live": live,
        "clips": [],
        "bench": r["bench"],
    }


def items(rows: list[dict[str, Any]], now_t: float | None = None) -> list[dict[str, Any]]:
    """The day's rows (dayview.shape) as turns and events, oldest first.
    `now_t`: now in the day's minutes, for today (a press is "pending"
    while its words may still come); None for a past day."""
    out: list[Turn | dict[str, Any]] = []
    cur: Turn | None = None
    waiting: list[Turn] = []  # presses released and still without his words, oldest first
    events: list[dict[str, Any]] = []

    def new_turn(r: dict[str, Any]) -> Turn:
        nonlocal cur
        cur = Turn(r["t"], _hm(r["at"]), bench=r["bench"])
        out.append(cur)
        return cur

    def answer_turn(r: dict[str, Any]) -> Turn:
        """The turn the walkie's sign belongs to: the latest, unless it is
        too long after the press or after the walkie last spoke in it."""
        if cur is None:
            return new_turn(r)
        if cur.answered is not None:
            late = (r["t"] - cur.answered) * 60 > AGAIN_S
        else:
            start = cur.released if cur.released is not None else cur.t
            late = (r["t"] - start) * 60 > FIRST_ANSWER_S
        return new_turn(r) if late else cur

    for r in rows:
        kind, who = r["kind"], r["who"]
        if kind == "rec" and who == "mic":
            turn = new_turn(r)
            turn.him = Side(clips=[_clip(r)])
            turn.released = r["t"] + (r["ms"] or 0) / 60000
            waiting.append(turn)
        elif kind == "say" and who == "him":
            waiting = [w for w in waiting if (r["t"] - (w.released or w.t)) * 60 <= HIS_WORDS_S]
            ready = [w for w in waiting if (w.released or w.t) <= r["t"] + 1 / 60]
            if ready:
                press = ready[0]
                waiting.remove(press)
                assert press.him is not None
                press.him.text = r["text"]
            else:
                turn = new_turn(r)
                turn.him = Side(text=r["text"])
        elif kind == "tool":
            turn = answer_turn(r)
            turn.actions.append(action(r["text"], r["detail"] or ""))
            turn.answered = r["t"]
        elif kind == "say" and who == "huxley":
            turn = answer_turn(r)
            if turn.walkie is None:
                turn.walkie = Side()
            turn.walkie.text = f"{turn.walkie.text} {r['text']}".strip()
            turn.answered = r["t"]
        elif kind == "rec" and who == "model":
            turn = answer_turn(r)
            if turn.walkie is None:
                turn.walkie = Side()
            turn.walkie.clips.append(_clip(r))
            turn.answered = r["t"]
        elif kind == "rec" and who == "call":
            call = next(
                (
                    e
                    for e in reversed(events)
                    if e["kind"] == "call" and e["t"] - 0.5 <= r["t"] <= (e["e"] or r["t"]) + 0.5
                ),
                None,
            )
            if call is not None:
                call["clips"].append({**_clip(r), "who": r["text"] or "?"})
        elif kind in ("stream", "call"):
            ev = _event(r)
            same = next(
                (
                    e
                    for e in reversed(events)
                    if e["kind"] == ev["kind"]
                    and e["title"] == ev["title"]
                    and e["kind"] != "call"
                ),
                None,
            )
            if same is not None and not same["live"] and (ev["t"] - same["e"]) * 60 <= RESUME_S:
                # It paused for a question and came back: one listening.
                same.update(e=ev["e"], end=ev["end"], live=ev["live"])
                same["seconds"] = None if ev["live"] else round((ev["e"] - same["t"]) * 60)
                continue
            events.append(ev)
            out.append(ev)

    # Ids from the time, unique and the same on every build of the day: the
    # app keeps what it already drew by id.
    used: dict[str, int] = {}
    result = []
    for x in out:
        item = x.json(now_t) if isinstance(x, Turn) else x
        base = f"{'turn' if item['type'] == 'turn' else item['kind']}-{item['t']:.4f}"
        used[base] = used.get(base, 0) + 1
        item["id"] = base if used[base] == 1 else f"{base}-{used[base]}"
        result.append(item)
    return result

"""The archive's list: one day in time order, under the day strip.

Each row: the time, an icon for who or what, then the row's content:
what he or Huxley said (the transcript, the only prose), a tool the model
called (its name and arguments, short), a recording (a play button, a
plain progress bar, its length), or what played (a stream's or a call's
title, start, end, length). A thin marker with the gap's length where
nothing happened for 10 minutes or more. Rows inside a bench window are
folded into one "bench" row, opened with a tap, never mixed into his.

Rendered from the day's rows (dayview.py), the same dicts
/day/YYYY-MM-DD.json serves; everything from the logs is escaped. Free
text is content only: transcripts (`tx`), titles and names (`name`), tool
arguments (`arg`).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from huxley_watch.icons import icon
from huxley_watch.page import KIND_LABEL, _a, _t

GAP_MIN = 10.0  # minutes of nothing that get a marker

# Tool name -> icon, first match wins ("search_audiobooks" is a book,
# "set_timer" a bell, not a clock).
_TOOL_ICONS = (
    (("radio", "station"), "radio"),
    (("book", "progress"), "book"),
    (("music", "playing", "song"), "music"),
    (("volume",), "volume"),
    (("battery",), "battery"),
    (("timer", "remind"), "bell"),
    (("time", "date"), "clock"),
    (("news", "weather"), "news"),
    (("search", "web"), "search"),
    (("call", "message", "contact"), "phone"),
)


def tool_icon(name: str) -> str:
    low = name.lower()
    for keys, which in _TOOL_ICONS:
        if any(k in low for k in keys):
            return which
    return "tool"


def clock(seconds: float) -> str:
    """A length the way a player shows it: '0:07', '12:30', '1:02:03'."""
    s = max(0, round(seconds))
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def span(seconds: float) -> str:
    """A duration label: '45 s', '12 min', '1.5 h'."""
    if seconds < 60:
        return f"{max(1, round(seconds))} s"
    if seconds < 3600:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"


def audio_url(rel: str) -> str:
    """The page-relative URL of a recording (web.py serves audio/<rel>)."""
    return "audio/" + quote(rel, safe="/")


def _ic(name: str, cls: str = "", tip: str = "") -> str:
    tip_attr = f' data-tip="{_a(tip)}"' if tip else ""
    return f'<span class="ic w-{cls}"{tip_attr}>{icon(name)}</span>'


def _body(r: dict[str, Any]) -> tuple[str, str]:
    """(the icon cell, the content cell) of a row."""
    kind, who = r["kind"], r["who"]
    if kind == "say":
        name = "him" if who == "him" else "huxley"
        return _ic(name, name, "Him" if who == "him" else "Huxley"), (
            f'<span class="tx">{_t(r["text"])}</span>'
        )
    if kind == "tool":
        arg = f' <span class="arg">{_t(r["detail"])}</span>' if r["detail"] else ""
        return _ic(tool_icon(r["text"]), "tool", "tool"), (
            f'<span class="tl"><b class="tn">{_t(r["text"])}</b>{arg}</span>'
        )
    if kind == "rec":
        side = {"mic": "him", "model": "huxley"}.get(who, "call")
        label = f'<span class="name">{_t(r["text"])}</span>' if who == "call" else ""
        ms = int(r["ms"] or 0)
        return _ic({"mic": "mic", "model": "model"}.get(who, "phone"), side, who), (
            f'<span class="pl"><button class="play" type="button" '
            f'data-src="{_a(audio_url(r["audio"]))}" data-ms="{ms}" aria-label="play">'
            f"{icon('play', 'ico i-play')}{icon('pause', 'ico i-pause')}</button>{label}"
            f'<span class="track"><i class="prog"></i></span>'
            f'<span class="len">{_t(clock(max(1.0, ms / 1000)) if ms else "")}</span></span>'
        )
    # stream or call: what played, from start to end
    if kind == "call":
        lead = _ic("phone", "call", KIND_LABEL["call"])
    else:
        lead = f'<span class="ic"><b class="sw {_a(who)}" data-tip="{_a(KIND_LABEL.get(who, ""))}"></b></span>'
    title = r["text"].strip() or KIND_LABEL.get(who, "")
    if kind == "call":
        title = title.title()
    return lead, (
        f'<span class="st"><span class="name">{_t(title)}</span><i class="dash"></i>'
        f'<span class="fig">{_t(r["end"] or "")}</span>'
        f'<span class="fig">{_t(span(r["seconds"] or 0))}</span></span>'
    )


def _row(r: dict[str, Any]) -> str:
    lead, content = _body(r)
    return (
        f'<div class="li k-{_a(r["kind"])}" data-t="{r["t"]}"><span class="at">{_t(r["at"])}</span>'
        f"{lead}{content}</div>"
    )


def render(rows: list[dict[str, Any]]) -> str:
    """The day's list; an empty day is an empty list."""
    out: list[str] = []
    bench: list[dict[str, Any]] = []
    last: float | None = None

    def fold() -> None:
        if bench:
            n = len(bench)
            out.append(
                f'<details class="bgroup" data-t="{bench[0]["t"]}"><summary class="li">'
                f'<span class="at">{_t(bench[0]["at"])}</span>'
                f'<span class="ic"><b class="sw hatch"></b></span>'
                f'<span class="st"><span class="fig">bench</span><span class="fig">{n}</span></span>'
                f"</summary>{''.join(_row(r) for r in bench)}</details>"
            )
            bench.clear()

    for r in rows:
        if last is not None and r["t"] - last >= GAP_MIN:
            fold()
            out.append(
                f'<div class="gap"><span class="fig">{_t(span((r["t"] - last) * 60))}</span></div>'
            )
        if r["bench"]:
            bench.append(r)
        else:
            fold()
            out.append(_row(r))
        last = max(last if last is not None else r["e"], r["e"])
    fold()
    return f'<div class="rows">{"".join(out)}</div>'

"""The page: labels, numbers, icons and charts, no sentences (Mario's
approved mockup, 2026-10-12). It renders only from the status dict
(views.py), the same data /status.json serves.

Top: a status dot and "All good" / "N need you". Then one card per open
item (big number, short label, action chip), six health tiles, today
hour by hour (walkie connected; him talking and Huxley speaking; music,
books, radio, calls; test windows hatched; a "now" marker), the last 7
days (stacked hours, presses), the month's audiobook gauge, audio gaps per
half hour on a continuous axis with the threshold, and calls per person.

Charts are HTML and CSS rather than SVG text, so labels stay readable at
phone width; the gauge alone is SVG. Self-contained: no external file;
one small inline script turns `data-tip` into a tooltip (hover or tap).
"""

from __future__ import annotations

import html
import math
from typing import Any

KIND_LABEL = {
    "online": "Walkie connected",
    "talk": "Him",
    "answer": "Huxley",
    "music": "Music",
    "book": "Books",
    "radio": "Radio",
    "call": "Calls",
}


def _t(text: object) -> str:
    """Element text."""
    return html.escape(str(text), quote=False)


def _a(text: object) -> str:
    """An attribute value (always double-quoted)."""
    return html.escape(str(text), quote=True)


def _hm(minutes: float) -> str:
    m = max(0, round(minutes))
    return f"{m // 60 % 24:02d}:{m % 60:02d}"


def _dur(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{max(1, round(seconds / 60))} min"


def _pct(minutes: float) -> str:
    return f"{max(0.0, min(100.0, minutes / 1440 * 100)):.3f}%"


CSS = """
:root{color-scheme:light;--page:#f4f3ef;--card:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--mute:#898781;--grid:#e1e0d9;--ring:rgba(11,11,11,.08);
--music:#2a78d6;--book:#eb6834;--radio:#1baf7a;--call:#eda100;--talk:#3b3a37;--answer:#a9a79e;--online:#d9d7cd;
--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--stale:#898781}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;--card:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--mute:#898781;--grid:#2c2c2a;--ring:rgba(255,255,255,.08);
--music:#3987e5;--book:#d95926;--radio:#199e70;--call:#c98500;--talk:#e9e7df;--answer:#77756e;--online:#33332f}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--card:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--mute:#898781;--grid:#2c2c2a;--ring:rgba(255,255,255,.08);
--music:#3987e5;--book:#d95926;--radio:#199e70;--call:#c98500;--talk:#e9e7df;--answer:#77756e;--online:#33332f}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:20px 16px 48px}
.top{display:flex;align-items:center;gap:14px;margin:6px 0 18px}
.dot{flex:none;width:22px;height:22px;border-radius:50%;background:var(--good);box-shadow:0 0 0 6px color-mix(in srgb,var(--good) 18%,transparent)}
.dot.warn{background:var(--warn);box-shadow:0 0 0 6px color-mix(in srgb,var(--warn) 22%,transparent)}
.dot.crit{background:var(--crit);box-shadow:0 0 0 6px color-mix(in srgb,var(--crit) 18%,transparent)}
.dot.stale{background:var(--stale);box-shadow:none}
.dot.test{background:repeating-linear-gradient(-45deg,var(--warn) 0 3px,color-mix(in srgb,var(--warn) 35%,transparent) 3px 6px);box-shadow:0 0 0 6px color-mix(in srgb,var(--warn) 14%,transparent)}
.top h1{font-size:30px;margin:0;font-weight:650;letter-spacing:-.01em}
.top .when{margin-left:auto;color:var(--mute);font-size:13px;white-space:nowrap}
.stale .body{opacity:.4;filter:grayscale(1)}
.grid{display:grid;gap:12px}
.tiles{grid-template-columns:repeat(6,minmax(0,1fr))}
@media (max-width:860px){.tiles{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:480px){.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}}
.card{background:var(--card);border-radius:14px;box-shadow:0 0 0 1px var(--ring);padding:14px}
.tile .lab{font-size:12px;color:var(--mute);display:flex;align-items:center;gap:6px}
.tile .lab i{width:8px;height:8px;border-radius:50%;display:inline-block}
.tile .val{font-size:26px;font-weight:620;margin-top:6px;letter-spacing:-.01em;white-space:nowrap}
.tile .sub{font-size:12px;color:var(--ink2);margin-top:2px;min-height:15px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.meter{height:8px;border-radius:4px;background:var(--grid);margin-top:10px;overflow:hidden}
.meter i{display:block;height:100%;border-radius:4px}
.meter.hatch{background:repeating-linear-gradient(45deg,var(--grid) 0 4px,transparent 4px 8px)}
.bars{display:flex;gap:3px;align-items:flex-end;height:26px;margin-top:6px}
.bars i{width:7px;border-radius:2px;background:var(--grid)}.bars i.on{background:var(--ink2)}
.alerts{margin-bottom:12px;grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
.alert{display:flex;gap:12px;align-items:center;border-left:4px solid var(--warn)}
.alert.crit{border-left-color:var(--crit)}
.alert .big{font-size:26px;font-weight:650;white-space:nowrap}.alert .what{font-size:13px;color:var(--ink2)}
.alert .act{margin-left:auto;font-size:12px;background:color-mix(in srgb,var(--warn) 22%,transparent);padding:5px 9px;border-radius:999px;white-space:nowrap}
.alert.crit .act{background:color-mix(in srgb,var(--crit) 16%,transparent)}
h2{font-size:13px;font-weight:600;color:var(--ink2);margin:0 0 10px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.legend{display:flex;gap:12px;flex-wrap:wrap;font-weight:400;color:var(--ink2);font-size:12px}
.legend span{display:inline-flex;align-items:center;gap:5px}.legend b{width:10px;height:10px;border-radius:3px;display:inline-block}
.legend b.hatch{background:repeating-linear-gradient(45deg,var(--mute) 0 2px,transparent 2px 4px)}
.legend b.hatch.test{background:repeating-linear-gradient(-45deg,var(--warn) 0 2px,transparent 2px 4px)}
b.pdot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--talk);margin-right:3px;vertical-align:1px}
.section{margin-top:12px}
.two{grid-template-columns:2fr 1fr}@media (max-width:760px){.two{grid-template-columns:1fr}}
.music{background:var(--music)}.book{background:var(--book)}.radio{background:var(--radio)}.call{background:var(--call)}
.talk{background:var(--talk)}.answer{background:var(--answer)}.online{background:var(--online)}
.strip{position:relative}
.lanes{position:relative;display:grid;gap:6px}
.lane{position:relative;height:22px;border-radius:4px;background:var(--page);overflow:hidden}
.seg{position:absolute;top:0;height:100%;min-width:2px;border-radius:2px;display:block}
.seg.talk{height:50%}.seg.answer{top:50%;height:50%}
.band{position:absolute;top:-3px;bottom:-3px;min-width:3px;border-radius:3px;display:block;
  background:repeating-linear-gradient(45deg,color-mix(in srgb,var(--mute) 45%,transparent) 0 3px,transparent 3px 7px);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--mute) 50%,transparent)}
.band.test{background:repeating-linear-gradient(-45deg,color-mix(in srgb,var(--warn) 45%,transparent) 0 3px,transparent 3px 7px);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--warn) 55%,transparent)}
.vline{position:absolute;top:0;bottom:0;width:1px;background:var(--grid)}
.now{position:absolute;top:-4px;bottom:-4px;width:0;border-left:1.5px dashed var(--ink)}
.hours{position:relative;height:16px;margin-top:6px}
.hours span,.xlab span{position:absolute;transform:translateX(-50%);font-size:11px;color:var(--mute);white-space:nowrap}
.hours span:first-child{transform:none}.hours span:last-child{transform:translateX(-100%)}
.plot{position:relative;height:170px;margin-left:34px}
.ytick{position:absolute;left:0;right:0;border-top:1px solid var(--grid)}
.ytick span{position:absolute;right:100%;margin-right:6px;transform:translateY(-50%);font-size:11px;color:var(--mute);white-space:nowrap}
.cols{position:absolute;inset:0;display:flex}
.col{flex:1;display:flex;align-items:flex-end;justify-content:center}
.stack{width:56%;display:flex;flex-direction:column-reverse;gap:2px;height:100%;justify-content:flex-start}
.stack i{display:block;border-radius:3px;min-height:2px}
.nolog{width:96%;max-width:72px;height:40px;border:1px dashed var(--mute);border-radius:4px;display:grid;place-items:center;font-size:11px;color:var(--mute);white-space:nowrap;overflow:hidden}
.xrow{display:flex;margin-left:34px;margin-top:6px}
.xrow div{flex:1;min-width:0;text-align:center;font-size:12px;color:var(--ink2);white-space:nowrap}.xrow small{display:block;font-size:11px;color:var(--mute)}
@media (max-width:480px){.xrow div{font-size:10.5px}.hours span.odd{display:none}.nolog{font-size:9px}}
.thresh{position:absolute;left:0;right:0;border-top:1.5px dashed var(--warn)}
.slots{position:absolute;inset:0;display:flex;align-items:flex-end;gap:2px}
.slot{flex:1;height:100%;display:flex;align-items:flex-end}
.slot i{display:block;width:100%;border-radius:2px;background:var(--answer);min-height:2px}
.slot i.over{background:var(--warn)}
.xlab{position:relative;height:16px;margin:6px 0 0 34px}
.gauge{position:relative;max-width:300px;margin:0 auto}
.gauge svg{display:block;width:100%;height:auto}
.gauge .num{position:absolute;left:0;right:0;bottom:26px;text-align:center;font-size:34px;font-weight:650}
.gauge .of{position:absolute;left:0;right:0;bottom:6px;text-align:center;font-size:12px;color:var(--mute)}
.calls .total{font-size:44px;font-weight:650;line-height:1}
.calls .span{color:var(--mute);font-size:12px;margin:2px 0 12px}
.person{display:flex;align-items:center;gap:10px;margin:6px 0}
.person .ini{width:30px;height:30px;border-radius:50%;background:color-mix(in srgb,var(--call) 30%,transparent);display:grid;place-items:center;font-weight:650}
.person .name{flex:1}.person .n{font-weight:620;min-width:2ch;text-align:right}
.person .ticks{display:flex;gap:3px}.person .ticks i{width:8px;height:18px;border-radius:2px;background:var(--call);display:inline-block}
.note{color:var(--mute);font-size:12px;margin-top:14px;display:flex;gap:16px;flex-wrap:wrap}
#tip{position:fixed;pointer-events:none;background:var(--card);color:var(--ink);box-shadow:0 0 0 1px var(--ring),0 6px 18px rgba(0,0,0,.12);border-radius:8px;padding:6px 9px;font-size:12px;display:none;z-index:9;max-width:260px}
"""

SCRIPT = """
(function(){var m=document.querySelector("main");var at=Date.parse(m.getAttribute("data-checked"));
var every=parseFloat(m.getAttribute("data-interval"))||300;
if(at&&Date.now()>at+(3*every+60)*1000&&m.className.indexOf("stale")<0){m.className+=" stale";
var d=m.querySelector(".dot");d.className="dot stale";m.querySelector("h1").textContent="Stopped checking";
m.querySelector(".when").textContent="at "+m.getAttribute("data-checked-local")}})();
(function(){var tip=document.getElementById("tip");
function show(e){var t=e.target.closest&&e.target.closest("[data-tip]");if(!t){tip.style.display="none";return}
tip.textContent=t.getAttribute("data-tip");tip.style.display="block";
var x=Math.min(e.clientX+12,innerWidth-tip.offsetWidth-8);tip.style.left=x+"px";tip.style.top=(e.clientY+14)+"px"}
document.addEventListener("pointermove",show);document.addEventListener("pointerdown",show);
document.addEventListener("pointerleave",function(){tip.style.display="none"})})();
"""


def _legend(kinds: list[str]) -> str:
    parts = []
    for k in kinds:
        if k in ("bench", "test"):
            parts.append(f'<span><b class="hatch {k}"></b>{_t(k)}</span>')
        elif k == "presses":
            parts.append('<span><b class="pdot"></b>Presses</span>')
        else:
            parts.append(f'<span><b class="{k}"></b>{_t(KIND_LABEL[k])}</span>')
    return f'<span class="legend">{"".join(parts)}</span>'


def _cards(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    cards = "".join(
        f'<div class="card alert {_a(it["severity"])}" data-id="{_a(it["id"])}">'
        f'<div><div class="big">{_t(it["big"])}</div><div class="what">{_t(it["label"])}</div></div>'
        f'<span class="act">{_t(it["chip"])}</span></div>'
        for it in items
    )
    return f'<div class="grid alerts">{cards}</div>'


def _tile(label: str, val: str, sub: str = "", dot: str = "", extra: str = "") -> str:
    d = f'<i style="background:var(--{dot})"></i>' if dot else ""
    return (
        f'<div class="card tile"><div class="lab">{d}{_t(label)}</div>'
        f'<div class="val">{_t(val)}</div><div class="sub">{_t(sub)}</div>{extra}</div>'
    )


def _tiles(t: dict[str, Any]) -> str:
    w = t["walkie"]
    if w["online"] is None:
        walkie = _tile("Walkie", "Never", "not seen", "mute")
    elif w["online"]:
        walkie = _tile("Walkie", "Online", f"since {w['since']}" if w["since"] else "", "good")
    else:
        walkie = _tile(
            "Walkie", "Away", f"seen {w['last_seen']}" if w["last_seen"] else "", "crit"
        )
    wf = t["wifi"]
    bars = "".join(
        f'<i class="{"on" if n <= wf["bars"] else ""}" style="height:{6 + n * 5}px"></i>'
        for n in range(1, 5)
    )
    wifi = _tile(
        "Wi-Fi",
        f"{wf['rssi']} dBm" if wf["rssi"] is not None else "-",
        "",
        extra=f'<div class="bars" data-tip="{wf["bars"]} of 4 bars">{bars}</div>',
    )
    au = t["audio"]
    if au["gap_pct"] is None:
        audio = _tile("Audio gaps", "-", f"nothing played, {au['window_h']:g} h")
    else:
        fill = min(au["gap_pct"] / (2 * au["threshold_pct"]), 1.0) * 100
        color = "warn" if au["over"] else "good"
        audio = _tile(
            "Audio gaps",
            f"{au['gap_pct']:.1f} %",
            f"last {au['window_h']:g} h",
            extra=f'<div class="meter" data-tip="threshold {au["threshold_pct"]:g} %">'
            f'<i style="width:{max(3.0, fill):.1f}%;background:var(--{color})"></i></div>',
        )
    sv = t["server"]
    sub = ", ".join(x for x in (sv["up"], f"fw {sv['firmware']}" if sv["firmware"] else "") if x)
    if sv["running"] is None:
        server = _tile("Server", "?", sub, "mute")
    else:
        server = _tile(
            "Server", "Up" if sv["running"] else "Down", sub, "good" if sv["running"] else "crit"
        )
    battery = _tile("Battery", "-", "not wired", extra='<div class="meter hatch"></div>')
    oa = t["openai"]
    if oa["balance_left_usd"] is not None:
        days = f", {oa['days_left']:.0f} d" if oa["days_left"] is not None else ""
        openai = _tile(
            "OpenAI",
            f"${oa['balance_left_usd']:.2f}" + ("?" if oa["partial"] else ""),
            f"left{days}",
        )
    else:
        openai = _tile("OpenAI today", f"${oa['today_usd']:.2f}", "balance not set")
    return f'<div class="grid tiles">{walkie}{wifi}{audio}{server}{battery}{openai}</div>'


def _seg(kind: str, s: float, e: float, label: str = "") -> str:
    tip = f"{KIND_LABEL[kind]}{', ' + label if label else ''}: {_hm(s)} to {_hm(e)} ({_dur((e - s) * 60)})"
    return (
        f'<i class="seg {kind}" style="left:{_pct(s)};width:{_pct(e - s)}" '
        f'data-s="{s}" data-e="{e}" data-tip="{_a(tip)}"></i>'
    )


def _today(d: dict[str, Any]) -> str:
    online = "".join(_seg("online", s, e) for s, e in d["online"])
    voices = "".join(_seg("talk", s, e) for s, e in d["talk"]) + "".join(
        _seg("answer", s, e) for s, e in d["answer"]
    )
    media = "".join(_seg(m["kind"], m["s"], m["e"], m["label"]) for m in d["media"])
    bands = "".join(
        f'<i class="band {_a(w["kind"])}" style="left:{_pct(w["s"])};width:{_pct(w["e"] - w["s"])}" '
        f'data-tip="{_a(w["kind"] + ": " + w["what"] + ", " + _hm(w["s"]) + " to " + _hm(w["e"]))}"></i>'
        for w in d["windows"]
    )
    vlines = "".join(
        f'<span class="vline" style="left:{_pct(h * 60)}"></span>' for h in range(3, 24, 3)
    )
    now = f'<span class="now" style="left:{_pct(d["now_min"])}" data-tip="now {_hm(d["now_min"])}"></span>'
    hours = "".join(
        f'<span class="{"odd" if h % 6 else ""}" style="left:{_pct(h * 60)}">{h:02d}:00</span>'
        for h in range(0, 25, 3)
    )
    return (
        f'<div class="strip" data-date="{_a(d["date"])}"><div class="lanes">{vlines}'
        f'<div class="lane" data-lane="online">{online}</div>'
        f'<div class="lane" data-lane="voices">{voices}</div>'
        f'<div class="lane" data-lane="media">{media}</div>{bands}{now}</div>'
        f'<div class="hours">{hours}</div></div>'
    )


def _axis(top: float) -> tuple[float, float]:
    """(axis top, step): a round step, at most 5 ticks, the top a multiple."""
    for step in (0.5, 1, 2, 5, 10, 20, 25, 50):
        if top / step <= 4:
            return step * math.ceil(top / step), step
    return 100.0, 25.0


def _ticks(top: float, step: float, unit: str) -> str:
    out = []
    n = 0
    while n * step <= top + 1e-9:
        v = n * step
        out.append(
            f'<div class="ytick" style="bottom:{v / top * 100:.2f}%"><span>{v:g}{unit}</span></div>'
        )
        n += 1
    return "".join(out)


def _week(w: dict[str, Any]) -> str:
    top, step = _axis(float(w["max_h"]))
    cols, xs = [], []
    for d in w["days"]:
        if not d["has_logs"]:
            cols.append('<div class="col"><div class="nolog">no logs</div></div>')
            xs.append(f"<div>{_t(d['label'])}<small>&nbsp;</small></div>")
            continue
        parts = "".join(
            f'<i class="{k}" style="height:{v / top * 100:.2f}%" data-h="{v}" '
            f'data-tip="{_a(d["label"] + ", " + KIND_LABEL[k] + ": " + _dur(v * 3600))}"></i>'
            for k, v in d["hours"].items()
            if v > 0
        )
        cols.append(f'<div class="col"><div class="stack">{parts}</div></div>')
        xs.append(
            f'<div>{_t(d["label"])}<small data-tip="{d["presses"]} presses">'
            f'<b class="pdot"></b>{d["presses"]}</small></div>'
        )
    return (
        f'<div class="plot">{_ticks(top, step, " h")}<div class="cols">{"".join(cols)}</div></div>'
        f'<div class="xrow">{"".join(xs)}</div>'
    )


def _gauge(b: dict[str, Any]) -> str:
    used_s = b["used_s"]
    allowance = b["allowance_h"]
    frac = 0.02 if not allowance or used_s is None else min(used_s / 3600 / allowance, 1.0)
    frac = max(frac, 0.02)
    cx, cy, r = 150, 140, 110

    def point(f: float) -> tuple[float, float]:
        a = math.pi * (1 - f)
        return cx + r * math.cos(a), cy - r * math.sin(a)

    def arc(f0: float, f1: float, color: str) -> str:
        (x0, y0), (x1, y1) = point(f0), point(f1)
        return (
            f'<path d="M{x0:.1f} {y0:.1f} A{r} {r} 0 0 1 {x1:.1f} {y1:.1f}" fill="none" '
            f'stroke="var(--{color})" stroke-width="18" stroke-linecap="round"/>'
        )

    if used_s is None:
        num, of = "-", "not readable"
    else:
        num = _dur(used_s) if used_s >= 60 else f"{round(used_s)} s"
        of = f"of {allowance:g} h" if allowance else "used, allowance not set"
    return (
        f'<div class="gauge" data-used-s="{used_s}" data-allowance-h="{allowance}">'
        f'<svg viewBox="0 0 300 170" role="img" aria-label="{_a(num + " " + of)}">'
        f"{arc(0, 1, 'grid')}{arc(0, frac, 'book') if used_s is not None else ''}</svg>"
        f'<div class="num">{_t(num)}</div><div class="of">{_t(of)}</div></div>'
    )


def _gaps(g: dict[str, Any]) -> str:
    th = float(g["threshold_pct"])
    worst = max((s["gap_pct"] or 0 for s in g["slots"]), default=0)
    top, step = _axis(min(100.0, max(10.0, 2 * th, worst)))
    slots = []
    for s in g["slots"]:
        if s["gap_pct"] is None:
            tip = (
                f"{s['start']}: nothing played"
                if not s["played_s"]
                else f"{s['start']}: {s['played_s']} s played"
            )
            slots.append(
                f'<div class="slot" data-start="{s["start"]}" data-tip="{_a(tip)}"></div>'
            )
            continue
        h = min(s["gap_pct"], top) / top * 100
        over = " over" if s["gap_pct"] >= th else ""
        tip = f"{s['start']}: {s['gap_pct']} % gaps of {_dur(s['played_s'])} played"
        slots.append(
            f'<div class="slot" data-start="{s["start"]}" data-tip="{_a(tip)}">'
            f'<i class="{over.strip()}" style="height:{h:.2f}%" data-pct="{s["gap_pct"]}"></i></div>'
        )
    n = len(g["slots"])
    xl = "".join(
        f'<span style="left:{(k + 0.5) / n * 100:.2f}%">{_t(s["start"])}</span>'
        for k, s in enumerate(g["slots"])
        if k % 4 == 0
    )
    return (
        f'<div class="plot">{_ticks(top, step, "%")}'
        f'<div class="thresh" style="bottom:{th / top * 100:.2f}%" data-tip="threshold {th:g} %"></div>'
        f'<div class="slots">{"".join(slots)}</div></div><div class="xlab">{xl}</div>'
    )


def _calls(c: dict[str, Any]) -> str:
    rows = "".join(
        f'<div class="person"><span class="ini">{_t(p["name"][:1])}</span>'
        f'<span class="name">{_t(p["name"])}</span><span class="ticks">'
        f'{"<i></i>" * min(p["count"], 10)}</span><span class="n">{p["count"]}</span></div>'
        for p in c["people"]
    )
    return (
        f'<div class="calls"><div class="total">{c["total"]}</div>'
        f'<div class="span">last {c["days"]} days</div>{rows}</div>'
    )


def _head(status: dict[str, Any]) -> tuple[str, str]:
    """The opening of <body>: <main> with what the inline staleness check
    needs, and the status line."""
    stale = bool(status.get("stale"))
    dot = "stale" if stale else status["status"]
    head = "Stopped checking" if stale else status["headline"]
    when = f"at {status['checked']}" if stale else f"checked {status['checked']}"
    interval = max(60, round(float(status.get("interval_s") or 300)))
    opening = (
        f'<main class="{"stale" if stale else ""}" data-status="{_a(dot)}" '
        f'data-checked="{_a(status["checked_at"])}" data-checked-local="{_a(status["checked"])}" '
        f'data-interval="{interval}">'
        f'<div class="top"><span class="dot {_a(dot)}"></span><h1>{_t(head)}</h1>'
        f'<span class="when">{_t(when)}</span></div>'
    )
    return opening, str(interval)


def _document(interval: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{interval}">
<meta name="color-scheme" content="light dark">
<title>The walkie</title>
<style>{CSS}</style>
</head>
<body>
{body}
<div id="tip"></div>
<script>{SCRIPT}</script>
</body>
</html>
"""


def render(status: dict[str, Any]) -> str:
    opening, interval = _head(status)
    alerts = "Alerts: Telegram" if status["alerts"] == "telegram" else "Alerts: log only"
    logs_from = f"Logs since {status['logs_from']}" if status.get("logs_from") else ""
    month = status["books"]["month"]
    legend_today = _legend(
        ["online", "talk", "answer", "music", "book", "radio", "call", "bench", "test"]
    )
    legend_week = _legend(["music", "book", "radio", "call", "presses"])
    body = f"""{opening}
<div class="body">
{_cards(status["items"])}
{_tiles(status["tiles"])}
<section class="card section"><h2>Today, hour by hour {legend_today}</h2>{_today(status["today"])}</section>
<div class="grid two section">
<section class="card"><h2>Last 7 days {legend_week}</h2>{_week(status["week"])}</section>
<section class="card"><h2>Audiobook hours, {_t(month)}</h2>{_gauge(status["books"])}</section>
</div>
<div class="grid two section">
<section class="card"><h2>Audio gaps per half hour</h2>{_gaps(status["gaps"])}</section>
<section class="card"><h2>Calls</h2>{_calls(status["calls"])}</section>
</div>
<div class="note"><span>{_t(alerts)}</span><span>{_t(logs_from)}</span><span>Battery not wired</span><a href="status.json" style="color:inherit">status.json</a></div>
</div>
</main>"""
    return _document(interval, body)


def render_fallback(status: dict[str, Any]) -> str:
    """The header and the cards, when the full page couldn't be built."""
    opening, interval = _head(status)
    body = f"""{opening}
<div class="body">
{_cards(status["items"])}
<div class="note"><span>Page error: {_t(status.get("error", "?"))}</span><a href="status.json" style="color:inherit">status.json</a></div>
</div>
</main>"""
    return _document(interval, body)

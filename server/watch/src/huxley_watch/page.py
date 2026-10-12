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
phone width; the gauge alone is SVG. Self-contained: no external file.
The inline script turns `data-tip` into a tooltip (hover or tap), and
runs the archive: the day picker above the strip (it swaps in another
day's strip from /day/YYYY-MM-DD.json), the day's list under it (a click
on a block scrolls it to that time and marks the rows there), the players
(audio/...), and the reload, done only when nothing is open or playing.
"""

from __future__ import annotations

import html
import math
from datetime import date
from typing import Any

from huxley_watch.icons import icon, sprite

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
.ico{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex:none;display:block}
.picker{display:flex;align-items:center;gap:6px;margin:0 0 10px}
.pk{appearance:none;border:0;background:var(--page);color:var(--ink);border-radius:999px;height:28px;min-width:28px;padding:0 10px;display:inline-grid;place-items:center;font:inherit;font-size:12px;cursor:pointer;box-shadow:0 0 0 1px var(--ring)}
.pk:disabled{opacity:.35;cursor:default}.pk .ico{width:14px;height:14px}
.pd{font-size:13px;font-weight:600;min-width:9ch;text-align:center;font-variant-numeric:tabular-nums}
.picker.err .pd{color:var(--crit)}
.strip .lane,.band{cursor:pointer}
.seg.sel{box-shadow:inset 0 0 0 2px var(--ink);min-width:6px;z-index:2}
.panel{margin-top:14px;border-top:1px solid var(--grid);padding-top:8px;position:relative}
.panel .x{position:absolute;right:0;top:10px;padding:0;z-index:3}
.pbody{max-height:min(70vh,640px);overflow:auto;overscroll-behavior:contain;padding-right:36px}
.rows{display:grid;gap:2px}
.li{display:grid;grid-template-columns:64px 22px minmax(0,1fr);align-items:center;gap:8px;padding:4px 6px;border-radius:7px;min-height:30px}
.li.near{background:color-mix(in srgb,var(--ink) 7%,transparent)}
.li .at{font-size:12px;color:var(--mute);font-variant-numeric:tabular-nums;white-space:nowrap}
.ic{display:grid;place-items:center;color:var(--ink2)}.ic.w-him{color:var(--talk)}.ic.w-huxley{color:var(--answer)}.ic.w-call{color:var(--call)}
.ic .sw{width:12px;height:12px}
.tx{font-size:14px;overflow-wrap:anywhere}
.ic.w-huxley+.tx{color:var(--ink2)}
.tl{font-size:13px;color:var(--ink2);overflow-wrap:anywhere}.tl .tn{font-weight:600;font-family:ui-monospace,Menlo,monospace;font-size:12px}.tl .arg{color:var(--mute)}
.pl{display:flex;align-items:center;gap:10px;min-width:0}
.pl .name{font-size:13px;color:var(--ink2)}
.st{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px}.st .name{font-weight:600}
.sw{width:12px;height:12px;border-radius:3px;display:inline-block;flex:none}
.sw.hatch{background:repeating-linear-gradient(45deg,var(--mute) 0 2px,transparent 2px 4px)}
.fig{font-variant-numeric:tabular-nums;color:var(--ink2)}
.dash{width:14px;border-top:1.5px solid var(--mute);display:inline-block}
.play{appearance:none;border:0;padding:0;width:26px;height:26px;border-radius:50%;display:grid;place-items:center;flex:none;background:var(--ink);color:var(--card);cursor:pointer}
.play .ico{width:12px;height:12px}.play .i-pause,.play.on .i-play{display:none}.play.on .i-pause{display:block}
.play.bad{background:var(--crit)}
.track{position:relative;flex:1;max-width:220px;height:5px;border-radius:3px;background:var(--grid);cursor:pointer;overflow:hidden}
.prog{position:absolute;left:0;top:0;height:100%;width:0;background:var(--ink);display:block}
.len{font-size:12px;color:var(--mute);font-variant-numeric:tabular-nums;white-space:nowrap}
.gap{display:flex;align-items:center;gap:8px;margin:6px 0 6px 72px;font-size:11px}
.gap::before,.gap::after{content:"";flex:1;border-top:1px dashed var(--grid)}.gap::before{flex:0 0 18px}
.gap .fig{color:var(--mute)}
details.bgroup>summary{list-style:none;cursor:pointer}details.bgroup>summary::-webkit-details-marker{display:none}
details.bgroup>summary .fig{color:var(--mute)}
details.bgroup>.li{opacity:.45;filter:grayscale(1)}
details.bgroup[open]>summary{opacity:.7}
@media (max-width:480px){.li{grid-template-columns:52px 20px minmax(0,1fr);gap:6px}.li .at{font-size:11px}.pbody{padding-right:30px}}
"""

SCRIPT = (
    """
(function(){var m=document.querySelector("main");var at=Date.parse(m.getAttribute("data-checked"));
var every=parseFloat(m.getAttribute("data-interval"))||300;
function old(t){return Date.now()>t+(3*every+60)*1000}
function mark(){if(m.className.indexOf("stale")>=0)return;m.className+=" stale";
var d=m.querySelector(".dot");d.className="dot stale";m.querySelector("h1").textContent="Stopped checking";
m.querySelector(".when").textContent="at "+m.getAttribute("data-checked-local")}
function stale(){if(!at||!old(at)||m.className.indexOf("stale")>=0)return;
fetch("status.json",{cache:"no-store"}).then(function(r){return r.json()}).then(function(j){var c=Date.parse(j.checked_at);
if(c&&!j.stale&&!old(c))at=c;else mark()}).catch(mark)}
stale();setInterval(stale,30000)})();
(function(){var tip=document.getElementById("tip");
function show(e){var t=e.target.closest&&e.target.closest("[data-tip]");if(!t){tip.style.display="none";return}
tip.textContent=t.getAttribute("data-tip");tip.style.display="block";
var x=Math.min(e.clientX+12,innerWidth-tip.offsetWidth-8);tip.style.left=x+"px";tip.style.top=(e.clientY+14)+"px"}
document.addEventListener("pointermove",show);document.addEventListener("pointerdown",show);
document.addEventListener("pointerleave",function(){tip.style.display="none"})})();
"""
    + """
(function(){var sec=document.getElementById("day");if(!sec)return;
var pk=sec.querySelector(".picker"),panel=document.getElementById("panel"),body=panel.querySelector(".pbody");
var today=pk.getAttribute("data-today"),cur=today,shown="",shownDay="",days={},got={},playing=null;
function q(s){return sec.querySelector(s)}
function load(d,cb){if(days[d]&&(d!==today||Date.now()-got[d]<60000))return cb(days[d]);
fetch("day/"+d+".json",{cache:"no-store"}).then(function(r){if(!r.ok)throw new Error(r.status);return r.json()})
.then(function(j){days[d]=j;got[d]=Date.now();pk.classList.remove("err");cb(j)}).catch(function(){pk.classList.add("err")})}
function unsel(){var s=q(".seg.sel");if(s)s.classList.remove("sel");body.querySelectorAll(".near").forEach(function(x){x.classList.remove("near")})}
function list(j,fresh){var same=shownDay===j.date;
if(fresh||!same||(shown!==j.list_html&&!playing)){stop();body.innerHTML=j.list_html;shown=j.list_html;shownDay=j.date}
panel.hidden=false}
function close(){stop();panel.hidden=true;unsel()}
function set(k,v){var b=pk.querySelector('[data-go="'+k+'"]');b.disabled=!v;b.setAttribute("data-day",v||"")}
function go(d){if(!d)return;load(d,function(j){cur=d;unsel();q(".strip").outerHTML=j.strip_html;
q("#dayname").textContent=j.is_today?"Today":j.label;q(".pd").textContent=j.label;
set("prev",j.prev);set("next",j.next);pk.querySelector('[data-go="today"]').disabled=j.is_today;
list(j,true);body.scrollTop=0})}
function scrollTo(el){body.scrollTop+=el.getBoundingClientRect().top-body.getBoundingClientRect().top-8;
var r=panel.getBoundingClientRect();if(r.top>innerHeight-160||r.bottom<0)panel.scrollIntoView({behavior:"smooth",block:"nearest"})}
function near(s,e,pad){var hit=[],best=null,d=1e9;body.querySelectorAll(".li[data-t]").forEach(function(x){var t=+x.getAttribute("data-t");
if(t>=s-pad&&t<=e+pad)hit.push(x);var v=Math.min(Math.abs(t-s),Math.abs(t-e));if(v<d){d=v;best=x}});
if(!hit.length&&best)hit=[best];hit.slice(0,80).forEach(function(x){x.classList.add("near");var g=x.parentNode;
if(g.tagName==="DETAILS"&&x.tagName!=="SUMMARY")g.open=true});if(hit.length)scrollTo(hit[0])}
sec.addEventListener("click",function(ev){var t=ev.target,b=t.closest(".picker button");
if(b){go(b.getAttribute("data-go")==="today"?today:b.getAttribute("data-day"));return}
if(t.closest(".panel .x")){close();return}
var band=t.closest(".strip .band"),seg=t.closest(".strip .seg"),lane=t.closest(".strip .lane");
if(!band&&!lane)return;var box=q(".strip .lanes").getBoundingClientRect(),per=1440/box.width;
var m=(ev.clientX-box.left)*per,s=m,e=m,pad=14*per;
if(band){s=+band.getAttribute("data-ws");e=+band.getAttribute("data-we");pad=0.5}
else if(seg&&!seg.classList.contains("online")){s=+seg.getAttribute("data-s");e=+seg.getAttribute("data-e");pad=1}
load(cur,function(j){list(j,false);unsel();if(seg&&!band&&!seg.classList.contains("online"))seg.classList.add("sel");
near(s,e,pad)})});
document.addEventListener("keydown",function(e){if(e.key==="Escape"&&!panel.hidden)close()});
function fmt(x){x=Math.max(0,Math.round(x||0));var h=Math.floor(x/3600),mm=Math.floor(x%3600/60),ss=x%60;
return(h?h+":"+(mm<10?"0":"")+mm:mm)+":"+(ss<10?"0":"")+ss}
function dur(p){var d=p.a.duration;return isFinite(d)&&d>0?d:p.ms/1000}
function stop(){if(!playing)return;playing.a.pause();playing.b.classList.remove("on");playing=null}
panel.addEventListener("click",function(ev){var b=ev.target.closest("button.play");
if(!b){var tr=ev.target.closest(".track");if(tr&&playing&&playing.host.contains(tr)){var r=tr.getBoundingClientRect();
playing.a.currentTime=Math.max(0,(ev.clientX-r.left)/r.width)*dur(playing)}return}
if(playing&&playing.b===b){stop();return}stop();
var host=b.closest(".li"),bar=host.querySelector(".prog"),len=host.querySelector(".len");
var p={a:b._a||new Audio(b.getAttribute("data-src")),b:b,host:host,ms:+b.getAttribute("data-ms")||0};
if(!b._a){b._a=p.a;p.a.addEventListener("timeupdate",function(){var d=dur(p);
if(bar)bar.style.width=(d?Math.min(100,p.a.currentTime/d*100):0)+"%";if(len)len.textContent=fmt(p.a.currentTime)+" / "+fmt(d)});
p.a.addEventListener("ended",function(){if(playing&&playing.a===p.a)stop();if(bar)bar.style.width="0"});
p.a.addEventListener("pause",function(){if(playing&&playing.a===p.a){b.classList.remove("on");playing=null}});
p.a.addEventListener("play",function(){if(!playing||playing.a!==p.a){stop();playing=p;b.classList.add("on")}});
p.a.addEventListener("error",function(){b.classList.remove("on");b.classList.add("bad");if(playing&&playing.a===p.a)playing=null})}
playing=p;b.classList.add("on");var pr=p.a.play();if(pr&&pr.catch)pr.catch(function(){b.classList.remove("on");b.classList.add("bad");
if(playing===p)playing=null})});
var every=(parseFloat(document.querySelector("main").getAttribute("data-interval"))||300)*1000,touched=Date.now();
["pointerdown","keydown","wheel","touchstart"].forEach(function(n){document.addEventListener(n,function(){touched=Date.now()},{passive:true})});
function tick(){var idle=Date.now()-touched>15*60000;
if(!playing&&(idle||(panel.hidden&&cur===today)))location.reload();else setTimeout(tick,30000)}
setTimeout(tick,every)})();
"""
)


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
    elif sv["running"] and sv.get("ready") is False:
        # Up, but its own /health says the persona or the voice session isn't.
        server = _tile("Server", "Up", ", ".join(x for x in ("not ready", sub) if x), "warn")
    else:
        server = _tile(
            "Server", "Up" if sv["running"] else "Down", sub, "good" if sv["running"] else "crit"
        )
    bt = t["battery"]
    if bt["pct"] is not None:
        pct = max(0, min(100, int(bt["pct"])))
        color = "crit" if pct < bt["crit_pct"] else "warn" if pct < bt["low_pct"] else "good"
        bsub = (
            f"at {bt['at']}"
            if bt["stale"] and bt["at"]
            else (f"{bt['mv'] / 1000:.2f} V" if bt["mv"] else "")
        )
        battery = _tile(
            "Battery",
            f"{pct} %",
            bsub,
            extra=f'<div class="meter" data-tip="{_a(bt["at"] or "")}">'
            f'<i style="width:{max(3, pct)}%;background:var(--{color})"></i></div>',
        )
    else:
        battery = _tile(
            "Battery",
            "-",
            "not wired" if bt["wired"] is False else "not reported",
            extra='<div class="meter hatch"></div>',
        )
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


def strip_html(d: dict[str, Any]) -> str:
    """A day's strip (views.strip): today's on the page, any day's in
    /day/YYYY-MM-DD.json for the archive. Blocks carry their minutes
    (data-s, data-e; bands data-ws, data-we) for the click that opens a
    panel."""
    online = "".join(_seg("online", s, e) for s, e in d["online"])
    voices = "".join(_seg("talk", s, e) for s, e in d["talk"]) + "".join(
        _seg("answer", s, e) for s, e in d["answer"]
    )
    media = "".join(_seg(m["kind"], m["s"], m["e"], m["label"]) for m in d["media"])
    bands = "".join(
        f'<i class="band {_a(w["kind"])}" style="left:{_pct(w["s"])};width:{_pct(w["e"] - w["s"])}" '
        f'data-ws="{w["s"]}" data-we="{w["e"]}" '
        f'data-tip="{_a(w["kind"] + ": " + w["what"] + ", " + _hm(w["s"]) + " to " + _hm(w["e"]))}"></i>'
        for w in d["windows"]
    )
    vlines = "".join(
        f'<span class="vline" style="left:{_pct(h * 60)}"></span>' for h in range(3, 24, 3)
    )
    now = (
        f'<span class="now" style="left:{_pct(d["now_min"])}" data-tip="now {_hm(d["now_min"])}"></span>'
        if d.get("now_min") is not None
        else ""
    )
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


def _day(today: dict[str, Any], bounds: dict[str, Any] | None) -> str:
    """The day section: the picker above the strip, the strip, and under it
    the day's list (daylist.py's markup from /day/YYYY-MM-DD.json), shown
    by a click on a block, which scrolls it to that time."""
    label = date.fromisoformat(today["date"]).strftime("%a %d %b")
    prev = (bounds or {}).get("prev") or ""
    picker = (
        f'<div class="picker" data-today="{_a(today["date"])}">'
        f'<button class="pk" type="button" data-go="prev" data-day="{_a(prev)}" '
        f'aria-label="previous day"{"" if prev else " disabled"}>{icon("prev")}</button>'
        f'<span class="pd">{_t(label)}</span>'
        f'<button class="pk" type="button" data-go="next" data-day="" aria-label="next day" disabled>'
        f"{icon('next')}</button>"
        f'<button class="pk" type="button" data-go="today" disabled>Today</button></div>'
    )
    panel = (
        '<div class="panel" id="panel" hidden>'
        f'<button class="pk x" type="button" aria-label="close">{icon("close")}</button>'
        '<div class="pbody"></div></div>'
    )
    return f"{picker}{strip_html(today)}{panel}"


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
<noscript><meta http-equiv="refresh" content="{interval}"></noscript>
<meta name="color-scheme" content="light dark">
<title>The walkie</title>
<style>{CSS}</style>
</head>
<body>
{sprite()}
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
    battery_note = (
        "<span>Battery not wired</span>" if status["tiles"]["battery"]["wired"] is False else ""
    )
    body = f"""{opening}
<div class="body">
{_cards(status["items"])}
{_tiles(status["tiles"])}
<section class="card section" id="day"><h2><span><span id="dayname">Today</span>, hour by hour</span> {legend_today}</h2>{_day(status["today"], status.get("archive"))}</section>
<div class="grid two section">
<section class="card"><h2>Last 7 days {legend_week}</h2>{_week(status["week"])}</section>
<section class="card"><h2>Audiobook hours, {_t(month)}</h2>{_gauge(status["books"])}</section>
</div>
<div class="grid two section">
<section class="card"><h2>Audio gaps per half hour</h2>{_gaps(status["gaps"])}</section>
<section class="card"><h2>Calls</h2>{_calls(status["calls"])}</section>
</div>
<div class="note"><span>{_t(alerts)}</span><span>{_t(logs_from)}</span>{battery_note}<a href="status.json" style="color:inherit">status.json</a></div>
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

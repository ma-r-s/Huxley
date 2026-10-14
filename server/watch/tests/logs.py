"""Synthetic server log lines in the exact shapes the server writes
(copied from real logs, 2026-10-10..12; names and numbers changed)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from huxley_watch.bench import Bench, Window
from huxley_watch.config import Thresholds
from huxley_watch.daylog import DAY_EVENTS
from huxley_watch.events import Event, make_events
from huxley_watch.rules import Inputs, make_inputs

TZ = ZoneInfo("America/Bogota")  # UTC-5, no DST
# Monday 2026-10-12 15:00 local
NOW = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
WALKIE_MAC = "44:1b:f6:85:26:80"
TWIN_MAC = "a0:f2:62:e2:fb:60"


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def line(event: str, at: datetime, level: str = "info", **fields: Any) -> str:
    return json.dumps({**fields, "level": level, "timestamp": iso(at), "event": event})


def ago(**kw: float) -> datetime:
    return NOW - timedelta(**kw)


# --- shapes ---------------------------------------------------------------


def starting(ts: datetime) -> str:
    return line("huxley_starting", ts)


def stopped(ts: datetime) -> list[str]:
    return [
        line("huxley_shutting_down", ts),
        line("huxley_stopped", ts),
        line("huxley_stopped", ts),
    ]


def connected(ts: datetime, conn: str, remote: str = "('192.168.68.69', 51749)") -> str:
    return line("client_connected", ts, remote=remote, language="es", persona=None, conn_id=conn)


def device(
    ts: datetime, conn: str, mac: str = WALKIE_MAC, version: str = "7ade19c8", rssi: int = -52
) -> str:
    return line(
        "client.device",
        ts,
        shell="firmware",
        board="esp32s3-audio-board",
        version=version,
        mac=mac,
        reset="USB",
        boot_counter=2,
        rssi=rssi,
        volume=50,
        conn_id=conn,
    )


def phone(ts: datetime, conn: str) -> str:
    return line(
        "client.device",
        ts,
        shell="native_app",
        audio="native",
        ua="Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X)",
        conn_id=conn,
    )


def disconnected(ts: datetime, conn: str) -> str:
    return line(
        "client_disconnected",
        ts,
        frames_tx=5489,
        frames_rx=843,
        bytes_tx=37864348,
        bytes_rx=3928571,
        duration_ms=987969,
        close_code=1006,
        close_reason=None,
        conn_id=conn,
    )


def evicted(ts: datetime, old: str, new: str) -> str:
    return line(
        "client_evicted",
        ts,
        old="('192.168.68.86', 51749)",
        old_conn_id=old,
        new="('192.168.68.86', 51750)",
        new_conn_id=new,
    )


def fw(ts: datetime, conn: str, text: str) -> str:
    return line(
        "client.huxley.firmware_log", ts, tag="hux_audio", line=text, ts=4019, conn_id=conn
    )


def alive(ts: datetime, conn: str, audio: int) -> str:
    return fw(
        ts,
        conn,
        f"spk.alive iters=100000 audio={audio} silence=89882 underruns=2 errs=0 ring_used=0 push_wait_max_ms=0",
    )


def underrun(ts: datetime, conn: str, silent_ms: int) -> str:
    return fw(ts, conn, f"spk.underrun silent_ms={silent_ms} count=1")


def info_rssi(ts: datetime, conn: str, rssi: int) -> str:
    return fw(ts, conn, f"info ring_used=0 volume=50 rssi={rssi}")


def walkie_session(start: datetime, end: datetime, conn: str, **device_kw: Any) -> list[str]:
    """Connected at start, the speaker reporting every 5 s (idle), until end."""
    out = [connected(start, conn), device(start + timedelta(milliseconds=80), conn, **device_kw)]
    t = start + timedelta(seconds=5)
    while t < end:
        out.append(alive(t, conn, 0))
        t += timedelta(seconds=5)
    return out


def playing(
    start: datetime, minutes: float, conn: str, gap_ms_per_min: int = 0, base: int = 0
) -> list[str]:
    """`minutes` of the speaker playing (50 chunks a second), with that many
    ms of gaps a minute, as spk.alive and spk.underrun lines."""
    out: list[str] = []
    chunks = base
    for m in range(int(minutes)):
        t = start + timedelta(minutes=m)
        for s in range(0, 60, 5):
            chunks += 250
            out.append(alive(t + timedelta(seconds=s + 5), conn, chunks))
        if gap_ms_per_min:
            out.append(underrun(t + timedelta(seconds=30), conn, gap_ms_per_min))
    return out


def cost(ts: datetime, usd: float, day_total: float) -> str:
    return line(
        "cost.response_done",
        ts,
        model="gpt-realtime-2.1-mini",
        cost_usd=usd,
        day_total_usd=day_total,
    )


def connection_failed(
    ts: datetime, last: str = "TimeoutError: timed out during opening handshake"
) -> str:
    return line(
        "connection_failed",
        ts,
        level="error",
        exception=(
            'Traceback (most recent call last):\n  File "/x/websockets/asyncio/client.py", '
            f"line 581, in __await_impl__\n    raise\n{last}"
        ),
    )


def session_connected(ts: datetime) -> str:
    return line("session_connected", ts, model="gpt-realtime-2.1-mini", language="es")


def press(ts: datetime, conn: str = "w1", turn: str = "a2e412d8") -> str:
    return line(
        "coord.ptt_start",
        ts,
        turn=turn,
        had_turn=False,
        prev_state=None,
        had_media=False,
        had_claim=False,
        will_interrupt=False,
        conn_id=conn,
    )


# --- building inputs ----------------------------------------------------------


def events(lines: list[str]) -> list[Event]:
    return make_events(lines)


def inputs(lines: list[str], now: datetime = NOW, **kw: Any) -> Inputs:
    th = kw.pop("th", Thresholds())
    return make_inputs(events(lines), now, TZ, th, **kw)


def flat(*groups: str | list[str]) -> list[str]:
    out: list[str] = []
    for g in groups:
        out.extend([g] if isinstance(g, str) else g)
    return out


# --- what the speaker played, him and Huxley ------------------------------------


def ptt_stop(
    ts: datetime, conn: str = "w1", turn: str = "a2db089e", committed: bool = True
) -> str:
    return line(
        "coord.ptt_stop",
        ts,
        turn=turn,
        frames=39,
        audio_ms=2340,
        committed=committed,
        peak_dbfs=0.0,
        rms_dbfs=-34.8,
        conn_id=conn,
    )


def commit(ts: datetime, conn: str = "w1") -> str:
    """The press's audio sent to the model (right after a committed release)."""
    return line("session.tx.commit", ts, conn_id=conn)


def speaking(ts: datetime, value: bool, conn: str | None = None) -> str:
    """The speaker-owned flag: the model's (no conn_id) or a stream's or
    call's pump (with one)."""
    if conn is None:
        return line("server.tx.model_speaking", ts, value=value)
    return line("server.tx.model_speaking", ts, value=value, conn_id=conn)


def audio_start(ts: datetime, turn: str = "24d007c6", owner: str = "user") -> str:
    """The model's audio for a turn begins (logged with model_speaking true)."""
    return line("coord.audio_start", ts, turn=turn, state="in_response", owner=owner)


def answer(start: datetime, end: datetime, turn: str = "24d007c6") -> list[str]:
    """Huxley speaking, as the server logs it."""
    return [speaking(start, True), audio_start(start, turn), speaking(end, False)]


def stream_started(ts: datetime, sid: str, label: str) -> str:
    return line("server.tx.stream_started", ts, stream_id=sid, label=label, preroll_ms=1560)


def stream_ended(ts: datetime, sid: str, reason: str = "interrupted") -> str:
    return line("server.tx.stream_ended", ts, stream_id=sid, end_reason=reason, conn_id="w1")


def play_started(ts: datetime, context: str) -> str:
    return line(
        "spotify.play_started",
        ts,
        skill="spotify",
        latency_ms=16,
        resumed=False,
        context=context,
        conn_id="w1",
    )


def radio_started(ts: datetime) -> str:
    return line("radio.stream_started", ts, skill="radio", station_id="caracol", conn_id="w1")


def audiobook_started(ts: datetime) -> str:
    return line(
        "audiobooks.stream_started",
        ts,
        skill="audiobooks",
        book_id="Author/Book.m4b",
        start=0.0,
        speed=1.0,
        conn_id="w1",
    )


def claim_started(ts: datetime, title: str) -> str:
    return line(
        "server.tx.claim_started", ts, claim_id="claim:active", skill="claim:active", title=title
    )


def claim_ended(ts: datetime) -> str:
    return line(
        "server.tx.claim_ended", ts, claim_id="claim:active", end_reason="natural", conn_id="w1"
    )


def app_ready(ts: datetime) -> str:
    return line("huxley_app_ready", ts, persona="Abuelo", disabled_skills=[], conn_id="w1")


def swap_committed(ts: datetime) -> str:
    return line("runtime.persona_swap_committed", ts, from_="abuelos", to="abuelos", conn_id="w1")


def bench_line(start: datetime, end: datetime | None, what: str) -> str:
    return json.dumps({"start": iso(start), "end": iso(end) if end else None, "what": what})


def bench(*windows: tuple[datetime, datetime, str]) -> Bench:
    return Bench(tuple(Window(s, e, w) for s, e, w in windows))


# --- the archive: transcripts, recordings, tools, call direction -------------


def transcript(ts: datetime, role: str, text: str) -> str:
    """Logged with no turn and no conn_id (remote-operation W3)."""
    return line("transcript", ts, role=role, text=text)


def segment(
    source: str,
    start: datetime,
    end: datetime,
    conn: str = "w1",
    path: str | None = None,
    ms: int = 1500,
    claim_id: str | None = None,
    label: str | None = None,
) -> str:
    """A recorder.segment line, its times with milliseconds as the recorder
    writes them; `path` relative to the persona's data dir."""

    def ms_iso(ts: datetime) -> str:
        return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"

    fields: dict[str, Any] = {
        "source": source,
        "label": label,
        "start": ms_iso(start),
        "end": ms_iso(end),
        "duration_ms": ms,
        "packets": ms // 60,
        "bytes": ms * 2,
        "path": path,
        "end_reason": "ptt_stop" if source == "mic" else "model_done",
        "cut_by_clear": False,
        "dropped": 0,
        "conn_id": conn,
    }
    if claim_id:
        fields["claim_id"] = claim_id
    return line("recorder.segment", end + timedelta(milliseconds=40), **fields)


def tool_call(ts: datetime, name: str) -> str:
    return line("session.rx.tool_call", ts, name=name, call_id="call_1eH3j8CgrUnZfIbg", args={})


def call_placed(ts: datetime, name: str) -> str:
    return line("telegram.call_started", ts, skill="telegram", name=name, user_id=1000000081)


def by_time(lines: list[str]) -> list[str]:
    """Log lines in time order, as a file holds them."""
    return sorted(lines, key=lambda x: json.loads(x)["timestamp"])


def day_events(lines: list[str]) -> list[Event]:
    """Parsed as the archive reads a day (daylog.DAY_EVENTS)."""
    return make_events(lines, DAY_EVENTS)

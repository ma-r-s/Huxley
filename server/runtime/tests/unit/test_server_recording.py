"""The AudioServer's recording taps, and the persona switch that turns
them on.

What the server hands the recorder must be what crossed the wire: the
speaker's Opus packets as they were written (not what `send_audio` was
given: a clear or a cancel drops queued audio), the mic's packets as
they arrived (before decoding or any gating), and the control frames in
the order the client got them. A fake recorder records the calls; one
test runs the real one end to end.
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
import threading
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
import structlog
from websockets.exceptions import ConnectionClosedError

from huxley.config import Settings
from huxley.runtime import Runtime
from huxley.server.opus_codec import (
    OpusDecoderStream,
    OpusEncoderStream,
    opus_unavailable_reason,
)
from huxley.server.recorder import Recorder
from huxley.server.server import AudioServer, _ConnInfo
from tests.unit.test_recorder import _ogg_packets

if TYPE_CHECKING:
    from pathlib import Path

_NO_OPUS = opus_unavailable_reason()
needs_opus = pytest.mark.skipif(
    _NO_OPUS is not None,
    reason=f"libopus not loadable ({_NO_OPUS}); brew install opus / apt install libopus0",
)

_SR = 24000


def _sine(ms: int, freq: float = 440.0) -> bytes:
    n = _SR * ms // 1000
    return struct.pack(
        f"<{n}h", *(int(8000 * math.sin(2 * math.pi * freq * i / _SR)) for i in range(n))
    )


class _Transport:
    def get_write_buffer_size(self) -> int:
        return 0

    def get_extra_info(self, name: str) -> Any:
        return None


class _WS:
    """Fake connection: `send` records first, then (once, if a gate is
    set) waits, like websockets writing before it waits on flow control."""

    def __init__(self) -> None:
        self.transport = _Transport()
        self.sent: list[str | bytes] = []
        self.gate: asyncio.Event | None = None

    async def send(self, msg: str | bytes) -> None:
        self.sent.append(msg)
        if self.gate is not None:
            gate, self.gate = self.gate, None
            await gate.wait()

    @property
    def packets(self) -> list[bytes]:
        return [m for m in self.sent if isinstance(m, bytes)]


class _ClientWS:
    """Fake socket for `_handle_connection`: inbound frames pushed by the
    test, `finish()` closes it."""

    def __init__(self) -> None:
        self.remote_address = ("test", 0)
        self.transport = _Transport()
        self.sent: list[str | bytes] = []
        self._inbound: asyncio.Queue[str | bytes | None] = asyncio.Queue()

    async def send(self, msg: str | bytes) -> None:
        self.sent.append(msg)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.finish()

    def finish(self) -> None:
        self._inbound.put_nowait(None)

    def __aiter__(self) -> _ClientWS:
        return self

    async def __anext__(self) -> str | bytes:
        item = await self._inbound.get()
        if item is None:
            raise ConnectionClosedError(None, None)
        return item

    @property
    def json(self) -> list[dict[str, Any]]:
        return [json.loads(m) for m in self.sent if isinstance(m, str)]


def _bare_server() -> tuple[AudioServer, AsyncMock]:
    on_audio_frame = AsyncMock()
    srv = AudioServer(
        host="127.0.0.1",
        port=0,
        on_wake_word=AsyncMock(),
        on_ptt_start=AsyncMock(),
        on_ptt_stop=AsyncMock(),
        on_audio_frame=on_audio_frame,
        on_reset=AsyncMock(),
    )
    return srv, on_audio_frame


def _server(opus: bool = True) -> tuple[AudioServer, _WS, AsyncMock]:
    """A server with one connection after its hello."""
    srv, on_audio_frame = _bare_server()
    ws = _WS()
    conn = _ConnInfo(
        conn_id="c1",
        remote="test",
        connected_at_ms=0,
        requested_persona=None,
        requested_language=None,
        ws=ws,  # type: ignore[arg-type]
    )
    if opus:
        conn.opus_tx = OpusEncoderStream()
        conn.opus_rx = OpusDecoderStream()
        conn.audio_format = "opus"
    conn.hello_sent = True
    srv._conn = conn
    return srv, ws, on_audio_frame


def _fake_recorder() -> MagicMock:
    return MagicMock(spec=Recorder)


def _speaker_packets(rec: MagicMock) -> list[bytes]:
    return [c.args[1] for c in rec.speaker_packet.call_args_list]


async def _until(cond: Any, timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


# --- Speaker: what was written --------------------------------------------------


@needs_opus
async def test_speaker_packets_are_recorded_as_written_in_wire_order() -> None:
    srv, ws, _ = _server()
    rec = _fake_recorder()
    srv.attach_recorder(rec)
    rec.reset_mock()
    await srv.send_model_speaking(True)
    await srv.send_audio(_sine(1000))
    await srv.send_model_speaking(False)
    assert _speaker_packets(rec) == ws.packets
    assert len(ws.packets) == 17
    # The padded tail is the reply's last packet: recorded before the
    # reply is declared over, as the client got it.
    names = [c[0] for c in rec.mock_calls]
    assert names[0] == "model_speaking"
    assert names[-1] == "model_speaking"
    assert rec.mock_calls[-1] == call.model_speaking(False)
    assert set(names[1:-1]) == {"speaker_packet"}


@needs_opus
async def test_audio_dropped_by_a_clear_is_never_recorded() -> None:
    """A slow link: one packet is written and stuck in flow control when
    the clear comes; the rest was never sent, so it is not recorded."""
    srv, ws, _ = _server()
    rec = _fake_recorder()
    srv.attach_recorder(rec)
    rec.reset_mock()
    gate = asyncio.Event()
    ws.gate = gate
    sender = asyncio.create_task(srv.send_audio(_sine(600)))
    await _until(lambda: len(ws.sent) == 1)
    await srv.send_audio_clear()
    gate.set()
    await sender
    assert _speaker_packets(rec) == ws.packets == [ws.sent[0]]
    # In wire order: the packet, then the clear.
    assert [c[0] for c in rec.mock_calls] == ["speaker_packet", "audio_clear"]


async def test_a_pcm_client_is_never_tapped() -> None:
    srv, ws, _ = _server(opus=False)
    rec = _fake_recorder()
    srv.attach_recorder(rec)
    await srv.send_audio(_sine(200))
    assert ws.sent  # the PCM client got its JSON audio
    rec.speaker_packet.assert_not_called()


# --- Mic: as it arrived ------------------------------------------------------------


@needs_opus
async def test_mic_packets_are_recorded_before_decoding_even_rejected_ones() -> None:
    srv, _ws, on_audio_frame = _server()
    rec = _fake_recorder()
    srv.attach_recorder(rec)
    rec.reset_mock()
    good = OpusEncoderStream().push(_sine(60))[0]
    await srv._dispatch(json.dumps({"type": "ptt_start"}))
    await srv._dispatch(good)
    await srv._dispatch(b"\x03\x00")  # libopus refuses it
    await srv._dispatch(json.dumps({"type": "ptt_stop"}))
    assert rec.mock_calls == [
        call.ptt(True),
        call.mic_packet("c1", good),
        call.mic_packet("c1", b"\x03\x00"),
        call.ptt(False),
    ]
    assert on_audio_frame.await_count == 1  # only the good one went on


# --- Control frames ------------------------------------------------------------


@needs_opus
async def test_stream_and_claim_frames_reach_the_recorder_in_order() -> None:
    srv, ws, _ = _server()
    rec = _fake_recorder()
    srv.attach_recorder(rec)
    rec.reset_mock()
    await srv.send_stream_started("s1", "Radio", 0)
    await srv.send_audio(_sine(100))  # 1 packet + 40 ms pending
    await srv.send_stream_ended("s1", "natural")
    await srv.send_claim_started("k1", "comms", "Ana")
    await srv.send_claim_ended("k1", "natural")
    assert rec.mock_calls == [
        call.stream_started("s1", "Radio"),
        call.speaker_packet("c1", ws.packets[0]),
        call.speaker_packet("c1", ws.packets[1]),  # the stream's padded tail
        call.stream_ended("s1", "natural"),
        call.claim_started("k1", "comms", "Ana"),
        call.claim_ended("k1", "natural"),
    ]


async def test_a_recorder_attached_mid_activity_starts_from_what_plays() -> None:
    """A reload while a stream plays under a claim, mid-press: the new
    recorder must know all of it, or its first segments are mislabeled."""
    srv, _ws, _ = _server()
    await srv.send_model_speaking(True)
    await srv.send_stream_started("s1", "Book", 0)
    await srv.send_claim_started("k1", "comms", "Ana")
    await srv._dispatch(json.dumps({"type": "ptt_start"}))
    rec = _fake_recorder()
    srv.attach_recorder(rec)
    assert rec.mock_calls == [
        call.model_speaking(True),
        call.stream_started("s1", "Book"),
        call.claim_started("k1", "comms", "Ana"),
        call.connection_opened("c1", "opus"),
        call.ptt(True),
    ]


async def test_only_the_attached_recorder_is_detached() -> None:
    """A persona swap: the new persona attaches its recorder before the
    old one shuts down; the old one's detach must not undo it."""
    srv, _ws, _ = _server()
    old, new = _fake_recorder(), _fake_recorder()
    srv.attach_recorder(old)
    srv.attach_recorder(new)
    old.detached.assert_called_once()
    assert srv.detach_recorder(old) is False
    assert srv._recorder is new
    assert srv.detach_recorder(new) is True
    assert srv._recorder is None


# --- Connections ---------------------------------------------------------------


async def test_a_pcm_client_is_skipped_with_one_log_line() -> None:
    srv, _ = _bare_server()
    rec = _fake_recorder()
    srv.attach_recorder(rec)
    ws = _ClientWS()
    with structlog.testing.capture_logs() as logs:
        task = asyncio.create_task(srv._handle_connection(ws))  # type: ignore[arg-type]
        await _until(lambda: any(m.get("type") == "input_mode" for m in ws.json))
        srv.attach_recorder(_fake_recorder())  # a reload: same connection
        await srv.send_audio(_sine(100))
        ws.finish()
        await asyncio.wait_for(task, 2)
    skipped = [e for e in logs if e["event"] == "recorder.skipped_pcm_client"]
    assert len(skipped) == 1
    assert skipped[0]["audio_format"] == "pcm"
    rec.connection_opened.assert_called_once()
    assert rec.connection_opened.call_args.args[1] == "pcm"
    rec.speaker_packet.assert_not_called()


async def test_a_disconnect_and_an_eviction_close_the_connections_segments() -> None:
    srv, _ = _bare_server()
    rec = _fake_recorder()
    srv.attach_recorder(rec)
    first, second = _ClientWS(), _ClientWS()
    t1 = asyncio.create_task(srv._handle_connection(first))  # type: ignore[arg-type]
    await _until(lambda: srv._conn is not None and srv._conn.ws is first)
    c1 = srv._conn.conn_id if srv._conn else ""
    t2 = asyncio.create_task(srv._handle_connection(second))  # type: ignore[arg-type]
    await _until(lambda: srv._conn is not None and srv._conn.ws is second)
    c2 = srv._conn.conn_id if srv._conn else ""
    second.finish()
    await asyncio.wait_for(asyncio.gather(t1, t2), 2)
    closes = [c.args for c in rec.connection_closed.call_args_list]
    assert closes == [(c1, "evicted"), (c2, "disconnect")]


async def test_audio_for_nobody_is_logged_rate_limited_with_a_count() -> None:
    srv, _ = _bare_server()
    with structlog.testing.capture_logs() as logs:
        await srv.send_audio(bytes(4800))  # 100 ms: the first is said at once
        await srv.send_audio(bytes(4800))
        await srv.send_audio(bytes(9600))  # within the window: counted only
        srv._no_client_drop_logged_s -= 10  # the window passes
        await srv.send_audio(bytes(4800))
        await srv.send_audio(bytes(4800))  # pending when a client connects
        ws = _ClientWS()
        task = asyncio.create_task(srv._handle_connection(ws))  # type: ignore[arg-type]
        await _until(lambda: any(m.get("type") == "input_mode" for m in ws.json))
        ws.finish()
        await asyncio.wait_for(task, 2)
    lines = [e for e in logs if e["event"] == "server.tx.audio_dropped_no_client"]
    assert [(e["count"], e["audio_ms"]) for e in lines] == [(1, 100), (3, 400), (1, 100)]


# --- The real recorder, end to end ----------------------------------------------


@needs_opus
async def test_a_press_and_a_reply_become_two_files(tmp_path: Path) -> None:
    srv, ws, _ = _server()
    mic = OpusEncoderStream().push(_sine(600))
    with structlog.testing.capture_logs() as logs:
        rec = Recorder(tmp_path)
        rec.start()
        srv.attach_recorder(rec)
        await srv._dispatch(json.dumps({"type": "ptt_start"}))
        for p in mic:
            await srv._dispatch(p)
        await srv._dispatch(json.dumps({"type": "ptt_stop"}))
        await srv.send_model_speaking(True)
        await srv.send_audio(_sine(1000))
        await srv.send_model_speaking(False)
        assert srv.detach_recorder(rec)
        assert await rec.aclose()
    segments = [e for e in logs if e["event"] == "recorder.segment"]
    by_source = {e["source"]: e for e in segments}
    assert set(by_source) == {"mic", "model"}
    assert by_source["mic"]["packets"] == len(mic) == 10
    assert by_source["mic"]["end_reason"] == "ptt_stop"
    assert by_source["model"]["packets"] == len(ws.packets) == 17
    assert by_source["model"]["duration_ms"] == 17 * 60
    # Byte for byte what crossed the wire, both ways.
    assert _ogg_packets(tmp_path / by_source["mic"]["path"]) == mic
    assert _ogg_packets(tmp_path / by_source["model"]["path"]) == ws.packets


# --- The persona switch -----------------------------------------------------------

_PERSONA_YAML = """\
version: 1
name: p
voice: coral
language_code: es
transcription_language: es
timezone: America/Bogota
system_prompt: |
  Test persona with no skills.
constraints: []
skills: {}
"""


def _recorder_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "huxley-recorder"]


async def _boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: str) -> Runtime:
    persona_dir = tmp_path / "personas" / "p"
    persona_dir.mkdir(parents=True)
    (persona_dir / "persona.yaml").write_text(_PERSONA_YAML + extra, encoding="utf-8")
    (persona_dir / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    runtime = Runtime(Settings(openai_api_key="test-key"))
    await runtime._switch_to_persona("p", auto_connect=False)
    return runtime


async def test_recording_off_writes_nothing_and_starts_no_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = len(_recorder_threads())
    runtime = await _boot(tmp_path, monkeypatch, "")
    app = runtime.current_app
    assert app is not None
    assert runtime.audio_server._recorder is None
    assert len(_recorder_threads()) == before
    await app.shutdown()
    assert not (tmp_path / "personas" / "p" / "data" / "recordings").exists()


async def test_recording_on_attaches_a_recorder_until_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await _boot(tmp_path, monkeypatch, "recording:\n  enabled: true\n")
    app = runtime.current_app
    assert app is not None
    rec = runtime.audio_server._recorder
    assert isinstance(rec, Recorder)
    assert rec.is_running
    with structlog.testing.capture_logs() as logs:
        await app.shutdown()
    assert runtime.audio_server._recorder is None
    assert not rec.is_running
    assert any(e["event"] == "recorder.stopped" for e in logs)

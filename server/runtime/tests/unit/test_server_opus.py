"""Opus audio (`?audio=opus`): the codec stream and `AudioServer`'s use of it.

The walkie asks for Opus because 48 KB/s of base64 PCM does not survive
its 2.4 GHz link. These tests drive a fake socket so ordering and timing
are deterministic; `test_firmware_contract.py` covers the same contract
over a real WebSocket. Real libopus throughout (opuslib_next); skipped,
with the reason, only where the system libopus cannot be loaded.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import operator
import struct
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import structlog

from huxley.server import opus_codec
from huxley.server import server as server_mod
from huxley.server.opus_codec import (
    FRAME_BYTES,
    FRAME_SAMPLES,
    OpusDecoderStream,
    OpusEncoderStream,
    opus_unavailable_reason,
)
from huxley.server.server import AudioServer, _ConnInfo

_NO_OPUS = opus_unavailable_reason()
pytestmark = pytest.mark.skipif(
    _NO_OPUS is not None,
    reason=f"libopus not loadable ({_NO_OPUS}); brew install opus / apt install libopus0",
)

_SR = 24000


def _sine(ms: int, freq: float = 440.0, start: int = 0) -> bytes:
    n = _SR * ms // 1000
    return struct.pack(
        f"<{n}h",
        *(int(8000 * math.sin(2 * math.pi * freq * (i + start) / _SR)) for i in range(n)),
    )


def _samples(pcm: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(pcm) // 2}h", pcm))


def _similarity(reference: bytes, decoded: bytes) -> float:
    """Best normalized correlation of a 60 ms window of `reference`
    against `decoded`, over the codec's delay (a few ms). 1.0 is
    identical; a lossy codec on a sine lands well above 0.9."""
    a = _samples(reference)[2880 : 2880 + 1440]
    b = _samples(decoded)
    best = 0.0
    for lag in range(400):
        seg = b[2880 + lag : 2880 + lag + 1440]
        if len(seg) < len(a):
            break
        num = sum(map(operator.mul, a, seg))
        den = math.sqrt(sum(x * x for x in a) * sum(y * y for y in seg)) or 1.0
        best = max(best, num / den)
    return best


def _ref_encoder() -> Any:
    lib = opus_codec._load()
    enc = lib.Encoder(_SR, 1, lib.APPLICATION_AUDIO)
    enc.bitrate = opus_codec.BITRATE
    enc.vbr = 1
    return enc


def _ref_decoder() -> Any:
    return opus_codec._load().Decoder(_SR, 1)


class _Transport:
    def get_write_buffer_size(self) -> int:
        return 0

    def get_extra_info(self, name: str) -> Any:
        return None


class _WS:
    """Fake connection. `send` records the frame first, then (once, if
    a gate is set) waits: like websockets, which writes before it waits
    on flow control."""

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

    @property
    def json(self) -> list[dict[str, Any]]:
        return [json.loads(m) for m in self.sent if isinstance(m, str)]


def _server(opus: bool = True, hello_sent: bool = True) -> tuple[AudioServer, _WS, AsyncMock]:
    """A server with one connection, as `_handle_connection` leaves it
    after the hello (`hello_sent=False`: before it)."""
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
    conn.hello_sent = hello_sent
    srv._conn = conn
    return srv, ws, on_audio_frame


async def _until(cond: Any, timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


# --- The codec stream ------------------------------------------------------


@pytest.mark.parametrize("ms", [0, 10, 59, 60, 61, 100, 119, 120, 1000, 2500])
def test_n_ms_become_ceil_n_over_60_packets_and_nothing_is_stranded(ms: int) -> None:
    enc = OpusEncoderStream()
    pcm = _sine(ms)
    packets: list[bytes] = []
    # Irregular chunk sizes (whole samples), like model deltas.
    sizes = [62, 2400, 4802, 1000, 9998]
    offset = i = 0
    while offset < len(pcm):
        step = sizes[i % len(sizes)]
        packets += enc.push(pcm[offset : offset + step])
        offset += step
        i += 1
    tail = enc.flush()
    if tail is not None:
        packets.append(tail)
    assert len(packets) == math.ceil(ms / 60)
    assert enc.pending_bytes == 0
    assert enc.flush() is None
    dec = _ref_decoder()
    for p in packets:
        assert len(dec.decode(p, 2880)) == FRAME_BYTES  # 60 ms each, the contract


def test_round_trip_through_the_codec_keeps_the_signal() -> None:
    pcm = _sine(600)
    enc = OpusEncoderStream()
    packets = enc.push(pcm)
    dec = OpusDecoderStream()
    decoded = b"".join(dec.decode(p) for p in packets)
    assert len(decoded) == len(pcm)
    assert _similarity(pcm, decoded) > 0.9


def test_decoder_refuses_an_empty_packet() -> None:
    # libopus would read it as a lost packet and return 120 ms of
    # concealment audio.
    with pytest.raises(ValueError):
        OpusDecoderStream().decode(b"")


# --- Outbound: send_audio -> binary packets ---------------------------------


async def test_send_audio_goes_out_as_binary_packets_that_decode_to_the_pcm() -> None:
    srv, ws, _ = _server()
    pcm = _sine(1000)
    await srv.send_audio(pcm)
    assert len(ws.packets) == 16  # whole frames only; 40 ms wait for more
    await srv.send_model_speaking(False)
    assert len(ws.packets) == 17  # ceil(1000 / 60): the tail went out padded
    # No JSON audio, and the tail precedes the end-of-reply message.
    assert all(m["type"] != "audio" for m in ws.json)
    assert isinstance(ws.sent[-2], bytes)
    assert json.loads(ws.sent[-1]) == {"type": "model_speaking", "value": False}
    dec = _ref_decoder()
    decoded = b"".join(dec.decode(p, 2880) for p in ws.packets)
    assert len(decoded) == 17 * FRAME_BYTES
    assert _similarity(pcm, decoded) > 0.9


async def test_no_binary_before_hello() -> None:
    """A reconnect while music plays: the stream keeps sending to the
    new connection before its hello is out. An opus client must not get
    binary before the hello that tells it the format."""
    srv, ws, _ = _server(hello_sent=False)
    await srv.send_audio(_sine(300))
    await srv.send_model_speaking(False)
    assert ws.packets == []
    assert srv._conn is not None and srv._conn.opus_tx is not None
    assert srv._conn.opus_tx.pending_bytes == 0


async def test_model_speaking_true_does_not_flush() -> None:
    srv, ws, _ = _server()
    await srv.send_audio(_sine(100))
    await srv.send_model_speaking(True)
    assert len(ws.packets) == 1


async def test_stream_ended_flushes_before_the_message() -> None:
    srv, ws, _ = _server()
    await srv.send_audio(_sine(100))
    await srv.send_stream_ended("s1", "natural")
    assert [type(m) for m in ws.sent] == [bytes, bytes, str]
    assert json.loads(ws.sent[-1])["type"] == "stream_ended"


async def test_idle_flush_sends_the_tail_when_no_pcm_follows() -> None:
    """An earcon that nothing follows (no reply end, no stream end)."""
    srv, ws, _ = _server()
    srv._OPUS_IDLE_FLUSH_S = 0.02
    with structlog.testing.capture_logs() as logs:
        await srv.send_audio(_sine(100))
        assert len(ws.packets) == 1
        await _until(lambda: len(ws.packets) == 2)
        await _until(lambda: any(e["event"] == "server.tx.opus_flush" for e in logs))
    flush = next(e for e in logs if e["event"] == "server.tx.opus_flush")
    assert flush["reason"] == "idle"
    assert flush["tail_ms"] == 40
    assert flush["pad_ms"] == 20


async def test_idle_timer_restarts_on_new_pcm_and_stops_when_nothing_pends() -> None:
    srv, _ws, _ = _server()
    conn = srv._conn
    assert conn is not None
    await srv.send_audio(_sine(50))
    first = conn.opus_flush_timer
    assert first is not None
    await srv.send_audio(_sine(50))  # 100 ms: one frame out, 40 ms pending
    second = conn.opus_flush_timer
    assert first.cancelled()
    assert second is not None and not second.cancelled()
    await srv.send_audio(_sine(20))  # 120 ms: nothing pending
    assert second.cancelled()
    assert conn.opus_flush_timer is None


async def test_audio_clear_drops_pending_pcm_and_resets_the_encoder() -> None:
    srv, ws, _ = _server()
    conn = srv._conn
    assert conn is not None and conn.opus_tx is not None
    await srv.send_audio(_sine(200))  # 3 packets, 20 ms pending
    assert conn.opus_tx.pending_bytes == 960
    await srv.send_audio_clear()
    assert conn.opus_tx.pending_bytes == 0
    assert conn.opus_flush_timer is None
    assert json.loads(ws.sent[-1]) == {"type": "audio_clear"}
    await srv.send_model_speaking(False)
    assert len(ws.packets) == 3  # the dropped 20 ms never went out
    # The next sound starts from a fresh encoder state: its first packet
    # is byte-identical to a brand-new encoder's (a carried-over state
    # would make it differ).
    nxt = _sine(60, start=12345)
    await srv.send_audio(nxt)
    assert ws.packets[-1] == _ref_encoder().encode(nxt, FRAME_SAMPLES)


async def test_audio_clear_drops_packets_not_yet_written() -> None:
    """A slow link: the first packet of a long sound is stuck in flow
    control when the barge-in comes. The rest must not follow the clear."""
    srv, ws, _ = _server()
    gate = asyncio.Event()
    ws.gate = gate
    sender = asyncio.create_task(srv.send_audio(_sine(600)))
    await _until(lambda: len(ws.sent) == 1)  # packet 0 written, then stuck
    await srv.send_audio_clear()
    gate.set()
    await sender
    assert [type(m) for m in ws.sent] == [bytes, str]
    assert json.loads(ws.sent[1]) == {"type": "audio_clear"}


async def test_concurrent_senders_keep_encoder_order() -> None:
    """Two tasks sending at once (the decoder needs packets in order)."""
    srv, ws, _ = _server()
    gate = asyncio.Event()
    ws.gate = gate
    a, b = _sine(180), _sine(120, freq=660)
    task_a = asyncio.create_task(srv.send_audio(a))
    await _until(lambda: len(ws.sent) == 1)  # A's first packet, stuck
    await srv.send_audio(b)  # B drains the shared queue: A1, A2, B0, B1
    gate.set()
    await task_a
    ref = _ref_encoder()
    pcm = a + b
    expected = [
        ref.encode(pcm[i * FRAME_BYTES : (i + 1) * FRAME_BYTES], FRAME_SAMPLES) for i in range(5)
    ]
    assert ws.packets == expected


async def test_stats_count_pcm_ms_and_the_bytes_actually_sent() -> None:
    srv, ws, _ = _server()
    now = [0]
    with (
        patch.object(server_mod, "_now_ms", lambda: now[0]),
        structlog.testing.capture_logs() as logs,
    ):
        for t in (1000, 1400, 2100):
            now[0] = t
            await srv.send_audio(_sine(100))
    stats = [e for e in logs if e["event"] == "server.tx.audio_stats"]
    assert len(stats) == 1
    s = stats[0]
    assert s["audio_ms"] == 300  # PCM handed in, not what is on the wire
    assert s["audio_format"] == "opus"
    assert s["sends"] == len(ws.packets) == 5
    assert s["bytes_sent"] == sum(len(p) for p in ws.packets)
    assert s["bytes_sent"] < 300 * 48 // 8  # an order of magnitude under PCM


# --- Inbound: binary packets -> PCM to the coordinator ----------------------


async def test_binary_packets_reach_the_coordinator_as_pcm() -> None:
    srv, _ws, on_audio_frame = _server()
    pcm = _sine(600)
    ref = _ref_encoder()
    packets = [
        ref.encode(pcm[i * FRAME_BYTES : (i + 1) * FRAME_BYTES], FRAME_SAMPLES) for i in range(10)
    ]
    for p in packets:
        await srv._dispatch(p)
    got = [c.args[0] for c in on_audio_frame.await_args_list]
    dec = _ref_decoder()
    assert got == [dec.decode(p, 2880) for p in packets]  # same PCM a decoder gives
    assert all(len(g) == FRAME_BYTES for g in got)
    assert _similarity(pcm, b"".join(got)) > 0.9


async def test_ptt_start_resets_the_decoder() -> None:
    srv, _ws, on_audio_frame = _server()
    pcm = _sine(360)
    ref = _ref_encoder()
    packets = [
        ref.encode(pcm[i * FRAME_BYTES : (i + 1) * FRAME_BYTES], FRAME_SAMPLES) for i in range(6)
    ]
    for p in packets[:5]:
        await srv._dispatch(p)
    await srv._dispatch(json.dumps({"type": "ptt_start"}))
    await srv._dispatch(packets[5])
    fresh = _ref_decoder().decode(packets[5], 2880)
    carried = _ref_decoder()
    for p in packets[:5]:
        carried.decode(p, 2880)
    assert carried.decode(packets[5], 2880) != fresh  # the test can tell
    assert on_audio_frame.await_args_list[-1].args[0] == fresh


async def test_a_packet_that_fails_to_decode_is_logged_and_dropped() -> None:
    srv, _ws, on_audio_frame = _server()
    with structlog.testing.capture_logs() as logs:
        await srv._dispatch(b"\x03\x00")  # code-3 packet with zero frames
        await srv._dispatch(b"")
    assert on_audio_frame.await_count == 0
    failures = [e for e in logs if e["event"] == "server.rx.opus_decode_failed"]
    assert [f["packet_bytes"] for f in failures] == [2, 0]
    assert failures[0]["log_level"] == "warning"
    assert failures[-1]["errors"] == 2
    # Still alive: the next good packet goes through.
    await srv._dispatch(_ref_encoder().encode(_sine(60), FRAME_SAMPLES))
    assert on_audio_frame.await_count == 1


async def test_opus_client_json_audio_is_still_accepted() -> None:
    srv, _ws, on_audio_frame = _server()
    pcm = _sine(20)
    await srv._dispatch(json.dumps({"type": "audio", "data": base64.b64encode(pcm).decode()}))
    on_audio_frame.assert_awaited_once_with(pcm)


# --- PCM clients: unchanged -------------------------------------------------


async def test_pcm_client_gets_json_audio_and_its_binary_is_not_decoded() -> None:
    srv, ws, _on_audio_frame = _server(opus=False)
    pcm = _sine(100)
    await srv.send_audio(pcm)
    await srv.send_model_speaking(False)
    await srv.send_audio_clear()
    assert all(isinstance(m, str) for m in ws.sent)
    assert ws.json[0] == {"type": "audio", "data": base64.b64encode(pcm).decode()}
    # A binary frame from a PCM client takes the JSON path, as before.
    await srv._dispatch(json.dumps({"type": "wake_word"}).encode())
    srv._on_wake_word.assert_awaited_once()  # type: ignore[attr-defined]

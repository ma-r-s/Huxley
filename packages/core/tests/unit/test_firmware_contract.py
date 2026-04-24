"""Firmware ↔ server wire-contract tests.

These tests stand the `AudioServer` up in-process, connect a WebSocket
client that mimics the exact messages the ESP32 firmware sends (see
``firmware/components/hux_app/hux_app.c``), and assert the server's
dispatch callbacks fire with the correct payloads.

Catches: any server-side change that drifts from the firmware's wire
shape — e.g. a message-type rename, a required-field addition, or a
payload-shape tweak that silently stops being accepted.

Does NOT catch: anything on the firmware side itself. The host unit
tests under ``firmware/tests/`` cover protocol *parsing* on the client;
this file covers protocol *dispatch* on the server.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import socket
from typing import Any

import pytest
import websockets
from websockets.asyncio.client import connect

from huxley.server.server import AudioServer


def _reserve_free_port() -> int:
    """Pick a port by binding and immediately closing. Small race
    window vs AudioServer's own bind, but acceptable for tests —
    `AudioServer` doesn't expose its bound port otherwise."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _wait_for_port(host: str, port: int, timeout_s: float = 2.0) -> None:
    """Poll until the server accepts TCP connections on the port."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return
        except (ConnectionRefusedError, OSError):
            await asyncio.sleep(0.02)
    raise RuntimeError(f"server did not come up on {host}:{port} in {timeout_s}s")


class _Recorder:
    """Captures every callback fired by the server for assertion."""

    def __init__(self) -> None:
        self.wake_words = 0
        self.ptt_starts = 0
        self.ptt_stops = 0
        self.resets = 0
        self.audio_frames: list[bytes] = []
        self.language_selects: list[str | None] = []

    async def on_wake_word(self) -> None:
        self.wake_words += 1

    async def on_ptt_start(self) -> None:
        self.ptt_starts += 1

    async def on_ptt_stop(self) -> None:
        self.ptt_stops += 1

    async def on_reset(self) -> None:
        self.resets += 1

    async def on_audio_frame(self, data: bytes) -> None:
        self.audio_frames.append(data)

    async def on_language_select(self, language: str | None) -> None:
        self.language_selects.append(language)


@contextlib.asynccontextmanager
async def _server_on_ephemeral_port(
    recorder: _Recorder,
) -> Any:
    """Stand up an AudioServer on a free 127.0.0.1 port, yield (url, server)."""
    port = _reserve_free_port()
    server = AudioServer(
        host="127.0.0.1",
        port=port,
        on_wake_word=recorder.on_wake_word,
        on_ptt_start=recorder.on_ptt_start,
        on_ptt_stop=recorder.on_ptt_stop,
        on_audio_frame=recorder.on_audio_frame,
        on_reset=recorder.on_reset,
        on_language_select=recorder.on_language_select,
    )
    task = asyncio.create_task(server.run())
    try:
        await _wait_for_port("127.0.0.1", port)
        yield f"ws://127.0.0.1:{port}/", server
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _recv_hello(ws: Any) -> dict[str, Any]:
    """First message the server sends on every connection."""
    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
    msg = json.loads(raw)
    assert msg["type"] == "hello", f"expected hello, got {msg}"
    return msg


@pytest.mark.asyncio
async def test_server_sends_hello_on_connect() -> None:
    """The firmware checks `hello.protocol == 2` before anything else."""
    rec = _Recorder()
    async with _server_on_ephemeral_port(rec) as (url, _srv):
        async with connect(url) as ws:
            hello = await _recv_hello(ws)
            assert "protocol" in hello
            assert isinstance(hello["protocol"], int)
            assert hello["protocol"] == 2, (
                "protocol version bump would break every existing client without a migration"
            )


@pytest.mark.asyncio
async def test_firmware_session_sequence_accepted() -> None:
    """Replay the exact sequence the ESP32 firmware sends for a PTT turn
    and assert every callback fires with the right payload.

    Sequence (see `hux_app.c`:
    `hux_app_start` → on hello: `wake_word`. On K2 press: `ptt_start`,
    then a stream of `audio` frames, then `ptt_stop` on release.
    """
    rec = _Recorder()
    async with _server_on_ephemeral_port(rec) as (url, _srv):
        async with connect(url) as ws:
            await _recv_hello(ws)

            await ws.send(json.dumps({"type": "wake_word"}))
            await ws.send(json.dumps({"type": "ptt_start"}))

            # 1920 B = 20 ms of PCM16 @ 24 kHz mono — matches what the
            # firmware emits per frame (see `hux_audio.c` FRAME_SAMPLES).
            pcm = bytes(i % 256 for i in range(1920))
            await ws.send(
                json.dumps(
                    {
                        "type": "audio",
                        "data": base64.b64encode(pcm).decode("ascii"),
                    }
                )
            )

            await ws.send(json.dumps({"type": "ptt_stop"}))

            # Give the server's event loop a moment to drain dispatches.
            for _ in range(25):
                await asyncio.sleep(0.02)
                if rec.ptt_stops >= 1:
                    break

    assert rec.wake_words == 1, "wake_word not dispatched"
    assert rec.ptt_starts == 1, "ptt_start not dispatched"
    assert rec.ptt_stops == 1, "ptt_stop not dispatched"
    # audio frames are only accepted between ptt_start and ptt_stop,
    # per docs/protocol.md §"Server-side PTT gating rules". One frame
    # in the window should reach on_audio_frame with the exact bytes.
    assert len(rec.audio_frames) == 1, (
        f"expected 1 audio frame accepted, got {len(rec.audio_frames)}"
    )
    assert rec.audio_frames[0] == pcm, "audio bytes round-tripped correctly"


@pytest.mark.asyncio
async def test_malformed_message_does_not_close_connection() -> None:
    """The firmware must survive if it sends something the server doesn't
    understand — we rely on the server logging + continuing, not closing.
    """
    rec = _Recorder()
    async with _server_on_ephemeral_port(rec) as (url, _srv):
        async with connect(url) as ws:
            await _recv_hello(ws)

            # Totally unknown type, plus malformed bytes.
            await ws.send(json.dumps({"type": "this_is_not_a_thing"}))
            await ws.send("this is not json at all")

            # Follow-up legitimate message must still be dispatched.
            await ws.send(json.dumps({"type": "wake_word"}))

            for _ in range(25):
                await asyncio.sleep(0.02)
                if rec.wake_words >= 1:
                    break

    assert rec.wake_words == 1, (
        "server must tolerate unknown / malformed messages and keep the connection alive"
    )


@pytest.mark.asyncio
async def test_second_client_evicts_first() -> None:
    """Second connection evicts the first (the server logs
    `client_evicted`) and the second client then talks normally.

    NOTE: docs/protocol.md still says "A second connection is rejected
    with close code 1008". That text is stale — the implementation
    evicts the OLDER client instead. This test locks down the actual
    behavior; see firmware/docs/triage.md for the doc-drift follow-up.
    """
    rec = _Recorder()
    async with _server_on_ephemeral_port(rec) as (url, _srv):
        async with connect(url) as first:
            await _recv_hello(first)

            async with connect(url) as second:
                await _recv_hello(second)

                # First client should now be closed by the server.
                with pytest.raises(
                    (
                        websockets.exceptions.ConnectionClosedError,
                        websockets.exceptions.ConnectionClosedOK,
                    )
                ):
                    # Drain anything still in the first client's buffer
                    # until the server-side close propagates.
                    for _ in range(50):
                        await asyncio.wait_for(first.recv(), timeout=0.2)

                # Second client is the live one now — its messages are
                # dispatched to the recorder.
                await second.send(json.dumps({"type": "wake_word"}))
                for _ in range(25):
                    await asyncio.sleep(0.02)
                    if rec.wake_words >= 1:
                        break

    assert rec.wake_words == 1, "second client's messages must be dispatched"

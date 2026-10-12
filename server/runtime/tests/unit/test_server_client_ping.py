"""The server's proof that a client got what was sent (T1.44).

The coordinator confirms an injected turn as delivered only once the
client it was said to has played it and answered a WebSocket ping sent
after it: TCP is ordered, so the pong means everything before the ping
arrived. That client is pinned by its epoch (`listener_epoch`): a pong
from a newer connection proves nothing about audio sent to the old one.
A Wi-Fi drop the server hasn't noticed yet never answers: the ping is
bounded, and on timeout the connection is aborted so the client reports
gone and reconnects.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import Any
from unittest.mock import AsyncMock

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from huxley.server.server import AudioServer, _ConnInfo


def _server(port: int = 0) -> AudioServer:
    return AudioServer(
        host="127.0.0.1",
        port=port,
        on_wake_word=AsyncMock(),
        on_ptt_start=AsyncMock(),
        on_ptt_stop=AsyncMock(),
        on_audio_frame=AsyncMock(),
        on_reset=AsyncMock(),
    )


class _Transport:
    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class _FakeWS:
    """A ServerConnection whose pong we control."""

    def __init__(self) -> None:
        self.remote_address = ("walkie", 0)
        self.pong: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        self.raise_on_ping: BaseException | None = None
        self.block_ping = False
        self.transport = _Transport()

    async def ping(self) -> asyncio.Future[float]:
        if self.raise_on_ping is not None:
            raise self.raise_on_ping
        if self.block_ping:  # the write waits on a full send buffer
            await asyncio.Event().wait()
        return self.pong


def _attach(server: AudioServer, ws: Any, *, epoch: int = 7, hello_sent: bool = True) -> None:
    server._conn = _ConnInfo(
        conn_id="abcd1234",
        remote="walkie",
        connected_at_ms=0,
        requested_persona=None,
        requested_language=None,
        ws=ws,
        epoch=epoch,
        hello_sent=hello_sent,
    )


async def test_no_listener_before_the_hello() -> None:
    server = _server()
    assert server.listener_epoch is None
    _attach(server, _FakeWS(), hello_sent=False)
    assert server.listener_epoch is None, "it can't play anything yet"
    _attach(server, _FakeWS())
    assert server.listener_epoch == 7


async def test_no_client_means_not_received() -> None:
    assert await _server().client_received_all(1, timeout_s=1.0) is False


async def test_a_pong_means_received() -> None:
    server = _server()
    ws = _FakeWS()
    _attach(server, ws)
    ws.pong.set_result(0.012)

    assert await server.client_received_all(7, timeout_s=1.0) is True


async def test_a_newer_connection_proves_nothing_about_an_older_one() -> None:
    """The walkie reconnected after a drop: the turn went to epoch 7, the
    current client is epoch 8. Its pong says nothing about epoch 7."""
    server = _server()
    ws = _FakeWS()
    _attach(server, ws, epoch=8)
    ws.pong.set_result(0.01)

    assert await server.client_received_all(7, timeout_s=1.0) is False


async def test_a_socket_closing_before_the_pong_means_not_received() -> None:
    server = _server()
    ws = _FakeWS()
    _attach(server, ws)
    task = asyncio.create_task(server.client_received_all(7, timeout_s=1.0))
    await asyncio.sleep(0.01)
    assert not task.done(), "waits for the pong"

    ws.pong.set_exception(ConnectionClosedError(None, None))

    assert await asyncio.wait_for(task, timeout=1.0) is False


async def test_a_closed_socket_means_not_received() -> None:
    server = _server()
    ws = _FakeWS()
    ws.raise_on_ping = ConnectionClosedError(None, None)
    _attach(server, ws)

    assert await server.client_received_all(7, timeout_s=1.0) is False


async def test_no_pong_in_time_aborts_the_connection() -> None:
    """Never counted as delivered: the socket is cut so the walkie
    reconnects and hears the turn again."""
    server = _server()
    ws = _FakeWS()
    _attach(server, ws)

    assert await server.client_received_all(7, timeout_s=0.05) is False
    assert ws.transport.aborted


async def test_a_ping_stuck_on_a_full_buffer_is_bounded_too() -> None:
    server = _server()
    ws = _FakeWS()
    ws.block_ping = True
    _attach(server, ws)

    assert await asyncio.wait_for(server.client_received_all(7, timeout_s=0.05), 1.0) is False
    assert ws.transport.aborted


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


async def test_a_real_client_answers() -> None:
    """End to end over a real socket: the websockets client answers the
    ping by itself, as browsers and the walkie's esp_websocket_client do."""
    port = _free_port()
    server = _server(port)
    run = asyncio.create_task(server.run())
    try:
        for _ in range(200):
            with contextlib.suppress(OSError):
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                break
            await asyncio.sleep(0.01)
        async with connect(f"ws://127.0.0.1:{port}/") as ws:
            await asyncio.wait_for(ws.recv(), timeout=1.0)  # hello

            async def _drain() -> None:
                with contextlib.suppress(Exception):
                    async for _msg in ws:
                        pass

            reader = asyncio.create_task(_drain())
            for _ in range(200):
                if server.listener_epoch is not None:
                    break
                await asyncio.sleep(0.01)
            epoch = server.listener_epoch
            assert epoch is not None
            assert await server.client_received_all(epoch, timeout_s=2.0) is True
            reader.cancel()
    finally:
        run.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run

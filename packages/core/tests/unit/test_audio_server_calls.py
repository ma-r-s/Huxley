"""Tests for AudioServer's call routes (T1.4 Stage 2 commit 4).

Verify the HTTP `/call/ring` and WS `/call` endpoints in isolation —
auth, busy semantics, missing-config 503s, and that the registered
callbacks fire with the right payloads.

Each test stands up a real `serve()` on `127.0.0.1` with an OS-assigned
port, exercises one route, and tears down. Slower than pure unit tests
(~50ms each for socket setup) but they cover the actual `process_request`
hook + path routing rather than mocking the websockets internals.
"""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
import websockets

from huxley.server.server import AudioServer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _free_port() -> int:
    """Grab an OS-assigned free port for the test server."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _server_kwargs() -> dict[str, Any]:
    """Default no-op grandpa-side callbacks. Tests override `on_ring`,
    `on_caller_connected`, `ring_secret` as needed."""
    return {
        "on_wake_word": AsyncMock(),
        "on_ptt_start": AsyncMock(),
        "on_ptt_stop": AsyncMock(),
        "on_audio_frame": AsyncMock(),
        "on_reset": AsyncMock(),
    }


async def _http_get(host: str, port: int, path: str, headers: dict[str, str]) -> tuple[int, bytes]:
    """Raw HTTP/1.1 GET with arbitrary headers. Returns (status, body)."""
    reader, writer = await asyncio.open_connection(host, port)
    header_lines = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
    request = f"GET {path} HTTP/1.1\r\nHost: {host}\r\n{header_lines}\r\nConnection: close\r\n\r\n"
    writer.write(request.encode())
    await writer.drain()
    raw = await reader.read()
    writer.close()
    with __import__("contextlib").suppress(Exception):
        await writer.wait_closed()
    head, _, body = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0]
    # b"HTTP/1.1 200 OK"
    parts = status_line.split(b" ", 2)
    status = int(parts[1])
    return status, body


@pytest.fixture
async def server_with_calls() -> AsyncIterator[tuple[AudioServer, AsyncMock, AsyncMock, int]]:
    """Spin up an AudioServer with both call hooks wired. Yields the server,
    the on_ring mock, the on_caller_connected mock, and the listening port.
    """
    on_ring = AsyncMock(return_value=True)
    on_caller_connected = AsyncMock()
    port = _free_port()
    server = AudioServer(
        host="127.0.0.1",
        port=port,
        on_ring=on_ring,
        on_caller_connected=on_caller_connected,
        ring_secret="hunter2",
        **_server_kwargs(),
    )
    task = asyncio.create_task(server.run())
    # Tiny delay to let `serve()` bind the port.
    await asyncio.sleep(0.05)
    try:
        yield server, on_ring, on_caller_connected, port
    finally:
        task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await task


@pytest.fixture
async def server_no_calls() -> AsyncIterator[tuple[AudioServer, int]]:
    """Server without any call hooks — both routes should 503."""
    port = _free_port()
    server = AudioServer(
        host="127.0.0.1",
        port=port,
        **_server_kwargs(),
    )
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        yield server, port
    finally:
        task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await task


# --- /call/ring ---


class TestRingRoute:
    async def test_valid_secret_fires_on_ring_returns_200(
        self,
        server_with_calls: tuple[AudioServer, AsyncMock, AsyncMock, int],
    ) -> None:
        _, on_ring, _, port = server_with_calls
        status, body = await _http_get(
            "127.0.0.1", port, "/call/ring?from=mario", {"X-Shared-Secret": "hunter2"}
        )
        assert status == 200
        assert body == b"ringing\n"
        on_ring.assert_awaited_once()
        # Query params parsed and passed to the skill callback.
        assert on_ring.await_args.args[0] == {"from": "mario"}

    async def test_missing_secret_returns_401(
        self,
        server_with_calls: tuple[AudioServer, AsyncMock, AsyncMock, int],
    ) -> None:
        _, on_ring, _, port = server_with_calls
        status, body = await _http_get(
            "127.0.0.1", port, "/call/ring", {"X-Shared-Secret": "wrong"}
        )
        assert status == 401
        assert body == b"bad secret\n"
        on_ring.assert_not_awaited()

    async def test_skill_busy_returns_409(
        self,
        server_with_calls: tuple[AudioServer, AsyncMock, AsyncMock, int],
    ) -> None:
        _, on_ring, _, port = server_with_calls
        on_ring.return_value = False  # skill rejects (already on call)
        status, body = await _http_get(
            "127.0.0.1", port, "/call/ring", {"X-Shared-Secret": "hunter2"}
        )
        assert status == 409
        assert body == b"busy\n"

    async def test_no_callback_returns_503(self, server_no_calls: tuple[AudioServer, int]) -> None:
        _, port = server_no_calls
        status, body = await _http_get(
            "127.0.0.1", port, "/call/ring", {"X-Shared-Secret": "anything"}
        )
        assert status == 503
        assert body == b"calls disabled\n"

    async def test_skill_handler_exception_returns_500(
        self,
        server_with_calls: tuple[AudioServer, AsyncMock, AsyncMock, int],
    ) -> None:
        _, on_ring, _, port = server_with_calls
        on_ring.side_effect = RuntimeError("skill blew up")
        status, body = await _http_get(
            "127.0.0.1", port, "/call/ring", {"X-Shared-Secret": "hunter2"}
        )
        assert status == 500
        assert body == b"internal error\n"


# --- /call WebSocket ---


class TestCallerWebSocket:
    async def test_valid_secret_invokes_on_caller_connected(
        self,
        server_with_calls: tuple[AudioServer, AsyncMock, AsyncMock, int],
    ) -> None:
        _, _, on_caller, port = server_with_calls

        async def caller_handler(ws: Any) -> None:
            # Simulate the skill receiving one frame then closing.
            await ws.send(b"\x00\x01\x02")
            await ws.close()

        on_caller.side_effect = caller_handler

        async with websockets.connect(f"ws://127.0.0.1:{port}/call?secret=hunter2") as ws:
            data = await ws.recv()
            assert data == b"\x00\x01\x02"

        on_caller.assert_awaited_once()

    async def test_bad_secret_closes_connection(
        self,
        server_with_calls: tuple[AudioServer, AsyncMock, AsyncMock, int],
    ) -> None:
        _, _, on_caller, port = server_with_calls
        with pytest.raises(websockets.ConnectionClosedError):
            async with websockets.connect(f"ws://127.0.0.1:{port}/call?secret=wrong") as ws:
                await ws.recv()
        on_caller.assert_not_awaited()

    async def test_missing_callback_closes_connection(
        self, server_no_calls: tuple[AudioServer, int]
    ) -> None:
        _, port = server_no_calls
        with pytest.raises(websockets.ConnectionClosedError):
            async with websockets.connect(f"ws://127.0.0.1:{port}/call?secret=anything") as ws:
                await ws.recv()


# --- Default grandpa path still works ---


class TestDefaultClientPathStillWorks:
    async def test_default_ws_path_routes_to_grandpa_handler(
        self,
        server_with_calls: tuple[AudioServer, AsyncMock, AsyncMock, int],
    ) -> None:
        """Adding call routes must not break the existing main client path."""
        _, _, _, port = server_with_calls
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            # First message should be the hello handshake.
            hello_raw = await ws.recv()
            import json

            hello = json.loads(hello_raw)
            assert hello["type"] == "hello"

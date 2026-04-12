"""Tests for the mpv IPC client."""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from abuel_os.media.mpv import MpvClient, MpvError


class FakeSocket:
    """Simulates an mpv IPC Unix socket for testing.

    Auto-responds to commands: when write() is called (command sent),
    it queues a pre-configured response so the receive loop can process it.
    This avoids the race condition of pre-queuing responses before futures
    are registered.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self._response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._auto_responses: list[dict[str, object]] = []

    def set_next_response(self, *, data: object = None, error: str = "success") -> None:
        """Configure what to respond with on the next command."""
        self._auto_responses.append({"error": error, "data": data})

    async def readline(self) -> bytes:
        line = await self._response_queue.get()
        return line.encode()

    def write(self, raw: bytes) -> None:
        msg = json.loads(raw.decode().strip())
        self.sent.append(msg)
        # Auto-queue response matching the request_id
        if self._auto_responses:
            resp = self._auto_responses.pop(0)
            resp["request_id"] = msg["request_id"]
            self._response_queue.put_nowait(json.dumps(resp) + "\n")

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


def _wire_fake_socket(client: MpvClient, socket: FakeSocket) -> asyncio.Task[None]:
    """Wire a FakeSocket into an MpvClient and start the receive loop."""
    client._reader = socket  # type: ignore[assignment]
    client._writer = socket  # type: ignore[assignment]
    task = asyncio.create_task(client._receive_loop())
    client._receive_task = task
    return task


@pytest.fixture
def mpv_client(tmp_path: str) -> MpvClient:
    return MpvClient(socket_path=f"{tmp_path}/test_mpv.sock")


class TestMpvClientCommands:
    async def test_command_sends_json_with_request_id(self, mpv_client: MpvClient) -> None:
        socket = FakeSocket()
        socket.set_next_response()
        task = _wire_fake_socket(mpv_client, socket)

        await mpv_client.loadfile("/path/to/book.mp3")

        assert len(socket.sent) == 1
        msg = socket.sent[0]
        assert msg["command"] == ["loadfile", "/path/to/book.mp3"]
        assert "request_id" in msg
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_pause_sets_property(self, mpv_client: MpvClient) -> None:
        socket = FakeSocket()
        socket.set_next_response()
        task = _wire_fake_socket(mpv_client, socket)

        await mpv_client.pause()

        msg = socket.sent[0]
        assert msg["command"] == ["set_property", "pause", True]
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_seek_relative(self, mpv_client: MpvClient) -> None:
        socket = FakeSocket()
        socket.set_next_response()
        task = _wire_fake_socket(mpv_client, socket)

        await mpv_client.seek(-30.0)

        msg = socket.sent[0]
        assert msg["command"] == ["seek", -30.0, "relative"]
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_get_position_returns_float(self, mpv_client: MpvClient) -> None:
        socket = FakeSocket()
        socket.set_next_response(data=42.5)
        task = _wire_fake_socket(mpv_client, socket)

        pos = await mpv_client.get_position()
        assert pos == 42.5
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_error_response_returns_default(self, mpv_client: MpvClient) -> None:
        socket = FakeSocket()
        socket.set_next_response(error="property not found")
        task = _wire_fake_socket(mpv_client, socket)

        pos = await mpv_client.get_position()
        assert pos == 0.0
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_not_connected_raises(self, mpv_client: MpvClient) -> None:
        with pytest.raises(MpvError, match="Not connected"):
            await mpv_client.loadfile("/test.mp3")

    def test_is_running_false_initially(self, mpv_client: MpvClient) -> None:
        assert not mpv_client.is_running


class TestMpvClientEvents:
    async def test_event_subscription(self, mpv_client: MpvClient) -> None:
        socket = FakeSocket()
        mpv_client._reader = socket  # type: ignore[assignment]
        mpv_client._writer = socket  # type: ignore[assignment]

        queue = mpv_client.subscribe_event("end-file")

        # Simulate receiving an event (no request_id, just an event)
        socket._response_queue.put_nowait(
            json.dumps({"event": "end-file", "reason": "eof"}) + "\n"
        )

        task = asyncio.create_task(mpv_client._receive_loop())
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert event["event"] == "end-file"

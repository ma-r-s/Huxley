"""Connection lifecycle under slow-closing evicted clients.

Field bug (2026-10-10, phone smoke): an iOS Safari tab was evicted by the
home-screen PWA, but its socket took seconds to finish closing. When its
handler finally exited, cleanup cleared `AudioServer._conn` even though it
pointed at the NEWER client. From then on the server still heard the phone
(ptt, audio) but every outbound frame was dropped: the UI sat on
"thinking" forever.

These tests drive `_handle_connection` with fake sockets whose close we
control, so the slow-close window is deterministic. A real websockets
client closes promptly and hides the bug (see test_firmware_contract's
eviction test, which passed throughout).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

from websockets.exceptions import ConnectionClosedError

from huxley.server.server import AudioServer


class _SlowClosingWS:
    """Fake ServerConnection: `close()` returns immediately but the
    inbound iterator keeps running until `finish()`, like a backgrounded
    phone tab that never answers the close frame."""

    def __init__(self, name: str) -> None:
        self.remote_address = (name, 0)
        self.sent: list[dict[str, Any]] = []
        self._inbound: asyncio.Queue[str | None] = asyncio.Queue()
        self.close_called = False

    async def send(self, msg: str) -> None:
        self.sent.append(json.loads(msg))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_called = True

    def push(self, msg: dict[str, Any]) -> None:
        self._inbound.put_nowait(json.dumps(msg))

    def finish(self) -> None:
        self._inbound.put_nowait(None)

    def __aiter__(self) -> _SlowClosingWS:
        return self

    async def __anext__(self) -> str:
        item = await self._inbound.get()
        if item is None:
            raise ConnectionClosedError(None, None)
        return item


def _server() -> tuple[AudioServer, dict[str, AsyncMock]]:
    mocks = {
        "on_wake_word": AsyncMock(),
        "on_ptt_start": AsyncMock(),
        "on_ptt_stop": AsyncMock(),
        "on_audio_frame": AsyncMock(),
        "on_reset": AsyncMock(),
    }
    return AudioServer(host="127.0.0.1", port=0, **mocks), mocks


async def _until(cond: Any, timeout_s: float = 2.0) -> None:
    """Wait for a condition. structlog's async log calls hop to a thread,
    so a fixed number of event-loop yields is not a reliable barrier."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


async def _connect_both(
    server: AudioServer, old: _SlowClosingWS, new: _SlowClosingWS
) -> tuple[asyncio.Task[None], asyncio.Task[None]]:
    old_task = asyncio.create_task(server._handle_connection(old))  # type: ignore[arg-type]
    await _until(lambda: server._conn is not None and server._conn.ws is old)
    new_task = asyncio.create_task(server._handle_connection(new))  # type: ignore[arg-type]
    await _until(lambda: server._conn is not None and server._conn.ws is new)
    await _until(lambda: any(m.get("type") == "input_mode" for m in new.sent))
    return old_task, new_task


async def test_evicted_client_exit_does_not_cut_off_newer_client() -> None:
    server, _ = _server()
    old, new = _SlowClosingWS("old-tab"), _SlowClosingWS("home-screen-app")

    old_task, new_task = await _connect_both(server, old, new)
    assert old.close_called, "new connection must evict the old one"

    # The evicted tab's socket finally dies, seconds later.
    old.finish()
    await asyncio.wait_for(old_task, 2)

    # The newer client must still receive outbound frames.
    new.sent.clear()
    await server.send_status("hola")
    assert new.sent == [{"type": "status", "message": "hola"}], (
        "evicted handler's cleanup cleared the current client; replies are dropped"
    )

    new.finish()
    await asyncio.wait_for(new_task, 2)


async def test_frames_from_evicted_client_are_not_dispatched() -> None:
    server, mocks = _server()
    old, new = _SlowClosingWS("old-tab"), _SlowClosingWS("home-screen-app")

    old_task, new_task = await _connect_both(server, old, new)

    # A late frame from the evicted tab, still draining, must not drive
    # the app on the new client's behalf. The old handler exits on it.
    old.push({"type": "ptt_start"})
    await asyncio.wait_for(old_task, 2)
    mocks["on_ptt_start"].assert_not_awaited()

    new.push({"type": "ptt_start"})
    await _until(lambda: mocks["on_ptt_start"].await_count == 1)

    new.finish()
    await asyncio.wait_for(new_task, 2)


async def test_client_presence_follows_the_current_client() -> None:
    """The app pauses content while nobody listens (2026-10-12: a stream
    ran 33 min into the silent drop while the walkie was off Wi-Fi). An
    eviction is the old client leaving (T1.44: a walkie reconnecting
    after a Wi-Fi drop evicts its dead socket, and a proactive turn being
    said to it must be said again), then the new one arriving after its
    handshake. The evicted socket's late exit adds nothing."""
    presence: list[bool] = []

    async def on_presence(present: bool) -> None:
        presence.append(present)

    server, _ = _server()
    server._on_client_presence = on_presence
    old, new = _SlowClosingWS("old-tab"), _SlowClosingWS("home-screen-app")

    old_task, new_task = await _connect_both(server, old, new)
    await _until(lambda: presence == [True, False, True])
    old.finish()
    await asyncio.wait_for(old_task, 2)
    assert presence == [True, False, True], "an evicted client's late exit is not news"

    new.finish()
    await asyncio.wait_for(new_task, 2)
    assert presence == [True, False, True, False]


async def test_device_hello_reaches_the_app() -> None:
    """A reconnect's `device.hello` says what the walkie played after the
    drop; the coordinator needs it to settle a cut narration (T1.44)."""
    hellos: list[dict[str, Any]] = []

    async def on_hello(data: dict[str, Any]) -> None:
        hellos.append(data)

    server, _ = _server()
    server._on_device_hello = on_hello
    ws = _SlowClosingWS("walkie")
    task = asyncio.create_task(server._handle_connection(ws))  # type: ignore[arg-type]
    await _until(lambda: any(m.get("type") == "input_mode" for m in ws.sent))

    data = {"schema": 1, "played_after_drop_ms": 4900, "ring_left_ms": 0}
    ws.push({"type": "client_event", "event": "device.hello", "data": data})
    ws.push({"type": "client_event", "event": "device.health", "data": {"rssi": -60}})
    await _until(lambda: hellos == [data])

    ws.finish()
    await asyncio.wait_for(task, 2)
    assert hellos == [data], "only device.hello"

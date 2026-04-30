"""Tests for the Stage-4 `client_event` skill-subscription dispatch and
the symmetric `server_event` outbound path.

Two layers:

- **Registry-level unit tests** drive `_dispatch_client_event` directly
  (no WebSocket) to exercise concurrency, exception isolation, and
  unregister-by-skill semantics. They're cheap and explicit.
- **WS integration tests** stand the server up on an ephemeral port
  and verify the wire path: `client_event` from the client reaches the
  registered handler; `send_server_event` from the server reaches the
  client. Mirror the existing `test_firmware_contract.py` style.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from typing import Any
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.client import connect

from huxley.server.server import AudioServer


def _reserve_free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _wait_for_port(host: str, port: int, timeout_s: float = 2.0) -> None:
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


def _make_server() -> AudioServer:
    """AudioServer with no-op callbacks — enough for registry tests."""
    return AudioServer(
        host="127.0.0.1",
        port=0,
        on_wake_word=AsyncMock(),
        on_ptt_start=AsyncMock(),
        on_ptt_stop=AsyncMock(),
        on_audio_frame=AsyncMock(),
        on_reset=AsyncMock(),
        on_language_select=AsyncMock(),
    )


@contextlib.asynccontextmanager
async def _server_on_ephemeral_port() -> Any:
    """Stand up a real AudioServer on a free port; yield (url, server)."""
    port = _reserve_free_port()
    server = AudioServer(
        host="127.0.0.1",
        port=port,
        on_wake_word=AsyncMock(),
        on_ptt_start=AsyncMock(),
        on_ptt_stop=AsyncMock(),
        on_audio_frame=AsyncMock(),
        on_reset=AsyncMock(),
        on_language_select=AsyncMock(),
    )
    task = asyncio.create_task(server.run())
    try:
        await _wait_for_port("127.0.0.1", port)
        yield f"ws://127.0.0.1:{port}/", server
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _drain_until(ws: Any, predicate: Any, deadline_s: float = 2.0) -> dict[str, Any]:
    """Read messages off the WS until `predicate(msg)` is True. Returns the
    matching message. Each non-matching message is logged and skipped.

    `deadline_s` is the wall-clock budget for the whole drain operation.
    Named to dodge ruff's ASYNC109 (which discourages async funcs taking
    a `timeout=` kwarg in favor of an `asyncio.timeout()` context — but
    here the timeout is for the recv loop's outer budget, not a single
    awaitable).
    """
    deadline = asyncio.get_event_loop().time() + deadline_s
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, remaining))
        msg = json.loads(raw)
        if predicate(msg):
            return msg
    raise AssertionError(f"timed out waiting for predicate; last attempt at {deadline}")


# -------------------------------------------------------- registry-level


class TestSubscriptionRegistry:
    """Direct exercise of `register` / `unregister` / `_dispatch_client_event`
    without the WebSocket — keeps these tests fast and unambiguous about
    what's being asserted."""

    @pytest.mark.asyncio
    async def test_single_subscriber_invoked_with_data(self) -> None:
        server = _make_server()
        handler = AsyncMock()
        server.register_client_event_subscriber("skill_a", "calls.panic", handler)
        await server._dispatch_client_event("calls.panic", {"pressed": True})
        handler.assert_awaited_once_with({"pressed": True})

    @pytest.mark.asyncio
    async def test_no_subscribers_is_noop(self) -> None:
        # Inbound event with no registered subscriber should not raise
        # and should not invoke any handler. The telemetry log path is
        # tested separately at the integration level.
        server = _make_server()
        await server._dispatch_client_event("nobody.cares", {})

    @pytest.mark.asyncio
    async def test_multiple_subscribers_same_key_all_invoked(self) -> None:
        server = _make_server()
        h1 = AsyncMock()
        h2 = AsyncMock()
        h3 = AsyncMock()
        server.register_client_event_subscriber("skill_a", "k", h1)
        server.register_client_event_subscriber("skill_b", "k", h2)
        server.register_client_event_subscriber("skill_c", "k", h3)
        await server._dispatch_client_event("k", {"v": 1})
        h1.assert_awaited_once_with({"v": 1})
        h2.assert_awaited_once_with({"v": 1})
        h3.assert_awaited_once_with({"v": 1})

    @pytest.mark.asyncio
    async def test_one_handler_raising_does_not_block_others(self) -> None:
        server = _make_server()
        ok_before = AsyncMock()
        ok_after = AsyncMock()

        async def raises(_data: dict[str, Any]) -> None:
            raise RuntimeError("subscriber bug")

        server.register_client_event_subscriber("skill_a", "k", ok_before)
        server.register_client_event_subscriber("skill_bad", "k", raises)
        server.register_client_event_subscriber("skill_c", "k", ok_after)
        # Should not raise — the dispatcher swallows handler exceptions
        # and logs each via aexception. Both healthy handlers run.
        await server._dispatch_client_event("k", {})
        ok_before.assert_awaited_once()
        ok_after.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_concurrent_dispatch(self) -> None:
        # Two slow handlers should run in parallel, not serially.
        # Wall-clock check: total dispatch time should be ~max(handler
        # durations), not sum.
        server = _make_server()
        started: list[float] = []

        async def slow(_data: dict[str, Any]) -> None:
            started.append(asyncio.get_event_loop().time())
            await asyncio.sleep(0.05)

        server.register_client_event_subscriber("skill_a", "k", slow)
        server.register_client_event_subscriber("skill_b", "k", slow)
        t0 = asyncio.get_event_loop().time()
        await server._dispatch_client_event("k", {})
        elapsed = asyncio.get_event_loop().time() - t0
        # Both started before either completed (concurrent), so each
        # `started[i]` is within a few ms of t0.
        assert len(started) == 2
        assert max(started) - min(started) < 0.02
        # Total time: ~0.05s (both ran concurrently), not ~0.10s.
        assert elapsed < 0.09

    @pytest.mark.asyncio
    async def test_unregister_removes_only_named_skill(self) -> None:
        server = _make_server()
        h_a = AsyncMock()
        h_b = AsyncMock()
        server.register_client_event_subscriber("skill_a", "k", h_a)
        server.register_client_event_subscriber("skill_b", "k", h_b)

        server.unregister_client_event_subscribers("skill_a")
        await server._dispatch_client_event("k", {})
        h_a.assert_not_awaited()
        h_b.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unregister_idempotent(self) -> None:
        server = _make_server()
        server.unregister_client_event_subscribers("never_registered")
        # Re-running on a name with no subs should also be a no-op.
        h = AsyncMock()
        server.register_client_event_subscriber("skill_a", "k", h)
        server.unregister_client_event_subscribers("skill_a")
        server.unregister_client_event_subscribers("skill_a")
        await server._dispatch_client_event("k", {})
        h.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unregister_drops_empty_key_entry(self) -> None:
        # After all subs for a key are gone, the key disappears from the
        # registry — keeps the dict from growing without bound when many
        # ephemeral keys are registered/unregistered.
        server = _make_server()
        server.register_client_event_subscriber("skill_a", "k", AsyncMock())
        server.unregister_client_event_subscribers("skill_a")
        assert "k" not in server._client_event_subs

    @pytest.mark.asyncio
    async def test_subscriptions_persist_across_dispatch_cycles(self) -> None:
        # Sanity: the registry isn't drained per-event. Same handler
        # invoked on every matching dispatch.
        server = _make_server()
        h = AsyncMock()
        server.register_client_event_subscriber("skill_a", "k", h)
        await server._dispatch_client_event("k", {})
        await server._dispatch_client_event("k", {"again": True})
        await server._dispatch_client_event("k", {})
        assert h.await_count == 3


# ---------------------------------------------------- WS integration


class TestClientEventDispatchOverWebSocket:
    """End-to-end: client sends a `client_event` over the WebSocket, the
    server's existing telemetry logger fires AND any registered skill
    handler runs. Locks the wire-to-handler path."""

    @pytest.mark.asyncio
    async def test_client_event_reaches_registered_handler(self) -> None:
        async with _server_on_ephemeral_port() as (url, server):
            received: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()

            async def on_event(data: dict[str, Any]) -> None:
                if not received.done():
                    received.set_result(data)

            server.register_client_event_subscriber("toy_skill", "demo.ping", on_event)

            async with connect(url) as ws:
                # Drain the server's hello + state + input_mode preamble.
                # The client_event we send and its response are what we care
                # about.
                await ws.send(
                    json.dumps(
                        {
                            "type": "client_event",
                            "event": "demo.ping",
                            "data": {"hi": 1},
                        }
                    )
                )
                data = await asyncio.wait_for(received, timeout=2.0)
                assert data == {"hi": 1}

    @pytest.mark.asyncio
    async def test_client_event_with_missing_data_passes_empty_dict(self) -> None:
        # Some clients (or hand-typed dev_panel JSON) may omit the `data`
        # field. The handler should still be called, with `{}`.
        async with _server_on_ephemeral_port() as (url, server):
            received: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()

            async def on_event(data: dict[str, Any]) -> None:
                if not received.done():
                    received.set_result(data)

            server.register_client_event_subscriber("toy", "demo.bare", on_event)
            async with connect(url) as ws:
                await ws.send(json.dumps({"type": "client_event", "event": "demo.bare"}))
                data = await asyncio.wait_for(received, timeout=2.0)
                assert data == {}


class TestServerEventOverWebSocket:
    """End-to-end: server emits `server_event`; the client receives a
    matching frame on the wire."""

    @pytest.mark.asyncio
    async def test_send_server_event_reaches_client(self) -> None:
        async with (
            _server_on_ephemeral_port() as (url, server),
            connect(url) as ws,
        ):
            # Wait until the client_connected callback fires by reading
            # the hello — guarantees server._client is set before we emit.
            await _drain_until(ws, lambda m: m["type"] == "hello")
            await server.send_server_event("demo.pong", {"counter": 7})
            msg = await _drain_until(ws, lambda m: m.get("type") == "server_event")
            assert msg == {
                "type": "server_event",
                "event": "demo.pong",
                "data": {"counter": 7},
            }

    @pytest.mark.asyncio
    async def test_send_server_event_with_no_client_is_noop(self) -> None:
        # No client ever connects — emit must not raise.
        server = _make_server()
        await server.send_server_event("demo.pong", {"x": 1})

    @pytest.mark.asyncio
    async def test_send_server_event_default_data_is_empty_dict(self) -> None:
        async with (
            _server_on_ephemeral_port() as (url, server),
            connect(url) as ws,
        ):
            await _drain_until(ws, lambda m: m["type"] == "hello")
            await server.send_server_event("demo.empty")
            msg = await _drain_until(ws, lambda m: m.get("type") == "server_event")
            assert msg["data"] == {}


class TestSubscriptionPersistsAcrossReconnect:
    """Subscriptions live on the AudioServer (process lifetime), not on
    individual connections. A skill subscribed before the WS dropped
    should still receive events after a reconnect — without the skill
    re-registering."""

    @pytest.mark.asyncio
    async def test_handler_called_after_reconnect(self) -> None:
        async with _server_on_ephemeral_port() as (url, server):
            count = 0

            async def on_event(_data: dict[str, Any]) -> None:
                nonlocal count
                count += 1

            server.register_client_event_subscriber("toy", "demo.tick", on_event)

            # First connection — fire one event, see one increment.
            async with connect(url) as ws1:
                await ws1.send(json.dumps({"type": "client_event", "event": "demo.tick"}))
                # Give the dispatcher a chance to run.
                for _ in range(20):
                    await asyncio.sleep(0.02)
                    if count >= 1:
                        break

            assert count == 1

            # Second connection — same handler still registered on the
            # AudioServer. Disconnecting and reconnecting should not have
            # cleared it.
            async with connect(url) as ws2:
                await ws2.send(json.dumps({"type": "client_event", "event": "demo.tick"}))
                for _ in range(20):
                    await asyncio.sleep(0.02)
                    if count >= 2:
                        break
            assert count == 2

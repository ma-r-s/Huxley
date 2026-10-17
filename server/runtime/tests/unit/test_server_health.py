"""`GET /health` on the voice port (docs/protocol.md "HTTP: /health").

The server answers it in `process_request` as plain HTTP, next to the
voice WebSocket on the same port. What must hold:

- loopback and Tailscale get 200 + JSON; anyone else gets 404;
- the WebSocket handshake at `/` is untouched, and a health request
  never evicts or disturbs the connected client (a second WebSocket
  client would: it evicts the walkie);
- the server's fields (`client`, `walkie_last_seen`, `recording`) follow
  connects, device events and disconnects.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from websockets.asyncio.client import connect

from huxley.server import server as server_mod
from huxley.server.server import AudioServer, health_source_allowed
from tests.unit.http_probe import free_port, http_get, until, wait_for_http

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


@contextlib.asynccontextmanager
async def _serving(
    get_health: Callable[[], dict[str, object]] | None,
) -> AsyncIterator[tuple[int, AudioServer]]:
    port = free_port()
    server = AudioServer(
        host="127.0.0.1",
        port=port,
        on_wake_word=AsyncMock(),
        on_ptt_start=AsyncMock(),
        on_ptt_stop=AsyncMock(),
        on_audio_frame=AsyncMock(),
        on_reset=AsyncMock(),
        get_health=get_health,
    )
    task = asyncio.create_task(server.run())
    try:
        await wait_for_http(port, "/")
        yield port, server
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _runtime_part() -> dict[str, object]:
    return {"ok": True, "persona": "abuelos"}


async def _health(port: int) -> dict[str, Any]:
    reply = await http_get(port, "/health")
    assert reply.status == 200, reply
    data: dict[str, Any] = reply.json()
    return data


async def _recv_type(ws: Any, msg_type: str) -> dict[str, Any]:
    while True:
        msg: dict[str, Any] = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
        if msg.get("type") == msg_type:
            return msg


class TestHealthResponse:
    async def test_answers_json_without_upgrading(self) -> None:
        async with _serving(_runtime_part) as (port, _server):
            reply = await http_get(port, "/health")
        assert reply.status == 200
        assert reply.headers["content-type"] == "application/json"
        assert reply.headers["cache-control"] == "no-store"
        assert "upgrade" not in reply.headers
        assert reply.json() == {
            "ok": True,
            "persona": "abuelos",
            "client": None,
            "walkie_last_seen": None,
            "recording": False,
        }

    async def test_query_string_does_not_change_the_route(self) -> None:
        async with _serving(_runtime_part) as (port, _server):
            reply = await http_get(port, "/health?verbose=1")
        assert reply.status == 200
        assert reply.json()["ok"] is True

    async def test_failure_building_it_is_a_500_and_logged(self) -> None:
        def _broken() -> dict[str, object]:
            msg = "boom"
            raise RuntimeError(msg)

        async with _serving(_broken) as (port, _server):
            with structlog.testing.capture_logs() as logs:
                reply = await http_get(port, "/health")
        assert reply.status == 500
        assert reply.json() == {"ok": False, "error": "RuntimeError: boom"}
        assert "server.health_failed" in [e["event"] for e in logs]

    async def test_recording_reflects_the_attached_recorder(self) -> None:
        async with _serving(_runtime_part) as (port, server):
            server.set_recorder(MagicMock())
            assert (await _health(port))["recording"] is True
            server.set_recorder(None)
            assert (await _health(port))["recording"] is False


class TestWho:
    async def test_current_client_then_last_client(self) -> None:
        async with _serving(_runtime_part) as (port, server):
            async with connect(f"ws://127.0.0.1:{port}/") as ws:
                hello = await _recv_type(ws, "hello")
                await ws.send(
                    json.dumps(
                        {"type": "client_event", "event": "device", "data": {"shell": "browser"}}
                    )
                )
                await until(lambda: server._conn is not None and server._conn.shell == "browser")
                health = await _health(port)
                client = health["client"]
                assert client["connected"] is True
                assert client["conn_id"] == hello["conn_id"]
                assert client["audio_format"] == "pcm"
                assert client["shell"] == "browser"
                datetime.fromisoformat(client["since"])
                # A browser is not the walkie.
                assert health["walkie_last_seen"] is None
            await until(lambda: not server.has_client)
            client = (await _health(port))["client"]
            assert client["connected"] is False
            assert client["conn_id"] == hello["conn_id"]

    @pytest.mark.parametrize(
        ("event", "data"),
        [
            ("device", {"shell": "firmware", "board": "esp32s3-audio-board"}),
            ("device.hello", {"schema": 1, "device_id": "02:00:00:00:00:01"}),
        ],
    )
    async def test_walkie_last_seen_follows_a_firmware_client(
        self, event: str, data: dict[str, object]
    ) -> None:
        async with _serving(_runtime_part) as (port, server):
            assert (await _health(port))["walkie_last_seen"] is None
            async with connect(f"ws://127.0.0.1:{port}/") as ws:
                await _recv_type(ws, "hello")
                await ws.send(json.dumps({"type": "client_event", "event": event, "data": data}))
                await until(lambda: server._conn is not None and server._conn.shell == "firmware")
                health = await _health(port)
                assert health["client"]["shell"] == "firmware"
                connected_seen = datetime.fromisoformat(health["walkie_last_seen"])
            await until(lambda: not server.has_client)
            health = await _health(port)
            assert health["client"]["connected"] is False
            # Still known after it left: when it was last there.
            gone_seen = datetime.fromisoformat(health["walkie_last_seen"])
            assert gone_seen >= connected_seen.replace(microsecond=0)


class TestAccessControl:
    @pytest.mark.parametrize(
        ("remote", "allowed"),
        [
            (("127.0.0.1", 5000), True),
            (("127.8.9.10", 5000), True),
            (("::1", 5000, 0, 0), True),
            (("::ffff:127.0.0.1", 5000, 0, 0), True),
            (("100.64.0.1", 5000), True),
            (("100.101.102.103", 5000), True),
            (("100.127.255.254", 5000), True),
            (("fd7a:115c:a1e0::1", 5000, 0, 0), True),
            (("100.128.0.1", 5000), False),
            (("192.168.68.69", 5000), False),
            (("10.0.0.5", 5000), False),
            (("8.8.8.8", 5000), False),
            (("::ffff:192.168.1.2", 5000, 0, 0), False),
            (("fe80::1%en0", 5000, 0, 0), False),
            (("not-an-ip", 5000), False),
            (None, False),
            ("127.0.0.1", False),
        ],
    )
    def test_source_allowed(self, remote: object, allowed: bool) -> None:
        assert health_source_allowed(remote) is allowed

    async def test_other_sources_get_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The test can only connect from loopback, so take loopback off
        # the list: the request is then from "somewhere else".
        monkeypatch.setattr(
            server_mod, "_HEALTH_NETWORKS", (ipaddress.ip_network("100.64.0.0/10"),)
        )
        async with _serving(_runtime_part) as (port, _server):
            with structlog.testing.capture_logs() as logs:
                reply = await http_get(port, "/health")
        assert reply.status == 404
        assert b"ok" not in reply.body
        refused = [e for e in logs if e["event"] == "server.health_refused"]
        assert refused and "127.0.0.1" in refused[0]["remote"]
        assert refused[0]["reason"] == "source"

    @pytest.mark.parametrize(
        "header",
        [
            {"CF-Connecting-IP": "203.0.113.9", "CF-Ray": "8c1f-BOG"},
            {"X-Forwarded-For": "203.0.113.9"},
            {"Forwarded": "for=203.0.113.9"},
            {"Tailscale-Funnel-Request": "?1"},
        ],
    )
    async def test_relayed_by_a_proxy_gets_404_even_from_loopback(
        self, header: dict[str, str]
    ) -> None:
        """cloudflared connects from loopback; its headers give it away."""
        async with _serving(_runtime_part) as (port, _server):
            with structlog.testing.capture_logs() as logs:
                reply = await http_get(port, "/health", headers=header)
        assert reply.status == 404
        refused = [e for e in logs if e["event"] == "server.health_refused"]
        assert refused and refused[0]["reason"] == "proxied"


class TestVoiceSocketUnaffected:
    async def test_handshake_still_works_next_to_health(self) -> None:
        async with _serving(_runtime_part) as (port, _server):
            assert (await http_get(port, "/health")).status == 200
            async with connect(f"ws://127.0.0.1:{port}/") as ws:
                hello = await _recv_type(ws, "hello")
                assert hello["protocol"] == 2
            async with connect(f"ws://127.0.0.1:{port}/?lang=es&audio=pcm") as ws:
                hello = await _recv_type(ws, "hello")
                assert hello["language"] == "es"

    async def test_health_never_evicts_the_connected_client(self) -> None:
        async with (
            _serving(_runtime_part) as (port, server),
            connect(f"ws://127.0.0.1:{port}/") as ws,
        ):
            hello = await _recv_type(ws, "hello")
            for _ in range(3):
                health = await _health(port)
                assert health["client"]["conn_id"] == hello["conn_id"]
            assert server.conn_id == hello["conn_id"]
            await ws.send(json.dumps({"type": "ping", "ts": 1}))
            pong = await _recv_type(ws, "pong")
            assert pong["ts"] == 1

    async def test_health_does_not_consume_the_next_connects_query(self) -> None:
        """`?lang=` is parked on the server between process_request and the
        connection handler; a health hit in between must not clobber it."""
        async with _serving(_runtime_part) as (port, server):
            server._pending_language = "en"  # as if an upgrade were mid-handshake
            await http_get(port, "/health?lang=fr")
            assert server._pending_language == "en"

    @pytest.mark.parametrize("serve_health", [True, False])
    async def test_other_plain_http_requests_keep_todays_answer(self, serve_health: bool) -> None:
        async with _serving(_runtime_part if serve_health else None) as (port, _server):
            root = await http_get(port, "/")
            other = await http_get(port, "/status")
        assert root.status == 426
        assert other.status == 426

    async def test_without_a_health_source_the_path_is_not_served(self) -> None:
        async with _serving(None) as (port, _server):
            reply = await http_get(port, "/health")
        assert reply.status == 426

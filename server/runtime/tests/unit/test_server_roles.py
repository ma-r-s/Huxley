"""The production role on the voice socket (docs/protocol.md
"Authentication and roles"; remote-operation.md decision 4, W2).

Production mode = a device token is configured. Then:

- an upgrade relayed by a proxy (the Cloudflare tunnel arrives from
  loopback with its headers) or from anywhere but loopback and Tailscale
  needs the token: 401 without it;
- the token makes the connection the `device`: the firmware's messages
  only, `?persona=` ignored;
- loopback and Tailscale without the token keep everything (`local`), but
  never evict the connected device unless they ask with `?evict=1`;
- `/health` and `/admin` refuse anything relayed, token or not.

Without a token nothing changes (`dev`); the rest of the suite runs that way.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
import structlog
from pydantic import SecretStr
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from huxley.server import server as server_mod
from huxley.server.server import ROLE_DEVICE, ROLE_LOCAL, AudioServer, _Access
from tests.unit.http_probe import free_port, http_get, until, wait_for_http

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

TOKEN = "roles-test-token-0123456789abcdefghij"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TUNNEL = {"Cf-Connecting-Ip": "203.0.113.9", "Cf-Ray": "8c1f-BOG"}


class Callbacks:
    def __init__(self) -> None:
        self.wake_word = AsyncMock()
        self.ptt_start = AsyncMock()
        self.ptt_stop = AsyncMock()
        self.audio_frame = AsyncMock()
        self.reset = AsyncMock()
        self.language = AsyncMock()
        self.list_sessions = AsyncMock()
        self.restart = AsyncMock()
        self.install = AsyncMock()
        self.persona = AsyncMock()


@contextlib.asynccontextmanager
async def _serving(
    token: str | None = TOKEN, fwdir: Path | None = None
) -> AsyncIterator[tuple[int, AudioServer, Callbacks]]:
    cb = Callbacks()
    port = free_port()
    server = AudioServer(
        host="127.0.0.1",
        port=port,
        on_wake_word=cb.wake_word,
        on_ptt_start=cb.ptt_start,
        on_ptt_stop=cb.ptt_stop,
        on_audio_frame=cb.audio_frame,
        on_reset=cb.reset,
        on_language_select=cb.language,
        on_list_sessions=cb.list_sessions,
        on_restart_server=cb.restart,
        on_install_skill=cb.install,
        on_persona_select=cb.persona,
        get_health=lambda: {"ok": True},
        firmware_dir=fwdir,
        device_token=SecretStr(token) if token is not None else None,
    )
    task = asyncio.create_task(server.run())
    try:
        await wait_for_http(port, "/health")
        yield port, server, cb
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _open(
    port: int, path: str = "/", headers: dict[str, str] | None = None
) -> tuple[ClientConnection, dict[str, Any]]:
    ws = await connect(f"ws://127.0.0.1:{port}{path}", additional_headers=headers)
    while True:
        msg: dict[str, Any] = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
        if msg.get("type") == "hello":
            return ws, msg


async def _refused_status(
    port: int, path: str = "/", headers: dict[str, str] | None = None
) -> int:
    with pytest.raises(InvalidStatus) as exc:
        await connect(f"ws://127.0.0.1:{port}{path}", additional_headers=headers)
    return exc.value.response.status_code


async def _pong(ws: ClientConnection, ts: int) -> None:
    """A ping round trip: everything sent before it has been dispatched."""
    await ws.send(json.dumps({"type": "ping", "ts": ts}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
        if msg.get("type") == "pong" and msg.get("ts") == ts:
            return


@pytest.fixture
def not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loopback off the local list: the test's requests come from 'the LAN'."""
    monkeypatch.setattr(server_mod, "_HEALTH_NETWORKS", (ipaddress.ip_network("100.64.0.0/10"),))


class TestUpgrade:
    @pytest.mark.parametrize(
        ("headers", "reason"),
        [(TUNNEL, "missing"), ({**TUNNEL, "Authorization": "Bearer nope"}, "bad_token")],
    )
    async def test_external_without_the_token_is_401(
        self, headers: dict[str, str], reason: str
    ) -> None:
        async with _serving() as (port, server, _cb):
            with structlog.testing.capture_logs() as logs:
                status = await _refused_status(port, headers=headers)
            assert not server.has_client
        assert status == 401
        refused = [e for e in logs if e["event"] == "server.auth.refused"]
        assert refused[0]["reason"] == reason
        assert refused[0]["external"] is True
        assert refused[0]["forwarded_for"] == "203.0.113.9"
        assert TOKEN not in repr(logs)

    async def test_other_sources_without_the_token_are_401(self, not_local: None) -> None:
        async with _serving() as (port, _server, _cb):
            with structlog.testing.capture_logs() as logs:
                status = await _refused_status(port, path="/?evict=1")
        assert status == 401
        refused = [e for e in logs if e["event"] == "server.auth.refused"]
        assert (refused[0]["external"], refused[0]["source"]) == (False, "other")

    @pytest.mark.parametrize("headers", [{**TUNNEL, **AUTH}, AUTH])
    async def test_the_token_makes_the_device(
        self, headers: dict[str, str], not_local: None
    ) -> None:
        async with _serving() as (port, server, _cb):
            with structlog.testing.capture_logs() as logs:
                ws, _hello = await _open(port, headers=headers)
                await until(lambda: server._conn is not None)
                assert server._conn is not None and server._conn.role == ROLE_DEVICE
                await ws.close()
        accepted = [e for e in logs if e["event"] == "server.auth.accepted"]
        assert accepted[0]["role"] == "device"
        assert accepted[0]["token"] == "valid"
        assert TOKEN not in repr(logs)

    async def test_local_without_the_token_keeps_everything(self) -> None:
        async with _serving() as (port, server, cb):
            ws, _hello = await _open(port, path="/?persona=basic")
            await ws.send(json.dumps({"type": "list_sessions"}))
            await _pong(ws, 1)
            assert server._conn is not None and server._conn.role == ROLE_LOCAL
            await ws.close()
        cb.list_sessions.assert_awaited_once()
        assert cb.persona.await_args.args[0] == "basic"

    async def test_production_off_changes_nothing(self) -> None:
        """No token: a relayed upgrade is accepted with every message, as before."""
        async with _serving(token=None) as (port, server, cb):
            with structlog.testing.capture_logs() as logs:
                ws, _hello = await _open(port, headers=TUNNEL)
                await ws.send(json.dumps({"type": "list_sessions"}))
                await _pong(ws, 1)
                assert server._conn is not None and server._conn.role == "dev"
                await ws.close()
        cb.list_sessions.assert_awaited_once()
        assert not [e for e in logs if e["event"].startswith("server.auth.")]


class TestDeviceMessages:
    async def test_only_the_firmwares_messages(self) -> None:
        async with _serving() as (port, _server, cb):
            ws, _hello = await _open(port, headers=AUTH)
            with structlog.testing.capture_logs() as logs:
                for msg in (
                    {"type": "list_sessions"},
                    {"type": "list_sessions"},
                    {"type": "get_diag"},
                    {"type": "install_skill", "package": "huxley-skill-evil"},
                    {"type": "restart_server"},
                    {"type": "set_language", "language": "en"},
                    {"type": "reset"},
                    {"type": "set_skill_secret", "skill": "x", "key": "k", "value": "v"},
                ):
                    await ws.send(json.dumps(msg))
                # Allowed: what the firmware sends.
                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(json.dumps({"type": "audio", "data": "AAAA"}))
                await ws.send(json.dumps({"type": "ptt_stop"}))
                await ws.send(json.dumps({"type": "wake_word"}))
                await ws.send(
                    json.dumps({"type": "client_event", "event": "device.health", "data": {}})
                )
                await _pong(ws, 7)
            await ws.close()
        for refused in (cb.list_sessions, cb.install, cb.restart, cb.reset):
            refused.assert_not_awaited()
        # Once by the handshake itself, never by `set_language`.
        assert cb.language.await_args_list == [((None,),)]
        cb.ptt_start.assert_awaited_once()
        cb.ptt_stop.assert_awaited_once()
        cb.wake_word.assert_awaited_once()
        cb.audio_frame.assert_awaited_once()
        refused_lines = [e for e in logs if e["event"] == "server.rx.refused_for_role"]
        assert [e["msg_type"] for e in refused_lines] == [
            "list_sessions",  # once, though sent twice
            "get_diag",
            "install_skill",
            "restart_server",
            "set_language",
            "reset",
            "set_skill_secret",
        ]
        assert all(e["role"] == "device" for e in refused_lines)
        assert "client.device.health" in [e["event"] for e in logs]

    async def test_persona_is_ignored_for_the_device(self) -> None:
        async with _serving() as (port, _server, cb):
            with structlog.testing.capture_logs() as logs:
                ws, _hello = await _open(port, path="/?persona=basic&lang=es", headers=AUTH)
                await ws.close()
        assert cb.persona.await_args.args == (None, "es")
        accepted = [e for e in logs if e["event"] == "server.auth.accepted"]
        assert accepted[0]["ignored_persona"] == "basic"


class TestEviction:
    async def test_a_local_client_cannot_evict_the_device(self) -> None:
        async with _serving() as (port, server, _cb):
            device, hello = await _open(port, headers=AUTH)
            with structlog.testing.capture_logs() as logs:
                assert await _refused_status(port) == 409
            assert server.conn_id == hello["conn_id"]
            await _pong(device, 1)  # still connected, still served
            await device.close()
        assert "server.auth.evict_refused" in [e["event"] for e in logs]

    async def test_unless_it_asks_with_evict(self) -> None:
        async with _serving() as (port, server, _cb):
            device, _hello = await _open(port, headers=AUTH)
            local, hello = await _open(port, path="/?evict=1")
            await until(lambda: server.conn_id == hello["conn_id"])
            with contextlib.suppress(ConnectionClosed):
                await asyncio.wait_for(device.recv(), timeout=2)
            assert device.close_code == 1001
            await local.close()

    async def test_a_device_replaces_a_device(self) -> None:
        """The walkie reconnecting before its old socket timed out."""
        async with _serving() as (port, server, _cb):
            old, _ = await _open(port, headers=AUTH)
            new, hello = await _open(port, headers=AUTH)
            await until(lambda: server.conn_id == hello["conn_id"])
            with contextlib.suppress(ConnectionClosed):
                await asyncio.wait_for(old.recv(), timeout=2)
            assert old.close_code == 1001
            await new.close()

    async def test_a_late_arrival_is_still_refused(self) -> None:
        """The device connected while this upgrade's handshake finished."""
        async with _serving() as (port, server, _cb):
            device, _hello = await _open(port, headers=AUTH)

            class _LateWS:
                remote_address = ("127.0.0.1", 5555)
                request = None
                close = AsyncMock()

            late = _LateWS()
            admitted = await server._admit(
                late,  # type: ignore[arg-type]
                _Access(ROLE_LOCAL, "local", "missing"),
                "late0001",
            )
            assert admitted is False
            late.close.assert_awaited_once()
            assert late.close.await_args.args[0] == 1008
            await _pong(device, 2)
            await device.close()


class TestLocalOnlyRoutes:
    async def test_health_and_admin_refuse_relayed_requests_even_with_the_token(
        self, tmp_path: Path
    ) -> None:
        async with _serving(fwdir=tmp_path / "firmware") as (port, _server, _cb):
            health = await http_get(port, "/health", headers={**TUNNEL, **AUTH})
            admin = await http_get(
                port, "/admin/firmware", headers={**TUNNEL, **AUTH, "X-Huxley-Admin": "1"}
            )
            direct = await http_get(port, "/health")
        assert health.status == 404
        assert admin.status == 404
        assert direct.status == 200

"""Firmware images, the admin routes and the accept flow, over the wire
(docs/protocol.md "HTTP: /firmware and /admin").

A real AudioServer on a free port; plain HTTP for the routes and a real
WebSocket client playing the walkie (it sends `device.hello` and answers
`dev.cmd`). What must hold:

- `/firmware/*` answers only with the device token, from any source (the
  walkie comes through the tunnel, as loopback with proxy headers);
- only the offered version and the one before it can be downloaded, and
  no path leaves the firmware directory;
- `/admin/*` answers only loopback and Tailscale, never a proxy, and only
  requests a browser page couldn't send on its own;
- dev-cmd round-trips through the walkie's socket;
- an offer reaches the walkie now or at its next hello, never when it
  already runs that version;
- the accept flow: pending hello, self-test, manifest GET, a 1012 close,
  the second hello, `ota.accept`;
- the voice socket and `/health` don't notice any of it.
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
from websockets.exceptions import ConnectionClosed

from huxley.server import firmware as fw
from huxley.server import server as server_mod
from huxley.server.server import AudioServer
from tests.unit.firmware_images import make_image
from tests.unit.http_probe import HttpReply, free_port, http_get, until, wait_for_http

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

TOKEN = "wire-test-token-abcdefghijklmnop"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ADMIN = {"X-Huxley-Admin": "1"}


@contextlib.asynccontextmanager
async def _serving(
    fwdir: Path | None, token: str | None = TOKEN
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
        get_health=lambda: {"ok": True},
        firmware_dir=fwdir,
        device_token=SecretStr(token) if token is not None else None,
    )
    task = asyncio.create_task(server.run())
    try:
        await wait_for_http(port, "/health")
        yield port, server
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.fixture
def fwdir(tmp_path: Path) -> Path:
    return tmp_path / "firmware"


@pytest.fixture
def quick_close(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fw, "RECONNECT_CLOSE_DELAY_S", 0.05)


def _hello(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "protocol": 2,
        "device_id": "a0:f2:62:e2:fb:60",
        "board": "esp32s3-audio-board",
        "version": "v1",
        "elf_sha": "111111111",
        "slot": "ota_0",
        "slot_state": "valid",
        "reset": "SW_RESET",
        "boot_counter": 7,
        "uptime_s": 9,
        "audio": "opus",
        "caps": ["ota.v1", "dev_cmd.v1", "health.v1"],
        "rollback": None,
    }
    base.update(fields)
    return base


async def _recv(ws: ClientConnection, timeout_s: float = 2.0) -> dict[str, Any]:
    msg: dict[str, Any] = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout_s))
    return msg


async def _recv_type(ws: ClientConnection, msg_type: str) -> dict[str, Any]:
    while True:
        msg = await _recv(ws)
        if msg.get("type") == msg_type:
            return msg


async def _recv_event(ws: ClientConnection, event: str, timeout_s: float = 2.0) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        left = deadline - asyncio.get_running_loop().time()
        msg = await _recv(ws, timeout_s=max(left, 0.01))
        if msg.get("type") == "server_event" and msg.get("event") == event:
            data: dict[str, Any] = msg["data"]
            return data


async def _no_event(ws: ClientConnection, event: str, wait_s: float = 0.3) -> None:
    with contextlib.suppress(TimeoutError):
        await _recv_event(ws, event, timeout_s=wait_s)
        raise AssertionError(f"unexpected server_event {event}")


async def _walkie(
    port: int, server: AudioServer, hello: dict[str, Any], *, token: bool = True
) -> ClientConnection:
    """Connect as the walkie (with the device token on the upgrade, as the
    contract says) and say hello; returns once the server took it in."""
    ws = await connect(
        f"ws://127.0.0.1:{port}/?audio=pcm", additional_headers=AUTH if token else None
    )
    server_hello = await _recv_type(ws, "hello")
    await _send_event(ws, "device.hello", hello)
    firmware = server._firmware
    assert firmware is not None
    await until(
        lambda: (
            (device := firmware._current_device()) is not None
            and device.conn_id == server_hello["conn_id"]
            and device.hello is not None
            and device.hello.get("uptime_s") == hello.get("uptime_s")
        )
    )
    return ws


async def _send_event(ws: ClientConnection, event: str, data: dict[str, Any]) -> None:
    await ws.send(json.dumps({"type": "client_event", "event": event, "data": data}))


async def _admin(port: int, path: str, **kwargs: Any) -> HttpReply:
    return await http_get(port, path, headers={**ADMIN, **kwargs.pop("headers", {})}, **kwargs)


async def _closed_code(ws: ClientConnection, timeout_s: float = 2.0) -> int | None:
    with contextlib.suppress(ConnectionClosed):
        while True:
            await asyncio.wait_for(ws.recv(), timeout=timeout_s)
    return ws.close_code


class TestManifest:
    async def test_nothing_offered_is_404(self, fwdir: Path) -> None:
        async with _serving(fwdir) as (port, _server):
            reply = await http_get(port, "/firmware/manifest.json", headers=AUTH)
        assert reply.status == 404
        assert reply.json() == {"error": "no firmware offered"}

    async def test_offered(self, fwdir: Path) -> None:
        sha = make_image(fwdir, "v0.5.0-2-gabc1234", size=3000)
        fw.FirmwareStore(fwdir).write_offer("v0.5.0-2-gabc1234", force=False)
        async with _serving(fwdir) as (port, _server):
            with structlog.testing.capture_logs() as logs:
                reply = await http_get(port, "/firmware/manifest.json?x=1", headers=AUTH)
        assert reply.status == 200
        assert reply.headers["content-type"] == "application/json"
        assert reply.headers["cache-control"] == "no-store"
        assert reply.json() == {
            "version": "v0.5.0-2-gabc1234",
            "url": "/firmware/v0.5.0-2-gabc1234.bin",
            "size": 3000,
            "elf_sha": sha,
        }
        served = [e for e in logs if e["event"] == "firmware.manifest_served"]
        assert served and served[0]["version"] == "v0.5.0-2-gabc1234"
        assert served[0]["bytes"] == 3000

    @pytest.mark.parametrize(
        ("headers", "reason"),
        [
            ({}, "missing"),
            ({"Authorization": "Bearer nope"}, "bad_token"),
            ({"Authorization": TOKEN}, "missing"),
        ],
    )
    async def test_without_the_token_is_401(
        self, fwdir: Path, headers: dict[str, str], reason: str
    ) -> None:
        make_image(fwdir, "v1")
        fw.FirmwareStore(fwdir).write_offer("v1", force=False)
        async with _serving(fwdir) as (port, _server):
            with structlog.testing.capture_logs() as logs:
                manifest = await http_get(port, "/firmware/manifest.json", headers=headers)
                image = await http_get(port, "/firmware/v1.bin", headers=headers)
        assert manifest.status == image.status == 401
        assert manifest.headers["www-authenticate"] == "Bearer"
        assert b"v1" not in manifest.body and len(image.body) < 100
        refused = [e for e in logs if e["event"] == "firmware.auth_refused"]
        assert [e["reason"] for e in refused] == [reason, reason]
        assert TOKEN not in repr(logs)

    async def test_token_unset_is_503_never_open(self, fwdir: Path) -> None:
        make_image(fwdir, "v1")
        fw.FirmwareStore(fwdir).write_offer("v1", force=False)
        async with _serving(fwdir, token=None) as (port, _server):
            with structlog.testing.capture_logs() as logs:
                reply = await http_get(port, "/firmware/manifest.json", headers=AUTH)
                image = await http_get(port, "/firmware/v1.bin", headers=AUTH)
        assert reply.status == image.status == 503
        refused = [e for e in logs if e["event"] == "firmware.auth_refused"]
        assert [e["reason"] for e in refused] == ["unconfigured", "unconfigured"]
        assert all(e["log_level"] == "warning" for e in refused)

    async def test_any_source_with_the_token(
        self, fwdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not address-gated: through the tunnel the walkie is loopback with
        Cloudflare's headers, and elsewhere a LAN address."""
        monkeypatch.setattr(
            server_mod, "_HEALTH_NETWORKS", (ipaddress.ip_network("100.64.0.0/10"),)
        )
        make_image(fwdir, "v1")
        fw.FirmwareStore(fwdir).write_offer("v1", force=False)
        async with _serving(fwdir) as (port, _server):
            with structlog.testing.capture_logs() as logs:
                reply = await http_get(
                    port,
                    "/firmware/manifest.json",
                    headers={**AUTH, "Cf-Connecting-Ip": "203.0.113.9", "Cf-Ray": "8c1f-BOG"},
                )
        assert reply.status == 200
        served = [e for e in logs if e["event"] == "firmware.manifest_served"]
        assert served[0]["forwarded_for"] == "203.0.113.9"


class TestImage:
    async def test_streams_the_whole_image(self, fwdir: Path) -> None:
        make_image(fwdir, "v2", size=2 * 1024 * 1024 + 17)
        fw.FirmwareStore(fwdir).write_offer("v2", force=False)
        expected = (fwdir / "v2.bin").read_bytes()
        async with _serving(fwdir) as (port, _server):
            with structlog.testing.capture_logs() as logs:
                reply = await http_get(port, "/firmware/v2.bin", headers=AUTH, timeout_s=10)
        assert reply.status == 200
        assert reply.headers["content-type"] == "application/octet-stream"
        assert reply.headers["content-length"] == str(len(expected))
        assert reply.body == expected
        served = [e for e in logs if e["event"] == "firmware.image_served"]
        assert served[0]["bytes"] == len(expected)

    async def test_a_slow_reader_gets_the_whole_image(self, fwdir: Path) -> None:
        """The answer drains at the reader's pace; only the handshake limit
        bounds it (websockets' default close deadline would cut the tail)."""
        make_image(fwdir, "v2", size=3 * 1024 * 1024)
        fw.FirmwareStore(fwdir).write_offer("v2", force=False)
        expected = (fwdir / "v2.bin").read_bytes()
        async with _serving(fwdir) as (port, _server):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(
                f"GET /firmware/v2.bin HTTP/1.1\r\nHost: x\r\n"
                f"Authorization: Bearer {TOKEN}\r\n\r\n".encode()
            )
            await writer.drain()
            raw = b""
            while chunk := await asyncio.wait_for(reader.read(256 * 1024), timeout=10):
                raw += chunk
                await asyncio.sleep(0.05)  # about 5 MB/s at most, the kernel buffers aside
            writer.close()
        _head, _, body = raw.partition(b"\r\n\r\n")
        assert body == expected

    async def test_only_the_offered_and_the_one_before(self, fwdir: Path) -> None:
        store = fw.FirmwareStore(fwdir)
        for version in ("v1", "v2", "v3"):
            make_image(fwdir, version)
            store.write_offer(version, force=False)
        async with _serving(fwdir) as (port, _server):
            codes = {
                v: (await http_get(port, f"/firmware/{v}.bin", headers=AUTH)).status
                for v in ("v1", "v2", "v3", "v4")
            }
        assert codes == {"v1": 404, "v2": 200, "v3": 200, "v4": 404}

    @pytest.mark.parametrize(
        "path",
        [
            "/firmware/../offer.json",
            "/firmware/..%2Foffer.json",
            "/firmware/..%2F..%2Fetc%2Fpasswd.bin",
            "/firmware/v1/../v1.bin",
            "/firmware/%2e%2e/v1.bin",
            "/firmware/v1.elf",
            "/firmware/offer.json",
            "/firmware/.v1.bin",
            "/firmware/",
        ],
    )
    async def test_no_path_leaves_the_directory(self, fwdir: Path, path: str) -> None:
        make_image(fwdir, "v1")
        fw.FirmwareStore(fwdir).write_offer("v1", force=False)
        (fwdir.parent / "secret.bin").write_bytes(b"do not serve")
        async with _serving(fwdir) as (port, _server):
            reply = await http_get(port, path, headers=AUTH)
        assert reply.status == 404
        assert b"\x7fELF" not in reply.body and b"do not serve" not in reply.body
        assert len(reply.body) < 100


class TestAdminGuard:
    async def test_loopback_with_the_header(self, fwdir: Path) -> None:
        async with _serving(fwdir) as (port, _server):
            reply = await _admin(port, "/admin/firmware")
        assert reply.status == 200
        assert reply.json()["offer"] is None
        assert reply.json()["token_configured"] is True

    async def test_other_sources_get_404(
        self, fwdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            server_mod, "_HEALTH_NETWORKS", (ipaddress.ip_network("100.64.0.0/10"),)
        )
        async with _serving(fwdir) as (port, _server):
            with structlog.testing.capture_logs() as logs:
                reply = await _admin(port, "/admin/firmware-offer?version=v1")
        assert reply.status == 404
        refused = [e for e in logs if e["event"] == "firmware.admin_refused"]
        assert refused[0]["reason"] == "source"
        assert not (fwdir / "offer.json").exists()

    @pytest.mark.parametrize(
        "header",
        [
            {"Cf-Connecting-Ip": "203.0.113.9"},
            {"X-Forwarded-For": "203.0.113.9"},
            {"Tailscale-Funnel-Request": "?1"},
        ],
    )
    async def test_proxied_gets_404_even_from_loopback(
        self, fwdir: Path, header: dict[str, str]
    ) -> None:
        async with _serving(fwdir) as (port, _server):
            with structlog.testing.capture_logs() as logs:
                reply = await _admin(port, "/admin/firmware", headers=header)
        assert reply.status == 404
        refused = [e for e in logs if e["event"] == "firmware.admin_refused"]
        assert refused[0]["reason"] == "proxied"

    async def test_without_the_admin_header_is_403(self, fwdir: Path) -> None:
        """A cross-site page can make the browser GET a URL, not add a header."""
        async with _serving(fwdir) as (port, _server):
            reply = await http_get(port, "/admin/dev-cmd?line=reboot")
        assert reply.status == 403

    async def test_a_foreign_host_is_403(self, fwdir: Path) -> None:
        """DNS rebinding: the page's own name, pointed at 127.0.0.1."""
        async with _serving(fwdir) as (port, _server):
            reply = await _admin(port, "/admin/firmware", host=f"evil.example.com:{port}")
        assert reply.status == 403

    async def test_unknown_admin_path_is_404(self, fwdir: Path) -> None:
        async with _serving(fwdir) as (port, _server):
            reply = await _admin(port, "/admin/nope")
        assert reply.status == 404


class TestDevCmd:
    async def test_round_trip_through_the_walkie(self, fwdir: Path) -> None:
        async with _serving(fwdir) as (port, server):
            ws = await _walkie(port, server, _hello())

            async def answer() -> dict[str, Any]:
                cmd = await _recv_event(ws, "dev.cmd")
                await _send_event(
                    ws,
                    "dev.cmd_result",
                    {"id": cmd["id"], "output": "heap 123456\n", "truncated": False},
                )
                return cmd

            walkie = asyncio.create_task(answer())
            with structlog.testing.capture_logs() as logs:
                reply = await _admin(port, "/admin/dev-cmd?line=info%20heap&timeout_s=2")
            cmd = await walkie
            await ws.close()
        assert cmd["line"] == "info heap"
        assert reply.status == 200
        assert reply.json() == {"id": cmd["id"], "output": "heap 123456\n", "truncated": False}
        sent = [e for e in logs if e["event"] == "firmware.dev_cmd_sent"]
        assert sent[0]["cmd"] == "info"  # the command word, never its arguments

    async def test_timeout_is_504(self, fwdir: Path) -> None:
        async with _serving(fwdir) as (port, server):
            ws = await _walkie(port, server, _hello())
            reply = await _admin(port, "/admin/dev-cmd?line=info&timeout_s=0.2")
            await ws.close()
        assert reply.status == 504
        assert reply.json()["error"] == "no reply from the walkie"

    async def test_no_walkie_is_409(self, fwdir: Path) -> None:
        async with _serving(fwdir) as (port, server):
            assert (await _admin(port, "/admin/dev-cmd?line=info")).status == 409
            # A browser is not the walkie.
            async with connect(f"ws://127.0.0.1:{port}/") as ws:
                await _recv_type(ws, "hello")
                await _send_event(ws, "device", {"shell": "browser"})
                await until(lambda: server._conn is not None and server._conn.shell == "browser")
                assert (await _admin(port, "/admin/dev-cmd?line=info")).status == 409

    async def test_walkie_without_the_token_on_its_socket_is_409(self, fwdir: Path) -> None:
        """Anyone can claim to be firmware; only a socket opened with the
        device token gets console lines."""
        async with _serving(fwdir) as (port, server):
            ws = await _walkie(port, server, _hello(), token=False)
            with structlog.testing.capture_logs() as logs:
                reply = await _admin(port, "/admin/dev-cmd?line=info")
            await _no_event(ws, "dev.cmd", wait_s=0.1)
            await ws.close()
        assert reply.status == 409
        refused = [e for e in logs if e["event"] == "firmware.dev_cmd_refused"]
        assert refused[0]["reason"] == "not_authenticated"

    async def test_shutdown_does_not_wait_for_a_pending_dev_cmd(self, fwdir: Path) -> None:
        port = free_port()
        server = AudioServer(
            host="127.0.0.1",
            port=port,
            on_wake_word=AsyncMock(),
            on_ptt_start=AsyncMock(),
            on_ptt_stop=AsyncMock(),
            on_audio_frame=AsyncMock(),
            on_reset=AsyncMock(),
            firmware_dir=fwdir,
            device_token=SecretStr(TOKEN),
        )
        task = asyncio.create_task(server.run())
        await wait_for_http(port, "/")
        ws = await _walkie(port, server, _hello())
        pending = asyncio.create_task(
            _admin(port, "/admin/dev-cmd?line=info&timeout_s=30", timeout_s=10)
        )
        await _recv_event(ws, "dev.cmd")
        started = asyncio.get_running_loop().time()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        elapsed = asyncio.get_running_loop().time() - started
        reply = await pending
        assert elapsed < 5
        assert reply.status == 503

    async def test_walkie_without_the_cap_is_409(self, fwdir: Path) -> None:
        async with _serving(fwdir) as (port, server):
            ws = await _walkie(port, server, _hello(caps=["ota.v1"]))
            reply = await _admin(port, "/admin/dev-cmd?line=info")
            await ws.close()
        assert reply.status == 409

    @pytest.mark.parametrize(
        "query",
        [
            "",
            "line=",
            "line=%20",
            "line=info%0Areboot",
            "line=info&timeout_s=0",
            "line=info&timeout_s=31",
            "line=info&timeout_s=soon",
            "line=" + "x" * 300,
        ],
    )
    async def test_bad_params_are_400(self, fwdir: Path, query: str) -> None:
        async with _serving(fwdir) as (port, server):
            ws = await _walkie(port, server, _hello())
            reply = await _admin(port, f"/admin/dev-cmd?{query}")
            await ws.close()
        assert reply.status == 400


class TestOffer:
    async def test_sent_now_to_the_connected_walkie(self, fwdir: Path) -> None:
        sha = make_image(fwdir, "v2")
        async with _serving(fwdir) as (port, server):
            ws = await _walkie(port, server, _hello(version="v1"))
            reply = await _admin(port, "/admin/firmware-offer?version=v2")
            update = await _recv_event(ws, "firmware.update")
            await ws.close()
        assert reply.status == 200
        assert reply.json() == {
            "offer": {"version": "v2", "force": False, "previous": None},
            "image": {"size": 4096, "elf_sha": sha},
            "sent": True,
            "reason": "sent",
        }
        assert update == {"version": "v2", "url": "/firmware/v2.bin", "force": False}
        assert json.loads((fwdir / "offer.json").read_text())["version"] == "v2"

    async def test_sent_on_the_next_hello(self, fwdir: Path) -> None:
        make_image(fwdir, "v2")
        async with _serving(fwdir) as (port, server):
            reply = await _admin(port, "/admin/firmware-offer?version=v2&force=1")
            assert reply.json()["sent"] is False
            assert reply.json()["reason"] == "no_device_connected"
            ws = await connect(f"ws://127.0.0.1:{port}/")
            await _recv_type(ws, "hello")
            await _send_event(ws, "device.hello", _hello(version="v1"))
            update = await _recv_event(ws, "firmware.update")
            await ws.close()
        assert update == {"version": "v2", "url": "/firmware/v2.bin", "force": True}

    async def test_not_sent_when_the_walkie_runs_it(self, fwdir: Path) -> None:
        sha = make_image(fwdir, "v2")
        async with _serving(fwdir) as (port, server):
            ws = await _walkie(port, server, _hello(version="v2", elf_sha=sha))
            reply = await _admin(port, "/admin/firmware-offer?version=v2")
            await _no_event(ws, "firmware.update")
            await ws.close()
        assert reply.json()["sent"] is False
        assert reply.json()["reason"] == "same_version"

    @pytest.mark.parametrize(
        ("query", "status"),
        [
            ("version=v9", 404),
            ("version=..%2Fv2", 400),
            ("version=v2&force=maybe", 400),
            ("", 400),
        ],
    )
    async def test_refused(self, fwdir: Path, query: str, status: int) -> None:
        make_image(fwdir, "v2")
        async with _serving(fwdir) as (port, _server):
            reply = await _admin(port, f"/admin/firmware-offer?{query}")
        assert reply.status == status
        assert not (fwdir / "offer.json").exists()

    async def test_status_shows_offer_files_and_last_hello(self, fwdir: Path) -> None:
        sha = make_image(fwdir, "v2", size=5000)
        async with _serving(fwdir) as (port, server):
            ws = await _walkie(port, server, _hello(version="v1"))
            await _admin(port, "/admin/firmware-offer?version=v2")
            status = (await _admin(port, "/admin/firmware")).json()
            await ws.close()
        assert status["offer"] == {"version": "v2", "force": False, "previous": None}
        assert status["manifest"] == {"version": "v2", "size": 5000, "elf_sha": sha}
        assert [f["name"] for f in status["files"]] == ["offer.json", "v2.bin", "v2.elf"]
        walkie = status["walkie"]
        assert walkie["connected"] is True
        assert (walkie["version"], walkie["slot"], walkie["slot_state"]) == (
            "v1",
            "ota_0",
            "valid",
        )
        assert walkie["rollback"] is None
        assert status["pending"] is None


class TestAcceptFlow:
    async def test_full_flow(self, fwdir: Path, quick_close: None) -> None:
        sha = make_image(fwdir, "v2")
        fw.FirmwareStore(fwdir).write_offer("v2", force=False)
        pending = _hello(version="v2", elf_sha=sha, slot="ota_1", slot_state="pending_verify")
        async with _serving(fwdir) as (port, server):
            with structlog.testing.capture_logs() as logs:
                ws1 = await _walkie(port, server, pending)
                await _send_event(
                    ws1, "firmware.status", {"version": "v2", "phase": "selftest", "error": None}
                )
                manifest = await http_get(port, "/firmware/manifest.json", headers=AUTH)
                code = await _closed_code(ws1)
                ws2 = await _walkie(port, server, {**pending, "uptime_s": 31})
                accept = await _recv_event(ws2, "ota.accept")
                await _send_event(
                    ws2, "firmware.status", {"version": "v2", "phase": "accepted", "error": None}
                )
                await until(
                    lambda: any(
                        e["event"] == "firmware.status" and e["phase"] == "accepted" for e in logs
                    )
                )
                await ws2.close()
        assert manifest.json()["elf_sha"] == sha
        assert code == 1012
        assert accept == {"version": "v2"}
        events = [e["event"] for e in logs]
        assert events.index("firmware.pending_boot") < events.index("firmware.reconnect_forced")
        assert events.index("firmware.reconnect_forced") < events.index("firmware.accept_sent")
        withheld = [e for e in logs if e["event"] == "firmware.accept_withheld"]
        assert [e["reason"] for e in withheld] == ["selftest_pending"]
        # The forced close is in the connection's own record.
        disconnected = [e for e in logs if e["event"] == "client_disconnected"]
        assert disconnected[0]["close_code"] == 1012
        # A pending image is never offered an update.
        assert "firmware.update_sent" not in events

    async def test_selftest_failed(self, fwdir: Path, quick_close: None) -> None:
        sha = make_image(fwdir, "v2")
        fw.FirmwareStore(fwdir).write_offer("v2", force=False)
        pending = _hello(version="v2", elf_sha=sha, slot_state="pending_verify")
        async with _serving(fwdir) as (port, server):
            with structlog.testing.capture_logs() as logs:
                ws1 = await _walkie(port, server, pending)
                await _send_event(
                    ws1,
                    "firmware.status",
                    {"version": "v2", "phase": "selftest", "error": "codec_es8311"},
                )
                assert (
                    await http_get(port, "/firmware/manifest.json", headers=AUTH)
                ).status == 200
                await _no_event(ws1, "ota.accept", wait_s=0.3)  # and no close either
                assert ws1.close_code is None
                await ws1.close()
                ws2 = await _walkie(port, server, {**pending, "uptime_s": 40})
                await _no_event(ws2, "ota.accept")
                await ws2.close()
        withheld = [e for e in logs if e["event"] == "firmware.accept_withheld"]
        assert {e["reason"] for e in withheld} >= {"selftest_failed"}
        assert withheld[-1]["reason"] == "selftest_failed"
        assert withheld[-1]["selftest_error"] == "codec_es8311"
        assert "firmware.reconnect_forced" not in [e["event"] for e in logs]

    async def test_no_manifest_get(self, fwdir: Path, quick_close: None) -> None:
        sha = make_image(fwdir, "v2")
        fw.FirmwareStore(fwdir).write_offer("v2", force=False)
        pending = _hello(version="v2", elf_sha=sha, slot_state="pending_verify")
        async with _serving(fwdir) as (port, server):
            with structlog.testing.capture_logs() as logs:
                ws1 = await _walkie(port, server, pending)
                await _send_event(
                    ws1, "firmware.status", {"version": "v2", "phase": "selftest", "error": None}
                )
                await _no_event(ws1, "ota.accept", wait_s=0.3)
                assert ws1.close_code is None
                await ws1.close()  # the walkie reconnects on its own
                ws2 = await _walkie(port, server, {**pending, "uptime_s": 40})
                await _no_event(ws2, "ota.accept")
                await ws2.close()
        withheld = [e for e in logs if e["event"] == "firmware.accept_withheld"]
        assert withheld[-1]["reason"] == "no_manifest_get"


class TestRequestHygiene:
    async def test_non_ascii_authorization_is_401_not_500(self, fwdir: Path) -> None:
        async with _serving(fwdir) as (port, _server):
            with structlog.testing.capture_logs() as logs:
                reply = await http_get(
                    port, "/firmware/manifest.json", headers={"Authorization": "Bearer tökén"}
                )
        assert reply.status == 401
        assert "firmware.http_failed" not in [e["event"] for e in logs]

    @pytest.mark.parametrize(
        "target", ["//x/admin/firmware", "http://127.0.0.1/admin/firmware", "//x/health"]
    )
    async def test_only_origin_form_targets_reach_the_routes(
        self, fwdir: Path, target: str
    ) -> None:
        """`//x/admin` parses to `/admin` and would slip past a tunnel's
        `^/admin` rule."""
        async with _serving(fwdir) as (port, _server):
            reply = await http_get(port, target, headers={**ADMIN, **AUTH})
        assert reply.status != 200


class TestVoiceSocketUnaffected:
    async def test_handshake_health_and_other_paths(self, fwdir: Path) -> None:
        async with _serving(fwdir) as (port, _server):
            assert (await http_get(port, "/health")).status == 200
            assert (await http_get(port, "/status")).status == 426
            assert (await http_get(port, "/firmwarex")).status == 426
            assert (await http_get(port, "/adminx")).status == 426
            async with connect(f"ws://127.0.0.1:{port}/?lang=es") as ws:
                hello = await _recv_type(ws, "hello")
            assert hello["language"] == "es"

    async def test_routes_do_not_touch_the_pending_query(self, fwdir: Path) -> None:
        async with _serving(fwdir) as (port, server):
            server._pending_language = "en"
            await http_get(port, "/firmware/manifest.json?lang=fr", headers=AUTH)
            await _admin(port, "/admin/firmware?lang=fr")
            assert server._pending_language == "en"

    @pytest.mark.parametrize("path", ["/firmware/manifest.json", "/admin/firmware"])
    async def test_not_served_without_a_firmware_dir(self, path: str) -> None:
        async with _serving(None) as (port, _server):
            reply = await http_get(port, path, headers={**AUTH, **ADMIN})
        assert reply.status == 426

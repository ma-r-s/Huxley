"""`huxley.server.firmware` without a socket: the firmware directory, the
offer policy and the accept flow's decisions, driven through a fake link.

The wire (real HTTP and a real WebSocket walkie) is in
test_server_firmware.py.
"""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest
import structlog
from pydantic import SecretStr

from huxley.server import firmware as fw
from huxley.server.firmware import (
    FirmwareError,
    FirmwareService,
    FirmwareStore,
    admin_host_allowed,
    read_image_info,
    valid_version,
)
from tests.unit.firmware_images import make_image
from tests.unit.http_probe import until

if TYPE_CHECKING:
    from pathlib import Path

TOKEN = "unit-test-token-0123456789"


class FakeLink:
    """The voice server as the firmware service sees it."""

    def __init__(self) -> None:
        self.conn_id: str | None = None
        self.sent: list[tuple[str, str, dict[str, Any]]] = []
        self.closed: list[tuple[str, int]] = []
        self.busy = False
        self.token_ok = True

    def device_conn_id(self) -> str | None:
        return self.conn_id

    def device_token_presented(self, conn_id: str) -> bool:
        return self.token_ok and conn_id == self.conn_id

    def device_busy(self) -> bool:
        return self.busy

    async def send_device_event(self, conn_id: str, event: str, data: dict[str, Any]) -> bool:
        if conn_id != self.conn_id:
            return False
        self.sent.append((conn_id, event, data))
        return True

    async def close_device(self, conn_id: str, code: int, reason: str) -> bool:
        del reason
        if conn_id != self.conn_id:
            return False
        self.closed.append((conn_id, code))
        self.conn_id = None
        return True

    def events(self, name: str) -> list[dict[str, Any]]:
        return [data for _, event, data in self.sent if event == name]


class Headers:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = {k.lower(): [v] for k, v in (values or {}).items()}

    def get_all(self, key: str) -> list[str]:
        return list(self._values.get(key.lower(), []))


AUTH = Headers({"Authorization": f"Bearer {TOKEN}"})
ADMIN_SECRET = "admin-unit-test-secret-0123456789abc"
ADMIN = Headers({"X-Huxley-Admin": ADMIN_SECRET, "Host": "127.0.0.1:8765"})


def hello(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "device_id": "a0:f2:62:e2:fb:60",
        "version": "v1",
        "elf_sha": "111111111",
        "slot": "ota_0",
        "slot_state": "valid",
        "boot_counter": 4,
        "uptime_s": 10,
        "caps": ["ota.v1", "dev_cmd.v1"],
        "rollback": None,
    }
    base.update(fields)
    return base


@pytest.fixture
def no_close_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fw, "RECONNECT_CLOSE_DELAY_S", 0.0)


@pytest.fixture
def fwdir(tmp_path: Path) -> Path:
    return tmp_path / "firmware"


def service(fwdir: Path, link: FakeLink, token: str | None = TOKEN) -> FirmwareService:
    return FirmwareService(
        fwdir,
        SecretStr(token) if token is not None else None,
        link,
        admin_token=SecretStr(ADMIN_SECRET),
    )


class TestVersionNames:
    @pytest.mark.parametrize(
        "version",
        ["v1", "v0.4.0-23-g85d5494-dirty", "1.2.3", "build_7+meta", "a" * 64],
    )
    def test_valid(self, version: str) -> None:
        assert valid_version(version)

    @pytest.mark.parametrize(
        "version",
        [
            "",
            "..",
            "a..b",
            "../etc/passwd",
            "..%2Fetc",
            "v1/../v2",
            "v1\\v2",
            ".hidden",
            "-rf",
            "v1\n",
            "a" * 65,
            "v 1",
            None,
            7,
        ],
    )
    def test_invalid(self, version: object) -> None:
        assert not valid_version(version)


class TestAdminHost:
    @pytest.mark.parametrize(
        ("host", "allowed"),
        [
            ("127.0.0.1:8765", True),
            ("localhost:8765", True),
            ("localhost", True),
            ("[::1]:8765", True),
            ("100.75.152.70:8765", True),
            ("orange:8765", True),
            ("orange.tail1234.ts.net:8765", True),
            ("evil.example.com:8765", False),
            ("127.0.0.1.nip.io:8765", False),
            ("", False),
            (None, False),
        ],
    )
    def test_host(self, host: str | None, allowed: bool) -> None:
        assert admin_host_allowed(host) is allowed


class TestStore:
    def test_image_info_reads_the_header(self, fwdir: Path) -> None:
        sha = make_image(fwdir, "v0.4.0-3-gabc", size=5000)
        info = read_image_info(fwdir / "v0.4.0-3-gabc.bin")
        assert info.version == "v0.4.0-3-gabc"
        assert info.elf_sha == sha
        assert len(sha) == 9
        assert info.size == 5000

    def test_not_an_image(self, fwdir: Path) -> None:
        fwdir.mkdir()
        (fwdir / "v1.bin").write_bytes(b"hello" * 100)
        with pytest.raises(FirmwareError, match="not an ESP32 app image"):
            read_image_info(fwdir / "v1.bin")

    def test_offer_keeps_the_one_before(self, fwdir: Path) -> None:
        store = FirmwareStore(fwdir)
        assert store.read_offer() is None
        store.write_offer("v1", force=False)
        store.write_offer("v2", force=True)
        offer = store.read_offer()
        assert offer is not None
        assert (offer.version, offer.force, offer.previous) == ("v2", True, "v1")
        # Offering the same version again keeps what came before it.
        store.write_offer("v2", force=False)
        offer = store.read_offer()
        assert offer is not None
        assert (offer.version, offer.force, offer.previous) == ("v2", False, "v1")
        assert json.loads((fwdir / "offer.json").read_text())["version"] == "v2"

    def test_corrupt_offer_offers_nothing_and_says_so(self, fwdir: Path) -> None:
        fwdir.mkdir()
        (fwdir / "offer.json").write_text("{not json")
        with structlog.testing.capture_logs() as logs:
            assert FirmwareStore(fwdir).read_offer() is None
        assert [e["event"] for e in logs] == ["firmware.offer_unreadable"]

    def test_offer_naming_a_bad_version_is_ignored(self, fwdir: Path) -> None:
        fwdir.mkdir()
        (fwdir / "offer.json").write_text(json.dumps({"version": "../x", "force": False}))
        assert FirmwareStore(fwdir).read_offer() is None

    def test_check_offerable(self, fwdir: Path) -> None:
        store = FirmwareStore(fwdir)
        sha = make_image(fwdir, "v2")
        assert store.check_offerable("v2").elf_sha == sha

    @pytest.mark.parametrize(
        ("setup", "status", "match"),
        [
            ("missing", HTTPStatus.NOT_FOUND, "no v2.bin"),
            ("misnamed", HTTPStatus.UNPROCESSABLE_ENTITY, "says it is version 'v9'"),
            ("no_elf", HTTPStatus.UNPROCESSABLE_ENTITY, "no v2.elf"),
            ("wrong_elf", HTTPStatus.UNPROCESSABLE_ENTITY, "is not the ELF of v2.bin"),
        ],
    )
    def test_not_offerable(self, fwdir: Path, setup: str, status: HTTPStatus, match: str) -> None:
        fwdir.mkdir()
        if setup == "misnamed":
            make_image(fwdir, "v2", embedded_version="v9")
        elif setup == "no_elf":
            make_image(fwdir, "v2", write_elf=False)
        elif setup == "wrong_elf":
            make_image(fwdir, "v2")
            (fwdir / "v2.elf").write_bytes(b"\x7fELF another build")
        with pytest.raises(FirmwareError, match=match) as exc:
            FirmwareStore(fwdir).check_offerable("v2")
        assert exc.value.status == status

    def test_files(self, fwdir: Path) -> None:
        make_image(fwdir, "v1", size=1000)
        FirmwareStore(fwdir).write_offer("v1", force=False)
        names = [f["name"] for f in FirmwareStore(fwdir).files()]
        assert names == ["offer.json", "v1.bin", "v1.elf"]
        assert FirmwareStore(fwdir / "nope").files() == []


class TestToken:
    async def test_empty_token_is_no_token(self, fwdir: Path) -> None:
        svc = service(fwdir, FakeLink(), token="")
        assert svc.token_configured is False
        answer = await svc.device_http(fw.MANIFEST_PATH, AUTH, "127.0.0.1")
        assert answer.status == HTTPStatus.SERVICE_UNAVAILABLE

    @pytest.mark.parametrize(
        "header",
        [
            f"Basic {TOKEN}",
            f"Bearer{TOKEN}",
            "Bearer ",
            f"Bearer {TOKEN}x",
            f"Bearer {TOKEN[:-1]}",
        ],
    )
    async def test_refused(self, fwdir: Path, header: str) -> None:
        svc = service(fwdir, FakeLink())
        with structlog.testing.capture_logs() as logs:
            answer = await svc.device_http(
                fw.MANIFEST_PATH, Headers({"Authorization": header}), "127.0.0.1"
            )
        assert answer.status == HTTPStatus.UNAUTHORIZED
        assert ("WWW-Authenticate", "Bearer") in answer.headers
        assert [e["event"] for e in logs] == ["firmware.auth_refused"]
        assert TOKEN[:12] not in repr(logs)

    async def test_bearer_is_case_insensitive(self, fwdir: Path) -> None:
        svc = service(fwdir, FakeLink())
        answer = await svc.device_http(
            fw.MANIFEST_PATH, Headers({"Authorization": f"bearer {TOKEN}"}), "127.0.0.1"
        )
        assert answer.status == HTTPStatus.NOT_FOUND  # authorized; nothing offered


class TestOfferPolicy:
    """When `firmware.update` goes out on a hello."""

    async def _hello(self, svc: FirmwareService, link: FakeLink, **fields: Any) -> None:
        link.conn_id = fields.pop("conn_id", "c1")
        await svc.on_client_event(link.conn_id, "device.hello", hello(**fields))

    async def test_sent_when_the_device_runs_another_version(self, fwdir: Path) -> None:
        make_image(fwdir, "v2")
        FirmwareStore(fwdir).write_offer("v2", force=False)
        link = FakeLink()
        svc = service(fwdir, link)
        await self._hello(svc, link)
        assert link.events("firmware.update") == [
            {"version": "v2", "url": "/firmware/v2.bin", "force": False}
        ]

    @pytest.mark.parametrize(
        ("fields", "reason"),
        [
            ({"version": "v2"}, "same_version"),
            ({"caps": ["health.v1"]}, "no_ota_cap"),
            ({"caps": None}, "no_ota_cap"),
            ({"slot_state": "pending_verify"}, "pending_verify"),
        ],
    )
    async def test_not_sent(self, fwdir: Path, fields: dict[str, Any], reason: str) -> None:
        make_image(fwdir, "v2")
        FirmwareStore(fwdir).write_offer("v2", force=False)
        link = FakeLink()
        svc = service(fwdir, link)
        with structlog.testing.capture_logs() as logs:
            await self._hello(svc, link, **fields)
        assert link.events("firmware.update") == []
        skipped = [e for e in logs if e["event"] == "firmware.update_skipped"]
        assert skipped and skipped[0]["reason"] == reason

    async def test_not_sent_without_a_device_token(self, fwdir: Path) -> None:
        """The walkie couldn't download it (503): don't make it try."""
        make_image(fwdir, "v2")
        FirmwareStore(fwdir).write_offer("v2", force=False)
        link = FakeLink()
        svc = service(fwdir, link, token=None)
        with structlog.testing.capture_logs() as logs:
            await self._hello(svc, link)
        assert link.events("firmware.update") == []
        skipped = [e for e in logs if e["event"] == "firmware.update_skipped"]
        assert skipped[0]["reason"] == "no_device_token"

    async def test_force_compares_the_image_not_the_version_string(self, fwdir: Path) -> None:
        """Uncommitted builds share a version: force sends a different build
        of it, never the build already running."""
        sha = make_image(fwdir, "v2-dirty")
        FirmwareStore(fwdir).write_offer("v2-dirty", force=True)
        link = FakeLink()
        svc = service(fwdir, link)
        await self._hello(svc, link, version="v2-dirty", elf_sha="999999999")
        assert len(link.events("firmware.update")) == 1
        assert link.events("firmware.update")[0]["force"] is True
        link.sent.clear()
        await self._hello(svc, link, version="v2-dirty", elf_sha=sha, conn_id="c2")
        assert link.events("firmware.update") == []

    async def test_crash_rollback_is_not_offered_again(self, fwdir: Path) -> None:
        make_image(fwdir, "v2")
        FirmwareStore(fwdir).write_offer("v2", force=False)
        link = FakeLink()
        svc = service(fwdir, link)
        rollback = {"from_version": "v2", "reason": "crash"}
        with structlog.testing.capture_logs() as logs:
            await self._hello(svc, link, rollback=rollback)
        assert link.events("firmware.update") == []
        assert "firmware.update_withheld" in [e["event"] for e in logs]
        # An explicit offer from the admin route goes through.
        reply = await svc.admin_http(fw.ADMIN_OFFER_PATH, "version=v2", ADMIN)
        assert json.loads(reply.body)["sent"] is True
        assert len(link.events("firmware.update")) == 1

    async def test_no_server_rollback_gets_one_retry(self, fwdir: Path) -> None:
        """Counted when the walkie starts downloading, not when the server
        sends: it waits until idle, so a reconnect before that keeps it."""
        make_image(fwdir, "v2")
        FirmwareStore(fwdir).write_offer("v2", force=False)
        link = FakeLink()
        svc = service(fwdir, link)
        rollback = {"from_version": "v2", "reason": "no_server"}
        await self._hello(svc, link, rollback=rollback)
        await self._hello(svc, link, rollback=rollback, conn_id="c2")
        assert len(link.events("firmware.update")) == 2
        await svc.on_client_event(
            "c2", "firmware.status", {"version": "v2", "phase": "downloading", "error": None}
        )
        await self._hello(svc, link, rollback=rollback, conn_id="c3")
        assert len(link.events("firmware.update")) == 2

    async def test_admin_override_lasts_until_the_download_starts(self, fwdir: Path) -> None:
        make_image(fwdir, "v2")
        link = FakeLink()
        svc = service(fwdir, link)
        rollback = {"from_version": "v2", "reason": "crash"}
        await self._hello(svc, link, rollback=rollback)
        await svc.admin_http(fw.ADMIN_OFFER_PATH, "version=v2", ADMIN)
        await self._hello(svc, link, rollback=rollback, conn_id="c2")  # walkie was busy
        assert len(link.events("firmware.update")) == 2
        await svc.on_client_event(
            "c2", "firmware.status", {"version": "v2", "phase": "downloading", "error": None}
        )
        await self._hello(svc, link, rollback=rollback, conn_id="c3")
        assert len(link.events("firmware.update")) == 2

    async def test_repeated_failures_stop_the_offer(self, fwdir: Path) -> None:
        make_image(fwdir, "v2")
        FirmwareStore(fwdir).write_offer("v2", force=False)
        link = FakeLink()
        svc = service(fwdir, link)
        await self._hello(svc, link)
        for _ in range(fw.MAX_UPDATE_FAILURES):
            await svc.on_client_event(
                "c1", "firmware.status", {"version": "v2", "phase": "failed", "error": "http_404"}
            )
        link.sent.clear()
        with structlog.testing.capture_logs() as logs:
            await self._hello(svc, link, conn_id="c2")
        assert link.events("firmware.update") == []
        skipped = [e for e in logs if e["event"] == "firmware.update_skipped"]
        assert skipped[0]["reason"] == "failed_too_often"
        # Offering it again from the admin route starts over.
        reply = await svc.admin_http(fw.ADMIN_OFFER_PATH, "version=v2", ADMIN)
        assert json.loads(reply.body)["sent"] is True

    async def test_a_rollback_from_another_version_does_not_block(self, fwdir: Path) -> None:
        make_image(fwdir, "v3")
        FirmwareStore(fwdir).write_offer("v3", force=False)
        link = FakeLink()
        svc = service(fwdir, link)
        await self._hello(svc, link, rollback={"from_version": "v2", "reason": "crash"})
        assert len(link.events("firmware.update")) == 1


class TestAcceptDecisions:
    """The accept flow's corners, step by step (the happy path runs over a
    real socket in test_server_firmware.py)."""

    async def _setup(self, fwdir: Path) -> tuple[FirmwareService, FakeLink, str]:
        sha = make_image(fwdir, "v2")
        FirmwareStore(fwdir).write_offer("v2", force=False)
        link = FakeLink()
        return service(fwdir, link), link, sha

    async def _pending_hello(
        self, svc: FirmwareService, link: FakeLink, conn_id: str, sha: str, **fields: Any
    ) -> None:
        link.conn_id = conn_id
        fields = {"version": "v2", "elf_sha": sha, "slot_state": "pending_verify", **fields}
        await svc.on_client_event(conn_id, "device.hello", hello(**fields))

    async def _status(self, svc: FirmwareService, conn_id: str, **data: Any) -> None:
        await svc.on_client_event(conn_id, "firmware.status", data)

    async def test_order_does_not_matter(self, fwdir: Path, no_close_delay: None) -> None:
        """Manifest before self-test: the close waits for both."""
        svc, link, sha = await self._setup(fwdir)
        await self._pending_hello(svc, link, "c1", sha)
        assert (await svc.device_http(fw.MANIFEST_PATH, AUTH, "x")).status == HTTPStatus.OK
        await asyncio.sleep(0.01)
        assert link.closed == []
        await self._status(svc, "c1", version="v2", phase="selftest", error=None)
        await until(lambda: link.closed == [("c1", 1012)])
        await self._pending_hello(svc, link, "c2", sha, uptime_s=30)
        assert link.events("ota.accept") == [{"version": "v2"}]

    async def test_a_new_boot_starts_over(self, fwdir: Path, no_close_delay: None) -> None:
        svc, link, sha = await self._setup(fwdir)
        await self._pending_hello(svc, link, "c1", sha)
        await self._status(svc, "c1", version="v2", phase="selftest", error=None)
        await svc.device_http(fw.MANIFEST_PATH, AUTH, "x")
        await until(lambda: link.closed != [])
        # Rebooted (boot counter moved): what the last boot proved is gone.
        with structlog.testing.capture_logs() as logs:
            await self._pending_hello(svc, link, "c2", sha, boot_counter=5)
        assert link.events("ota.accept") == []
        withheld = [e for e in logs if e["event"] == "firmware.accept_withheld"]
        assert withheld[0]["reason"] == "selftest_pending"
        assert "firmware.pending_boot" in [e["event"] for e in logs]

    async def test_uptime_going_back_is_a_new_boot(self, fwdir: Path) -> None:
        svc, link, sha = await self._setup(fwdir)
        await self._pending_hello(svc, link, "c1", sha, uptime_s=50, boot_counter=None)
        await self._status(svc, "c1", version="v2", phase="selftest", error=None)
        with structlog.testing.capture_logs() as logs:
            await self._pending_hello(svc, link, "c2", sha, uptime_s=3, boot_counter=None)
        assert "firmware.pending_boot" in [e["event"] for e in logs]

    async def test_manifest_for_another_version(self, fwdir: Path, no_close_delay: None) -> None:
        svc, link, sha = await self._setup(fwdir)
        make_image(fwdir, "v3")
        FirmwareStore(fwdir).write_offer("v3", force=False)
        await self._pending_hello(svc, link, "c1", sha)
        await self._status(svc, "c1", version="v2", phase="selftest", error=None)
        await svc.device_http(fw.MANIFEST_PATH, AUTH, "x")
        await asyncio.sleep(0.01)
        assert link.closed == []
        with structlog.testing.capture_logs() as logs:
            await self._pending_hello(svc, link, "c2", sha, uptime_s=30)
        assert link.events("ota.accept") == []
        withheld = [e for e in logs if e["event"] == "firmware.accept_withheld"]
        assert withheld[0]["reason"] == "manifest_version_mismatch"

    async def test_elf_sha_mismatch(self, fwdir: Path, no_close_delay: None) -> None:
        svc, link, _sha = await self._setup(fwdir)
        await self._pending_hello(svc, link, "c1", "abcdef012")
        await self._status(svc, "c1", version="v2", phase="selftest", error=None)
        await svc.device_http(fw.MANIFEST_PATH, AUTH, "x")
        with structlog.testing.capture_logs() as logs:
            await self._pending_hello(svc, link, "c2", "abcdef012", uptime_s=30)
        assert link.events("ota.accept") == []
        withheld = [e for e in logs if e["event"] == "firmware.accept_withheld"]
        assert withheld[0]["reason"] == "elf_sha_mismatch"

    async def test_selftest_for_another_version_is_ignored(self, fwdir: Path) -> None:
        svc, link, sha = await self._setup(fwdir)
        await self._pending_hello(svc, link, "c1", sha)
        with structlog.testing.capture_logs() as logs:
            await self._status(svc, "c1", version="v1", phase="selftest", error=None)
        assert "firmware.status_ignored" in [e["event"] for e in logs]
        assert svc._current_device() is not None
        pending = svc._current_device().pending  # type: ignore[union-attr]
        assert pending is not None and pending.selftest == "unknown"

    async def test_gone_before_the_close_closes_the_next_connection(
        self, fwdir: Path, no_close_delay: None
    ) -> None:
        """The device reconnected on its own before the server's close: no
        accept for that (it never saw a server close); the new connection is
        closed instead, and the one after is accepted."""
        svc, link, sha = await self._setup(fwdir)
        await self._pending_hello(svc, link, "c1", sha)
        await self._status(svc, "c1", version="v2", phase="selftest", error=None)
        link.conn_id = None  # c1 dropped before the close task ran
        await svc.device_http(fw.MANIFEST_PATH, AUTH, "x")
        await asyncio.sleep(0.01)
        assert link.closed == []
        with structlog.testing.capture_logs() as logs:
            await self._pending_hello(svc, link, "c2", sha, uptime_s=30)
        assert link.events("ota.accept") == []
        assert [e["reason"] for e in logs if e["event"] == "firmware.accept_withheld"] == [
            "awaiting_reconnect"
        ]
        await until(lambda: link.closed == [("c2", 1012)])
        await self._pending_hello(svc, link, "c3", sha, uptime_s=40)
        assert link.events("ota.accept") == [{"version": "v2"}]

    async def test_the_close_waits_for_a_turn_to_end(
        self, fwdir: Path, no_close_delay: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(fw, "RECONNECT_BUSY_POLL_S", 0.01)
        svc, link, sha = await self._setup(fwdir)
        await self._pending_hello(svc, link, "c1", sha)
        link.busy = True  # grandpa pressed right after the reboot
        with structlog.testing.capture_logs() as logs:
            await self._status(svc, "c1", version="v2", phase="selftest", error=None)
            await svc.device_http(fw.MANIFEST_PATH, AUTH, "x")
            await asyncio.sleep(0.1)
            assert link.closed == []
            link.busy = False
            await until(lambda: link.closed == [("c1", 1012)])
        assert "firmware.reconnect_waiting" in [e["event"] for e in logs]

    async def test_valid_hello_ends_the_pending_boot(self, fwdir: Path) -> None:
        svc, link, sha = await self._setup(fwdir)
        await self._pending_hello(svc, link, "c1", sha)
        with structlog.testing.capture_logs() as logs:
            await self._pending_hello(svc, link, "c2", sha, slot_state="valid")
        assert "firmware.pending_ended" in [e["event"] for e in logs]
        device = svc._current_device()
        assert device is not None and device.pending is None

    async def test_every_status_phase_is_logged(self, fwdir: Path) -> None:
        svc, link, sha = await self._setup(fwdir)
        await self._pending_hello(svc, link, "c1", sha)
        with structlog.testing.capture_logs() as logs:
            for phase, error in [
                ("downloading", None),
                ("verifying", None),
                ("rebooting", None),
                ("failed", "http_404"),
                ("selftest", None),
                ("accepted", None),
            ]:
                await self._status(svc, "c1", version="v2", phase=phase, error=error)
        status = [e for e in logs if e["event"] == "firmware.status"]
        assert [e["phase"] for e in status] == [
            "downloading",
            "verifying",
            "rebooting",
            "failed",
            "selftest",
            "accepted",
        ]
        assert [e["log_level"] for e in status][3] == "warning"


class TestDevCmdResults:
    async def test_late_or_unknown_results_are_logged(self, fwdir: Path) -> None:
        svc = service(fwdir, FakeLink())
        with structlog.testing.capture_logs() as logs:
            await svc.on_client_event("c1", "dev.cmd_result", {"id": "nope", "output": "x"})
        assert [e["event"] for e in logs] == ["firmware.dev_cmd_result_unmatched"]

    async def test_never_to_a_connection_without_the_token(self, fwdir: Path) -> None:
        """Anyone can say device.hello; console lines can carry secrets."""
        link = FakeLink()
        link.token_ok = False
        svc = service(fwdir, link)
        link.conn_id = "c1"
        await svc.on_client_event("c1", "device.hello", hello())
        answer = await svc.admin_http(fw.ADMIN_DEV_CMD_PATH, "line=info", ADMIN)
        assert answer.status == HTTPStatus.CONFLICT
        assert link.events("dev.cmd") == []

    async def test_shutdown_ends_the_wait(self, fwdir: Path) -> None:
        link = FakeLink()
        svc = service(fwdir, link)
        link.conn_id = "c1"
        await svc.on_client_event("c1", "device.hello", hello())
        waiting = asyncio.create_task(
            svc.admin_http(fw.ADMIN_DEV_CMD_PATH, "line=info&timeout_s=30", ADMIN)
        )
        await until(lambda: link.events("dev.cmd") != [])
        svc.close()
        answer = await asyncio.wait_for(waiting, 1)
        assert answer.status == HTTPStatus.SERVICE_UNAVAILABLE


class TestBoundedState:
    async def test_made_up_hellos_cannot_grow_memory(self, fwdir: Path) -> None:
        link = FakeLink()
        svc = service(fwdir, link)
        link.conn_id = "c1"
        for i in range(50):
            await svc.on_client_event(
                "c1",
                "device.hello",
                hello(device_id=f"id-{i}", junk="x" * 100_000, version="v" * 5000),
            )
        assert len(svc._devices) == 4
        device = svc._current_device()
        assert device is not None and device.device_id == "id-49"
        assert "junk" not in device.hello
        assert len(device.hello["version"]) <= 64

    def test_token_match_never_raises(self, fwdir: Path) -> None:
        svc = service(fwdir, FakeLink())
        assert svc.token_matches(Headers({"Authorization": f"Bearer {TOKEN}"}))
        assert not svc.token_matches(Headers({"Authorization": "Bearer t\udcc3ken"}))
        assert not svc.token_matches(Headers({"Authorization": "Bearer t\u00f6k\u00e9n"}))
        assert not svc.token_matches(Headers())

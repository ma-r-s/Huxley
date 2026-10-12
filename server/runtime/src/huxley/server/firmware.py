"""Firmware updates and the remote console, server side (T1.43 W4).

The hardware client (a `device.hello` sender, `shell: firmware`) updates
itself by pulling from this server, and Mario reaches its console through
it. Three pieces, all framework plumbing (no skill sees any of it):

- **Images** (`GET /firmware/manifest.json`, `GET /firmware/<version>.bin`),
  gated by the device token (`HUXLEY_DEVICE_TOKEN`), never by address: the
  device comes through the Cloudflare tunnel, where every request arrives
  from loopback.
- **Admin** (`GET /admin/firmware`, `/admin/firmware-offer`,
  `/admin/dev-cmd`), loopback and Tailscale only. The server checks the
  source (server.py); this module checks the request itself
  (`X-Huxley-Admin`, a local `Host`) so a web page in Mario's browser can't
  fire one. GET with query parameters, not POST: websockets answers 400 to
  any other method or any request body before `process_request` runs.
- **The accept flow**: a freshly updated image boots `pending_verify` and
  is kept only after the server's `ota.accept`, which the server sends
  once the image passed its self-test, fetched the manifest, and came back
  after a server-initiated close (code 1012). In memory: a server restart
  midway just means the pending image asks again.

Wire contract: docs/protocol.md "Device contract (schema 1)" and
"HTTP: /firmware and /admin". Log events: docs/observability.md
"Firmware updates and the remote console".
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import parse_qs

import structlog

from huxley.server.auth import (
    HeaderLookup,
    bearer_token_state,
    configured_token,
    forwarded_for,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import SecretStr

logger = structlog.get_logger()

FIRMWARE_PREFIX = "/firmware/"
MANIFEST_PATH = "/firmware/manifest.json"
ADMIN_PREFIX = "/admin/"
ADMIN_STATUS_PATH = "/admin/firmware"
ADMIN_OFFER_PATH = "/admin/firmware-offer"
ADMIN_DEV_CMD_PATH = "/admin/dev-cmd"
ADMIN_HEADER = "X-Huxley-Admin"
OFFER_FILE = "offer.json"

# The forced reconnect of the accept flow. 1012 ("service restart")
# reconnects normally; only 1001 with the eviction reason pauses the device.
RECONNECT_CLOSE_CODE = 1012
RECONNECT_CLOSE_REASON = "firmware: reconnect to finish the update"
# The close waits this long, so the manifest's HTTP answer is out before it.
RECONNECT_CLOSE_DELAY_S = 2.0
# ...and, after that, until no turn or call is in progress (polled).
RECONNECT_BUSY_POLL_S = 1.0

# A device that reported `failed` this many times for one version is not
# offered it again automatically (an admin offer resets the count).
MAX_UPDATE_FAILURES = 3
# Devices remembered (the twin board and the walkie; a flood of made-up
# hellos can't grow it), and what of each hello is kept.
_MAX_DEVICES = 4
_HELLO_KEYS = (
    "device_id",
    "version",
    "elf_sha",
    "slot",
    "slot_state",
    "boot_counter",
    "uptime_s",
    "caps",
    "rollback",
)
# A token shorter than this is reported at startup (`firmware.device_token_short`).
MIN_TOKEN_CHARS = 32

# An image answer is written in one piece inside websockets' handshake
# (process_request can't stream). Its connection gets no close timeout, so
# the only limit is the handshake's (60 s from the TCP accept, server.py):
# the default 10 s close deadline is set before the drain and would cut a
# slow download's tail.
# dev-cmd waits inside the same handshake limit.
DEV_CMD_DEFAULT_TIMEOUT_S = 10.0
DEV_CMD_MAX_TIMEOUT_S = 30.0
DEV_CMD_MAX_LINE = 256

# A version names files: letters, digits and `._+-`, starting with a letter
# or digit, no `..` (git describe output fits). No `/`, `\` or `%`, so a
# version can never leave the firmware directory, encoded or not.
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")

# ESP32 app image layout (ESP-IDF): image magic at 0, esp_app_desc_t at
# 0x20 (its magic word 0xABCD5432, little-endian), the version string at
# 0x30, the ELF's SHA-256 at 0xB0 (esptool fills it: `--elf-sha256-offset
# 0xb0`). The device reports the first 9 hex digits of that hash as
# `elf_sha` (esp_app_get_elf_sha256, CONFIG_APP_RETRIEVE_LEN_ELF_SHA).
_IMAGE_MAGIC = 0xE9
_APP_DESC_MAGIC = bytes.fromhex("3254cdab")
_VERSION_SPAN = slice(0x30, 0x50)
_ELF_SHA_SPAN = slice(0xB0, 0xB5)
_HEADER_LEN = 0xD0
ELF_SHA_HEX = 9
# Bigger than any OTA slot the walkie will ever have.
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat(timespec="seconds")


def valid_version(version: object) -> bool:
    """True when `version` may name a firmware file."""
    return (
        isinstance(version, str)
        and _VERSION_RE.fullmatch(version) is not None
        and ".." not in version
    )


def is_firmware_path(path: str) -> bool:
    """The paths this module answers (everything else keeps its handling)."""
    return path.startswith((FIRMWARE_PREFIX, ADMIN_PREFIX))


class DeviceLink(Protocol):
    """What this module needs from the voice server: the connected device
    (the current client, when it is a firmware client) and two ways to
    reach it. Implemented by AudioServer."""

    def device_conn_id(self) -> str | None: ...

    def device_token_presented(self, conn_id: str) -> bool:
        """Whether connection `conn_id` opened its WebSocket with the
        device token (`Authorization: Bearer` on the upgrade)."""
        ...

    def device_busy(self) -> bool:
        """A turn or a call is in progress (a press, the model speaking, a
        skill holding the mic): not a moment to drop the connection."""
        ...

    async def send_device_event(self, conn_id: str, event: str, data: dict[str, Any]) -> bool: ...

    async def close_device(self, conn_id: str, code: int, reason: str) -> bool: ...


@dataclass(frozen=True)
class HttpAnswer:
    """A plain HTTP answer, independent of the transport (server.py turns
    it into a websockets Response)."""

    status: HTTPStatus
    body: bytes
    content_type: str = "application/json"
    headers: tuple[tuple[str, str], ...] = ()
    # A firmware image: no close deadline on its connection, so the drain is
    # bounded by the handshake limit alone (see above).
    long_send: bool = False


class _ShuttingDownError(Exception):
    """The server is stopping: pending dev-cmd waits end at once."""


def _json_answer(status: HTTPStatus, payload: dict[str, Any]) -> HttpAnswer:
    return HttpAnswer(status, (json.dumps(payload) + "\n").encode())


def _error(status: HTTPStatus, message: str, **extra: Any) -> HttpAnswer:
    return _json_answer(status, {"error": message, **extra})


class FirmwareError(Exception):
    """An image that can't be offered or served. The message goes in the
    reply and the log; `status` is the HTTP status for an admin call."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.UNPROCESSABLE_ENTITY) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ImageInfo:
    """What an app image says about itself."""

    version: str
    elf_sha: str
    size: int


def read_image_info(path: Path) -> ImageInfo:
    """Version, elf_sha and size of the app image at `path` (reads its
    header only)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(_HEADER_LEN)
    except FileNotFoundError:
        raise FirmwareError(f"no {path.name}", HTTPStatus.NOT_FOUND) from None
    except OSError as exc:
        raise FirmwareError(f"{path.name}: {exc.strerror}") from None
    if len(head) < _HEADER_LEN or head[0] != _IMAGE_MAGIC or head[0x20:0x24] != _APP_DESC_MAGIC:
        raise FirmwareError(f"{path.name} is not an ESP32 app image")
    version = head[_VERSION_SPAN].split(b"\0", 1)[0].decode("ascii", "replace")
    return ImageInfo(version=version, elf_sha=head[_ELF_SHA_SPAN].hex()[:ELF_SHA_HEX], size=size)


def elf_sha9(path: Path) -> str:
    """The first 9 hex digits of an ELF file's SHA-256 (what the image
    embeds and the device reports)."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:ELF_SHA_HEX]


@dataclass(frozen=True)
class Offer:
    """`offer.json`: the one version this server offers, and the one it
    offered before (still downloadable while a device moves over)."""

    version: str
    force: bool
    previous: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"version": self.version, "force": self.force, "previous": self.previous}


class FirmwareStore:
    """The firmware directory: `<version>.bin`, `<version>.elf`, `offer.json`."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def image_path(self, version: str) -> Path:
        return self.directory / f"{version}.bin"

    def read_offer(self) -> Offer | None:
        """The current offer, None when nothing is offered. An unreadable
        offer.json offers nothing and says so in the log."""
        path = self.directory / OFFER_FILE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.error("firmware.offer_unreadable", path=str(path), error=str(exc))
            return None
        version = raw.get("version") if isinstance(raw, dict) else None
        previous = raw.get("previous") if isinstance(raw, dict) else None
        if not valid_version(version) or not (previous is None or valid_version(previous)):
            logger.error("firmware.offer_unreadable", path=str(path), error="bad version")
            return None
        assert isinstance(raw, dict)
        return Offer(version=str(version), force=raw.get("force") is True, previous=previous)

    def write_offer(self, version: str, force: bool) -> Offer:
        """Offer `version` (atomically: a crash never leaves half a file).
        The version offered until now becomes `previous`."""
        old = self.read_offer()
        if old is None:
            previous = None
        elif old.version != version:
            previous = old.version
        else:
            previous = old.previous
        offer = Offer(version=version, force=force, previous=previous)
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.directory / f".{OFFER_FILE}.tmp"
        tmp.write_text(json.dumps(offer.to_json()) + "\n", encoding="utf-8")
        tmp.replace(self.directory / OFFER_FILE)
        return offer

    def check_offerable(self, version: str) -> ImageInfo:
        """The image for `version`, if it may be offered: an app image that
        says it is `version` (else the device would report another version
        and be offered the update forever), not oversized, and its ELF next
        to it with the matching hash (core dumps need it)."""
        info = read_image_info(self.image_path(version))
        if info.version != version:
            raise FirmwareError(
                f"{version}.bin says it is version {info.version!r}; name it after its version"
            )
        if info.size > _MAX_IMAGE_BYTES:
            raise FirmwareError(f"{version}.bin is {info.size} bytes, more than any OTA slot")
        elf = self.directory / f"{version}.elf"
        try:
            sha = elf_sha9(elf)
        except FileNotFoundError:
            raise FirmwareError(f"no {version}.elf: copy it next to the .bin") from None
        except OSError as exc:
            raise FirmwareError(f"{version}.elf: {exc.strerror}") from None
        if sha != info.elf_sha:
            raise FirmwareError(
                f"{version}.elf (sha {sha}) is not the ELF of {version}.bin (sha {info.elf_sha})"
            )
        return info

    def files(self) -> list[dict[str, Any]]:
        """Every regular file in the directory, by name."""
        try:
            entries = sorted(self.directory.iterdir())
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for entry in entries:
            with contextlib.suppress(OSError):
                if entry.is_file() and not entry.name.startswith("."):
                    out.append({"name": entry.name, "bytes": entry.stat().st_size})
        return out


@dataclass
class _PendingBoot:
    """One boot of a `pending_verify` image, from its first hello until it
    reports `valid` or reboots. What the accept needs, gathered as it comes."""

    version: str
    elf_sha: str | None
    boot_counter: int | None
    last_uptime_s: float | None
    selftest: str = "unknown"  # unknown | passed | failed
    selftest_error: str | None = None
    # (version, elf_sha) of the last manifest served during this boot.
    manifest: tuple[str, str] | None = None
    # The connection the forced reconnect closes (scheduled or done).
    closed_conn_id: str | None = None
    close_done: bool = False
    accept_sent: bool = False

    @classmethod
    def from_hello(cls, hello: dict[str, Any]) -> _PendingBoot:
        sha = hello.get("elf_sha")
        return cls(
            version=str(hello.get("version")),
            elf_sha=sha if isinstance(sha, str) else None,
            boot_counter=_as_int(hello.get("boot_counter")),
            last_uptime_s=_as_float(hello.get("uptime_s")),
        )

    def same_boot(self, hello: dict[str, Any]) -> bool:
        """True when `hello` comes from this same boot (a reconnect)."""
        if str(hello.get("version")) != self.version:
            return False
        counter = _as_int(hello.get("boot_counter"))
        if counter is not None and self.boot_counter is not None and counter != self.boot_counter:
            return False
        uptime = _as_float(hello.get("uptime_s"))
        return not (
            uptime is not None and self.last_uptime_s is not None and uptime < self.last_uptime_s
        )

    def blocker(self) -> str | None:
        """Why the accept can't be sent yet; None when the image earned it."""
        if self.selftest == "failed":
            return "selftest_failed"
        if self.selftest != "passed":
            return "selftest_pending"
        if self.manifest is None:
            return "no_manifest_get"
        if self.manifest[0] != self.version:
            return "manifest_version_mismatch"
        if self.elf_sha is not None and self.manifest[1] != self.elf_sha:
            return "elf_sha_mismatch"
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "selftest": self.selftest,
            "selftest_error": self.selftest_error,
            "manifest_seen": self.manifest is not None,
            "reconnect_forced": self.close_done,
            "accept_sent": self.accept_sent,
            "waiting_for": self.blocker() or ("accept" if self.close_done else "reconnect"),
        }


@dataclass
class _Device:
    device_id: str
    hello: dict[str, Any]
    conn_id: str  # the connection its last hello came on
    hello_at_ms: int
    pending: _PendingBoot | None = None
    # Automatic retries of an offer this device rolled back from with
    # reason no_server (one allowed per server run), counted when the
    # device starts downloading it.
    no_server_retries: dict[str, int] = field(default_factory=dict)
    # `firmware.status failed` reports per version (MAX_UPDATE_FAILURES).
    failures: dict[str, int] = field(default_factory=dict)


def _trim_hello(hello: dict[str, Any]) -> dict[str, Any]:
    """The part of a `device.hello` this module uses, bounded: the voice
    socket is not authenticated yet, so a made-up hello must not cost
    memory (a frame can be 1 MiB)."""

    def short(value: object, limit: int = 64) -> object:
        if isinstance(value, str):
            return value[:limit]
        if isinstance(value, bool) or value is None:
            return value
        return value if isinstance(value, int | float) else None

    out: dict[str, Any] = {key: short(hello.get(key)) for key in _HELLO_KEYS}
    caps = hello.get("caps")
    out["caps"] = (
        [c[:32] for c in caps[:16] if isinstance(c, str)] if isinstance(caps, list) else None
    )
    rollback = hello.get("rollback")
    out["rollback"] = (
        {k: short(rollback.get(k)) for k in ("from_version", "reason")}
        if isinstance(rollback, dict)
        else None
    )
    return out


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def admin_host_allowed(host: str | None) -> bool:
    """True for a `Host` a person types to reach this server locally: an IP
    literal, `localhost`, a one-label name or a Tailscale `*.ts.net` name.
    A DNS-rebinding page arrives with its own domain name and is refused."""
    if not host:
        return False
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        name = host[1:end] if end > 0 else ""
    else:
        name, _, port = host.rpartition(":")
        if not name or not port.isdigit():
            name = host
    if not name:
        return False
    with contextlib.suppress(ValueError):
        ipaddress.ip_address(name)
        return True
    return name == "localhost" or "." not in name or name.endswith(".ts.net")


def _one(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None


def _parse_bool(raw: str | None, default: bool) -> bool | None:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off", ""):
        return False
    return None


class FirmwareService:
    """The firmware routes and the device's update/console events."""

    def __init__(self, directory: Path, device_token: SecretStr | None, link: DeviceLink) -> None:
        self._store = FirmwareStore(directory)
        # An empty token is no token: never open.
        self._token = configured_token(device_token)
        self._link = link
        self._devices: dict[str, _Device] = {}
        self._last_device_id: str | None = None
        self._pending_cmds: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # A version an admin offer asked for: sent at the next chance even
        # past the rollback guard (an explicit offer is not "automatic").
        self._explicit_offer: str | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def token_configured(self) -> bool:
        return self._token is not None

    @property
    def token_short(self) -> bool:
        """A configured token shorter than MIN_TOKEN_CHARS (weak against
        guessing through the tunnel)."""
        return self._token is not None and len(self._token.get_secret_value()) < MIN_TOKEN_CHARS

    @property
    def directory(self) -> Path:
        return self._store.directory

    def token_matches(self, headers: HeaderLookup) -> bool:
        """True when `headers` carry the device token (`huxley.server.auth`)."""
        return bearer_token_state(headers, self._token) == "valid"

    def close(self) -> None:
        """The server is stopping: end every dev-cmd wait now (its answer is
        503) and drop the scheduled reconnects, so the listener's close
        never waits on them."""
        for future in self._pending_cmds.values():
            if not future.done():
                future.set_exception(_ShuttingDownError())
        for task in list(self._tasks):
            task.cancel()

    # --- the device's routes (device token) ---

    async def device_http(self, path: str, headers: HeaderLookup, remote: str) -> HttpAnswer:
        """`GET /firmware/...`: the device token first, then the route."""
        forwarded = forwarded_for(headers)
        refused = self._check_token(headers, path, remote, forwarded)
        if refused is not None:
            return refused
        if path == MANIFEST_PATH:
            return await self._manifest(remote, forwarded)
        name = path[len(FIRMWARE_PREFIX) :]
        if name.endswith(".bin"):
            return await self._image(name[: -len(".bin")], remote, forwarded)
        logger.info("firmware.not_found", path=path[:200], remote=remote)
        return _error(HTTPStatus.NOT_FOUND, "not found")

    def _check_token(
        self, headers: HeaderLookup, path: str, remote: str, forwarded: str | None
    ) -> HttpAnswer | None:
        """None when the request carries the device token. Never logs any
        token, presented or configured."""
        if self._token is None:
            logger.warning(
                "firmware.auth_refused",
                reason="unconfigured",
                path=path[:200],
                remote=remote,
                forwarded_for=forwarded,
                note="HUXLEY_DEVICE_TOKEN is not set: firmware routes refuse everyone (503)",
            )
            return _error(HTTPStatus.SERVICE_UNAVAILABLE, "device token not configured")
        reason = bearer_token_state(headers, self._token)
        if reason == "valid":
            return None
        logger.warning(
            "firmware.auth_refused",
            reason=reason,
            path=path[:200],
            remote=remote,
            forwarded_for=forwarded,
        )
        return HttpAnswer(
            HTTPStatus.UNAUTHORIZED,
            (json.dumps({"error": "device token required"}) + "\n").encode(),
            headers=(("WWW-Authenticate", "Bearer"),),
        )

    def _manifest_body(self) -> tuple[Offer, ImageInfo] | None:
        """The offer and its image, None (logged) when nothing can be offered."""
        offer = self._store.read_offer()
        if offer is None:
            return None
        try:
            info = read_image_info(self._store.image_path(offer.version))
        except FirmwareError as exc:
            logger.error("firmware.manifest_unavailable", version=offer.version, reason=str(exc))
            return None
        if info.version != offer.version:
            logger.error(
                "firmware.manifest_unavailable",
                version=offer.version,
                reason=f"the image says it is {info.version!r}",
            )
            return None
        return offer, info

    async def _manifest(self, remote: str, forwarded: str | None) -> HttpAnswer:
        found = await asyncio.to_thread(self._manifest_body)
        if found is None:
            logger.info("firmware.manifest_none", remote=remote, forwarded_for=forwarded)
            return _error(HTTPStatus.NOT_FOUND, "no firmware offered")
        offer, info = found
        device = self._current_device()
        logger.info(
            "firmware.manifest_served",
            version=offer.version,
            bytes=info.size,
            elf_sha=info.elf_sha,
            remote=remote,
            forwarded_for=forwarded,
            conn_id=self._link.device_conn_id(),
            pending_boot=device is not None and device.pending is not None,
        )
        if device is not None and device.pending is not None:
            device.pending.manifest = (offer.version, info.elf_sha)
            self._advance(device, trigger="manifest")
        return _json_answer(
            HTTPStatus.OK,
            {
                "version": offer.version,
                "url": f"{FIRMWARE_PREFIX}{offer.version}.bin",
                "size": info.size,
                "elf_sha": info.elf_sha,
            },
        )

    async def _image(self, version: str, remote: str, forwarded: str | None) -> HttpAnswer:
        offer = await asyncio.to_thread(self._store.read_offer)
        downloadable = {offer.version, offer.previous} - {None} if offer is not None else set()
        if not valid_version(version) or version not in downloadable:
            logger.warning(
                "firmware.image_refused",
                version=version[:80],
                reason="bad_version" if not valid_version(version) else "not_offered",
                remote=remote,
                forwarded_for=forwarded,
            )
            return _error(HTTPStatus.NOT_FOUND, "not offered")
        path = self._store.image_path(version)
        try:
            body = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            logger.error("firmware.image_unreadable", version=version, error=str(exc))
            return _error(HTTPStatus.NOT_FOUND, f"no {version}.bin")
        logger.info(
            "firmware.image_served",
            version=version,
            bytes=len(body),
            remote=remote,
            forwarded_for=forwarded,
            conn_id=self._link.device_conn_id(),
        )
        return HttpAnswer(
            HTTPStatus.OK, body, content_type="application/octet-stream", long_send=True
        )

    # --- admin routes (the caller vetted the source) ---

    async def admin_http(self, path: str, query: str, headers: HeaderLookup) -> HttpAnswer:
        """`GET /admin/...`. Refuses requests a browser could send on its
        own: without `X-Huxley-Admin` (a cross-site page can't add a
        header without a CORS preflight, which this server never grants)
        or with a `Host` that is not local (DNS rebinding)."""
        if not headers.get_all(ADMIN_HEADER):
            logger.warning("firmware.admin_refused", reason="no_admin_header", path=path[:200])
            return _error(HTTPStatus.FORBIDDEN, f"send the header {ADMIN_HEADER}: 1")
        hosts = headers.get_all("Host")
        if len(hosts) != 1 or not admin_host_allowed(hosts[0]):
            logger.warning(
                "firmware.admin_refused",
                reason="host",
                host=hosts[0][:100] if hosts else None,
                path=path[:200],
            )
            return _error(
                HTTPStatus.FORBIDDEN, "use a local address (127.0.0.1, a Tailscale name)"
            )
        try:
            params = parse_qs(query, keep_blank_values=True, max_num_fields=8)
        except ValueError:
            return _error(HTTPStatus.BAD_REQUEST, "too many parameters")
        if path == ADMIN_STATUS_PATH:
            return await self._admin_status()
        if path == ADMIN_OFFER_PATH:
            return await self._admin_offer(params)
        if path == ADMIN_DEV_CMD_PATH:
            return await self._admin_dev_cmd(params)
        logger.info("firmware.admin_not_found", path=path[:200])
        return _error(HTTPStatus.NOT_FOUND, "not found")

    async def _admin_status(self) -> HttpAnswer:
        offer = await asyncio.to_thread(self._store.read_offer)
        found = await asyncio.to_thread(self._manifest_body) if offer is not None else None
        files = await asyncio.to_thread(self._store.files)
        device = self._current_device()
        walkie: dict[str, Any] | None = None
        if device is not None:
            hello = device.hello
            walkie = {
                "connected": self._connected(device),
                "conn_id": device.conn_id,
                "device_id": device.device_id,
                "hello_at": _iso(device.hello_at_ms),
                **{
                    key: hello.get(key)
                    for key in ("version", "elf_sha", "slot", "slot_state", "rollback", "caps")
                },
            }
        logger.info("firmware.admin_status", offered=offer.version if offer else None)
        return _json_answer(
            HTTPStatus.OK,
            {
                "offer": offer.to_json() if offer is not None else None,
                "manifest": (
                    {
                        "version": found[0].version,
                        "size": found[1].size,
                        "elf_sha": found[1].elf_sha,
                    }
                    if found is not None
                    else None
                ),
                "files": files,
                "token_configured": self.token_configured,
                "walkie": walkie,
                "pending": (
                    device.pending.to_json()
                    if device is not None and device.pending is not None
                    else None
                ),
            },
        )

    async def _admin_offer(self, params: dict[str, list[str]]) -> HttpAnswer:
        version = _one(params, "version")
        force = _parse_bool(_one(params, "force"), default=False)
        if not valid_version(version) or force is None:
            logger.warning(
                "firmware.offer_refused", reason="bad_params", version=str(version)[:80]
            )
            return _error(HTTPStatus.BAD_REQUEST, "version=<letters, digits, ._+-> [force=0|1]")
        assert version is not None
        try:
            info = await asyncio.to_thread(self._store.check_offerable, version)
        except FirmwareError as exc:
            logger.warning("firmware.offer_refused", reason=str(exc), version=version)
            return _error(exc.status, str(exc))
        offer = await asyncio.to_thread(self._store.write_offer, version, force)
        self._explicit_offer = version
        for known in self._devices.values():
            known.failures.pop(version, None)  # an explicit offer starts over
        logger.info(
            "firmware.offered",
            version=version,
            force=force,
            previous=offer.previous,
            bytes=info.size,
            elf_sha=info.elf_sha,
        )
        device = self._current_device()
        if device is None or not self._connected(device):
            sent, reason = False, "no_device_connected"
            logger.info("firmware.update_deferred", version=version, reason=reason)
        else:
            sent, reason = await self._maybe_send_update(device, trigger="offer")
        return _json_answer(
            HTTPStatus.OK,
            {
                "offer": offer.to_json(),
                "image": {"size": info.size, "elf_sha": info.elf_sha},
                "sent": sent,
                "reason": reason,
            },
        )

    async def _admin_dev_cmd(self, params: dict[str, list[str]]) -> HttpAnswer:
        line = _one(params, "line")
        raw_timeout = _one(params, "timeout_s")
        try:
            timeout_s = DEV_CMD_DEFAULT_TIMEOUT_S if raw_timeout is None else float(raw_timeout)
        except ValueError:
            timeout_s = -1.0
        if (
            not line
            or not line.strip()
            or len(line) > DEV_CMD_MAX_LINE
            or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in line)
            or not 0 < timeout_s <= DEV_CMD_MAX_TIMEOUT_S
        ):
            logger.warning("firmware.dev_cmd_refused", reason="bad_params")
            return _error(
                HTTPStatus.BAD_REQUEST,
                f"line=<one console line, up to {DEV_CMD_MAX_LINE} printable characters>"
                f" [timeout_s=<up to {DEV_CMD_MAX_TIMEOUT_S:g}>]",
            )
        conn_id = self._link.device_conn_id()
        device = self._current_device()
        if conn_id is None or device is None or device.conn_id != conn_id:
            logger.info("firmware.dev_cmd_refused", reason="no_device")
            return _error(HTTPStatus.CONFLICT, "no walkie connected")
        if not self._link.device_token_presented(conn_id):
            # Console lines can carry secrets and their output is trusted:
            # never send one to a connection that merely says it is firmware.
            logger.warning("firmware.dev_cmd_refused", reason="not_authenticated", conn_id=conn_id)
            return _error(
                HTTPStatus.CONFLICT, "the walkie's connection did not present the device token"
            )
        caps = device.hello.get("caps")
        if not isinstance(caps, list) or "dev_cmd.v1" not in caps:
            logger.info("firmware.dev_cmd_refused", reason="no_dev_cmd_cap", conn_id=conn_id)
            return _error(HTTPStatus.CONFLICT, "the walkie's hello has no dev_cmd.v1 in caps")
        cmd_id = uuid.uuid4().hex[:12]
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_cmds[cmd_id] = future
        started = time.monotonic()
        try:
            if not await self._link.send_device_event(
                conn_id, "dev.cmd", {"id": cmd_id, "line": line}
            ):
                logger.info("firmware.dev_cmd_refused", reason="device_gone", id=cmd_id)
                return _error(HTTPStatus.CONFLICT, "no walkie connected")
            # The command word only: arguments can carry secrets (Wi-Fi
            # passwords, tokens).
            logger.info(
                "firmware.dev_cmd_sent",
                id=cmd_id,
                cmd=line.split()[0][:32],
                chars=len(line),
                timeout_s=timeout_s,
                conn_id=conn_id,
            )
            result = await asyncio.wait_for(future, timeout_s)
        except TimeoutError:
            logger.warning("firmware.dev_cmd_timeout", id=cmd_id, timeout_s=timeout_s)
            return _error(HTTPStatus.GATEWAY_TIMEOUT, "no reply from the walkie", id=cmd_id)
        except _ShuttingDownError:
            logger.info("firmware.dev_cmd_refused", reason="shutting_down", id=cmd_id)
            return _error(HTTPStatus.SERVICE_UNAVAILABLE, "the server is stopping", id=cmd_id)
        finally:
            self._pending_cmds.pop(cmd_id, None)
        logger.info(
            "firmware.dev_cmd_done",
            id=cmd_id,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            output_chars=len(result["output"]),
            truncated=result["truncated"],
        )
        return _json_answer(HTTPStatus.OK, {"id": cmd_id, **result})

    # --- the device's events ---

    async def on_client_event(self, conn_id: str, event: str, data: dict[str, Any]) -> None:
        """Every `client_event` from the current client; acts on the
        device contract's update and console events."""
        if event == "device.hello":
            await self._on_hello(conn_id, data)
        elif event == "firmware.status":
            self._on_status(conn_id, data)
        elif event == "dev.cmd_result":
            self._on_cmd_result(data)

    async def _on_hello(self, conn_id: str, raw_hello: dict[str, Any]) -> None:
        hello = _trim_hello(raw_hello)
        raw_id = hello.get("device_id")
        device_id = raw_id if isinstance(raw_id, str) and raw_id else "unknown"
        device = self._devices.pop(device_id, None)
        if device is None:
            device = _Device(
                device_id=device_id, hello=hello, conn_id=conn_id, hello_at_ms=_now_ms()
            )
        else:
            device.hello, device.conn_id, device.hello_at_ms = hello, conn_id, _now_ms()
        self._devices[device_id] = device  # most recent last
        while len(self._devices) > _MAX_DEVICES:
            forgotten = next(iter(self._devices))
            del self._devices[forgotten]
            logger.info("firmware.device_forgotten", device_id=forgotten)
        self._last_device_id = device_id
        await self._accept_on_hello(device, conn_id)
        await self._maybe_send_update(device, trigger="hello")

    async def _accept_on_hello(self, device: _Device, conn_id: str) -> None:
        """The accept flow's decision point: every hello of a pending image."""
        hello = device.hello
        if hello.get("slot_state") != "pending_verify":
            if device.pending is not None:
                logger.info(
                    "firmware.pending_ended",
                    version=device.pending.version,
                    running=hello.get("version"),
                    slot_state=hello.get("slot_state"),
                    accept_sent=device.pending.accept_sent,
                )
                device.pending = None
            return
        pending = device.pending
        if pending is None or not pending.same_boot(hello):
            pending = device.pending = _PendingBoot.from_hello(hello)
            logger.info(
                "firmware.pending_boot",
                version=pending.version,
                elf_sha=pending.elf_sha,
                boot_counter=pending.boot_counter,
                device_id=device.device_id,
            )
        else:
            pending.last_uptime_s = _as_float(hello.get("uptime_s")) or pending.last_uptime_s
        reason = pending.blocker()
        if reason is None and pending.close_done and pending.closed_conn_id != conn_id:
            sent = await self._link.send_device_event(
                conn_id, "ota.accept", {"version": pending.version}
            )
            if sent:
                logger.info(
                    "firmware.accept_sent",
                    version=pending.version,
                    conn_id=conn_id,
                    repeat=pending.accept_sent,
                )
                pending.accept_sent = True
                return
            reason = "device_gone"
        elif reason is None:
            if pending.closed_conn_id != conn_id:
                self._force_reconnect(device, pending, conn_id, trigger="hello")
            reason = "awaiting_reconnect"
        logger.info(
            "firmware.accept_withheld",
            reason=reason,
            version=pending.version,
            selftest_error=pending.selftest_error,
            served=pending.manifest[0] if pending.manifest else None,
            conn_id=conn_id,
        )

    def _on_status(self, conn_id: str, data: dict[str, Any]) -> None:
        version = data.get("version")
        phase = data.get("phase")
        error = data.get("error")
        device = self._device_on(conn_id)
        pending = device.pending if device is not None else None
        failed = phase == "failed" or (phase == "selftest" and error is not None)
        log = logger.warning if failed else logger.info
        log(
            "firmware.status",
            version=version,
            phase=phase,
            error=error,
            conn_id=conn_id,
            pending_boot=pending is not None,
        )
        if device is not None and isinstance(version, str):
            self._count_update_progress(device, version[:64], phase)
        if phase != "selftest" or device is None or pending is None:
            return
        if version != pending.version:
            logger.warning(
                "firmware.status_ignored",
                reason="version_mismatch",
                version=version,
                pending=pending.version,
            )
            return
        if error is None:
            pending.selftest, pending.selftest_error = "passed", None
            self._advance(device, trigger="selftest")
        else:
            pending.selftest, pending.selftest_error = "failed", str(error)[:120]
            logger.warning(
                "firmware.accept_withheld",
                reason="selftest_failed",
                version=pending.version,
                selftest_error=pending.selftest_error,
                conn_id=conn_id,
            )

    def _count_update_progress(self, device: _Device, version: str, phase: object) -> None:
        """What a status means for the next offers: `downloading` uses up an
        admin offer's override and a no_server rollback's retry (counted
        when the walkie acts on it, not when the server sends it: the walkie
        waits until idle); `failed` counts towards MAX_UPDATE_FAILURES."""
        if phase == "downloading":
            if self._explicit_offer == version:
                self._explicit_offer = None
            rollback = device.hello.get("rollback")
            if isinstance(rollback, dict) and rollback.get("from_version") == version:
                device.no_server_retries[version] = device.no_server_retries.get(version, 0) + 1
        elif phase == "failed":
            device.failures[version] = device.failures.get(version, 0) + 1
            logger.warning(
                "firmware.update_failed",
                version=version,
                failures=device.failures[version],
                max_failures=MAX_UPDATE_FAILURES,
            )

    def _on_cmd_result(self, data: dict[str, Any]) -> None:
        cmd_id = data.get("id")
        future = self._pending_cmds.get(cmd_id) if isinstance(cmd_id, str) else None
        if future is None or future.done():
            logger.warning("firmware.dev_cmd_result_unmatched", id=str(cmd_id)[:40])
            return
        output = data.get("output")
        future.set_result(
            {
                "output": output
                if isinstance(output, str)
                else ("" if output is None else str(output)),
                "truncated": data.get("truncated") is True,
            }
        )

    # --- the accept flow's forced reconnect ---

    def _advance(self, device: _Device, *, trigger: str) -> None:
        """Self-test passed or manifest fetched: once both hold, close the
        device's connection (1012) so its next hello can be accepted."""
        pending = device.pending
        if pending is None or pending.blocker() is not None or pending.closed_conn_id is not None:
            return
        conn_id = self._link.device_conn_id()
        if conn_id is None or conn_id != device.conn_id:
            logger.info(
                "firmware.reconnect_deferred",
                version=pending.version,
                reason="device_not_connected",
                trigger=trigger,
            )
            return
        self._force_reconnect(device, pending, conn_id, trigger=trigger)

    def _force_reconnect(
        self, device: _Device, pending: _PendingBoot, conn_id: str, *, trigger: str
    ) -> None:
        pending.closed_conn_id, pending.close_done = conn_id, False
        logger.info(
            "firmware.reconnect_forced",
            version=pending.version,
            conn_id=conn_id,
            trigger=trigger,
            delay_s=RECONNECT_CLOSE_DELAY_S,
            code=RECONNECT_CLOSE_CODE,
        )
        task = asyncio.create_task(self._close_later(device, pending, conn_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _close_later(self, device: _Device, pending: _PendingBoot, conn_id: str) -> None:
        await asyncio.sleep(RECONNECT_CLOSE_DELAY_S)
        waited = False
        while self._link.device_busy() and self._link.device_conn_id() == conn_id:
            # Never cut a press, an answer or a call; a stream is fine (it
            # pauses with the connection and resumes after the reconnect).
            if not waited:
                logger.info("firmware.reconnect_waiting", reason="busy", conn_id=conn_id)
                waited = True
            await asyncio.sleep(RECONNECT_BUSY_POLL_S)
        if device.pending is not pending or pending.closed_conn_id != conn_id:
            return  # a newer boot or a newer close took over
        try:
            closed = await self._link.close_device(
                conn_id, RECONNECT_CLOSE_CODE, RECONNECT_CLOSE_REASON
            )
        except Exception:
            logger.exception("firmware.reconnect_failed", conn_id=conn_id)
            closed = False
        if pending.closed_conn_id != conn_id:
            return
        if closed:
            pending.close_done = True
        else:
            # Gone before the close: the next hello closes again.
            pending.closed_conn_id = None
            logger.info("firmware.reconnect_skipped", reason="device_gone", conn_id=conn_id)

    # --- offering the update ---

    async def _maybe_send_update(self, device: _Device, *, trigger: str) -> tuple[bool, str]:
        """Send `firmware.update` for the offer when the device runs
        something else and may take it. Returns (sent, reason)."""
        offer = await asyncio.to_thread(self._store.read_offer)
        hello = device.hello
        if offer is None:
            logger.debug("firmware.update_skipped", reason="no_offer", trigger=trigger)
            return False, "no_offer"
        reason: str | None = None
        info: ImageInfo | None = None
        caps = hello.get("caps")
        if not isinstance(caps, list) or "ota.v1" not in caps:
            reason = "no_ota_cap"
        elif self._token is None:
            reason = "no_device_token"  # the download would be refused (503)
        elif hello.get("slot_state") == "pending_verify":
            reason = "pending_verify"  # ESP-IDF refuses an update before the accept
        else:
            try:
                info = await asyncio.to_thread(
                    read_image_info, self._store.image_path(offer.version)
                )
            except FirmwareError as exc:
                logger.error(
                    "firmware.update_skipped",
                    reason="image_unavailable",
                    version=offer.version,
                    error=str(exc),
                )
                return False, "image_unavailable"
            running_sha = hello.get("elf_sha")
            if offer.force and isinstance(running_sha, str):
                # Uncommitted builds share a version string: force compares
                # the image itself, so it still never resends what runs.
                same = running_sha == info.elf_sha
            else:
                same = hello.get("version") == offer.version
            if same:
                reason = "same_version"
            elif (
                device.failures.get(offer.version, 0) >= MAX_UPDATE_FAILURES
                and self._explicit_offer != offer.version
            ):
                reason = "failed_too_often"  # offer it again from the admin route to retry
            else:
                reason = self._rollback_guard(device, offer.version)
        if reason is not None:
            logger.info(
                "firmware.update_skipped",
                reason=reason,
                version=offer.version,
                running=hello.get("version"),
                trigger=trigger,
            )
            return False, reason
        sent = await self._link.send_device_event(
            device.conn_id,
            "firmware.update",
            {
                "version": offer.version,
                "url": f"{FIRMWARE_PREFIX}{offer.version}.bin",
                "force": offer.force,
            },
        )
        if not sent:
            logger.info("firmware.update_deferred", version=offer.version, reason="device_gone")
            return False, "device_gone"
        logger.info(
            "firmware.update_sent",
            version=offer.version,
            force=offer.force,
            running=hello.get("version"),
            trigger=trigger,
            conn_id=device.conn_id,
        )
        return True, "sent"

    def _rollback_guard(self, device: _Device, version: str) -> str | None:
        """W4's rollback bookkeeping: an image the device rolled back from is
        not offered again automatically (a no_server rollback gets one
        retry per server run). An admin offer of it goes through."""
        rollback = device.hello.get("rollback")
        if not isinstance(rollback, dict) or rollback.get("from_version") != version:
            return None
        why = rollback.get("reason")
        if self._explicit_offer == version:
            logger.info("firmware.rollback_overridden", version=version, rollback_reason=why)
            return None
        if why == "no_server" and device.no_server_retries.get(version, 0) < 1:
            logger.info("firmware.rollback_retry", version=version, rollback_reason=why)
            return None
        logger.warning(
            "firmware.update_withheld",
            reason="rolled_back",
            version=version,
            rollback_reason=why,
            note="offer it again from /admin/firmware-offer to retry",
        )
        return "rolled_back"

    # --- helpers ---

    def _current_device(self) -> _Device | None:
        """The device that said hello last (one server serves one walkie)."""
        return self._devices.get(self._last_device_id) if self._last_device_id else None

    def _device_on(self, conn_id: str) -> _Device | None:
        device = self._current_device()
        return device if device is not None and device.conn_id == conn_id else None

    def _connected(self, device: _Device) -> bool:
        return self._link.device_conn_id() == device.conn_id

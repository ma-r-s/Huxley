"""WebSocket audio server — the single interface point for Huxley clients.

One client at a time (browser, ESP32, future `huxley-web` PWA, any
other client speaking the Huxley wire protocol). The client owns
audio I/O (mic capture, speaker playback); this server owns the
OpenAI session, tool dispatch, state machine, and storage.

See [`docs/clients.md`](../../../../../docs/clients.md) for how this
fits into the multi-client architecture and [`docs/protocol.md`](../../../../../docs/protocol.md)
for the wire-level spec of everything below.

Protocol — client → server
    {"type": "audio",     "data": "<base64 PCM16 24 kHz>"}
    {"type": "ptt_start"}
    {"type": "ptt_stop"}
    {"type": "wake_word"}
    {"type": "reset"}                     # dev: disconnect + fresh OpenAI session
    {"type": "client_event", "event": "<name>", "data": {...}}  # telemetry + skill dispatch

Protocol — server → client
    {"type": "hello",          "protocol": PROTOCOL_VERSION}  # first message
    {"type": "audio",          "data": "<base64 PCM16 24 kHz>"}
    {"type": "audio_clear"}                               # drop queued audio
    {"type": "state",          "value": "IDLE"|"CONNECTING"|"CONVERSING"}
    {"type": "status",         "message": "..."}
    {"type": "transcript",     "role": "user"|"assistant", "text": "..."}
    {"type": "model_speaking", "value": bool}
    {"type": "set_volume",     "level": int}              # 0-100, client-controlled
    {"type": "input_mode",     "mode": "assistant_ptt"|"skill_continuous",
                               "reason": "idle"|"claim_started"|"claim_ended"|"claim_preempted",
                               "claim_id": str|None}      # mic-streaming policy
    {"type": "claim_started",  "claim_id": str, "skill": str}  # observability
    {"type": "claim_ended",    "claim_id": str, "end_reason": str}
    {"type": "dev_event",      "kind": "...", "payload": {...}}
    {"type": "server_event",   "event": "<name>", "data": {...}}  # generic skill→client

Opus audio (opt-in, `?audio=opus`; the design is xiaozhi-esp32's)
    hello gains "audio_params": {"format": "opus", "sample_rate": 24000,
    "channels": 1, "frame_duration": 60}. After it, audio in both
    directions is binary messages, one raw 60 ms Opus packet each; the
    server sends no JSON `audio` to that client (it still accepts it).
    Everything else stays JSON text. See `opus_codec.py`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import ipaddress
import json
import socket
import struct
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import structlog
import websockets
import websockets.http11
from websockets.asyncio.server import ServerConnection, serve

from huxley.server.auth import (
    MIN_TOKEN_CHARS,
    HeaderLookup,
    bearer_token_state,
    configured_token,
    forwarded_for,
    local_origin,
)
from huxley.server.firmware import (
    ADMIN_PREFIX,
    FirmwareService,
    HttpAnswer,
    is_firmware_path,
)
from huxley.server.opus_codec import (
    AUDIO_PARAMS,
    FRAME_MS,
    OpusDecoderStream,
    OpusEncoderStream,
    OpusUnavailableError,
    opus_unavailable_reason,
)

# Localhost cookie jars from dev tools (React DevTools, HMR clients, etc.) can
# push the WebSocket upgrade's Cookie header past websockets v16's default 8 KB
# line limit, causing all handshakes to fail with SecurityError("line too long").
# Bump to 64 KB — more than enough for any real header, still bounded.
websockets.http11.MAX_LINE_LENGTH = 64 * 1024

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from pydantic import SecretStr
    from websockets.http11 import Request, Response

    from huxley.server.recorder import Recorder

logger = structlog.get_logger()

PROTOCOL_VERSION = 2

# Keys the log pipeline owns: structlog's own (`event`, `level`,
# `timestamp`, `logger`, `exc_info`, `stack_info`) and the server's bound
# `conn_id`. A client_event field with one of these names would replace
# or crash the server's value (the firmware's `level: "W"` was logged as
# "info"; a field named `event` raised TypeError), so it is logged as
# `client_<key>` instead. Skill handlers still get the data unchanged.
_RESERVED_LOG_KEYS = frozenset(
    {"event", "level", "timestamp", "logger", "exc_info", "stack_info", "conn_id"}
)


def client_event_log_fields(data: dict[str, Any]) -> dict[str, Any]:
    """`data` of a client_event, with log-reserved keys renamed `client_<key>`."""
    return {(f"client_{k}" if k in _RESERVED_LOG_KEYS else k): v for k, v in data.items()}


# T1.21 — keep the last N connection records (after they close) so the
# `get_diag` frame can return a recent-history snapshot. Bounded so a
# long-running server can't grow unbounded.
_DIAG_RECENT_CONNS_CAP = 32


def _now_ms() -> int:
    """Wall-clock ms since epoch for diagnostic timestamps. Matches the
    PWA's `Date.now()` so server + client logs can be aligned on a single
    timeline without timezone math."""
    return int(time.time() * 1000)


def iso_utc(ms: int) -> str:
    """Epoch ms as an ISO 8601 UTC timestamp to the second (`/health`)."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat(timespec="seconds")


# `GET /health` (docs/protocol.md "HTTP: /health") answers only requests
# from loopback or Tailscale (100.64.0.0/10 and Tailscale's IPv6 range);
# any other source gets 404. Behind the Cloudflare tunnel EVERY request
# arrives from loopback (cloudflared shares the network namespace), so the
# tunnel must never route /health: Cloudflare Access guards that hostname,
# and the device token comes later (docs/remote-operation.md decision 4).
# As a second guard, a request carrying a reverse proxy's forwarding header
# (cloudflared always adds Cf-Connecting-Ip, Cf-Ray and X-Forwarded-For;
# Tailscale Serve and Funnel add X-Forwarded-For) is refused too, whatever
# the tunnel's ingress rules say. 100.64.0.0/10 is also carrier-grade NAT
# space, but those addresses never reach this socket directly.
HEALTH_PATH = "/health"
_HEALTH_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)
_PROXY_HEADERS = (
    "Cf-Connecting-Ip",
    "Cf-Ray",
    "X-Forwarded-For",
    "Forwarded",
    "X-Real-Ip",
    "Tailscale-Funnel-Request",
)


# websockets' opening-handshake time limit (`open_timeout`, 10 s by
# default). It also bounds every plain HTTP answer given in
# process_request: a firmware image must be written out, and an admin
# dev-cmd must get its reply, inside it (huxley.server.firmware). 60 s
# leaves a 4 MB image about 70 KB/s.
_HANDSHAKE_TIMEOUT_S = 60.0


def _url_path(target: str) -> str | None:
    """The path part of a request target (no query), None if unparsable or
    not an origin-form target: `//host/health` and `http://host/health`
    would parse to `/health` and slip past a tunnel's path rule."""
    if not target.startswith("/") or target.startswith("//"):
        return None
    try:
        return urlparse(target).path
    except ValueError:
        return None


def health_source_allowed(remote: object) -> bool:
    """True when `remote` (a socket peername tuple) may read `/health`."""
    if not isinstance(remote, tuple) or not remote or not isinstance(remote[0], str):
        return False
    try:
        addr = ipaddress.ip_address(remote[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped  # a dual-stack socket's view of an IPv4 peer
    return any(addr in net for net in _HEALTH_NETWORKS)


def _local_refusal(
    connection: ServerConnection, request: Request
) -> tuple[str, str | None] | None:
    """Why a local-only route (`/health`, `/admin/*`) is refused: a reverse
    proxy's header (`("proxied", header)`) or a source outside loopback and
    Tailscale (`("source", None)`). None when allowed."""
    proxied = next((h for h in _PROXY_HEADERS if h in request.headers), None)
    if proxied is not None:
        return "proxied", proxied
    if not health_source_allowed(connection.remote_address):
        return "source", None
    return None


# Roles on the voice socket (docs/protocol.md "Authentication and roles";
# remote-operation.md decision 4). Production mode = a device token is
# configured; without one every connection is `dev`, as before roles.
ROLE_DEVICE = "device"
ROLE_LOCAL = "local"
ROLE_DEV = "dev"
# What the firmware sends; a `device` connection may send nothing else
# (binary Opus audio is dispatched before this check and always allowed).
DEVICE_MESSAGE_TYPES = frozenset(
    {"audio", "ptt_start", "ptt_stop", "wake_word", "client_event", "ping"}
)


@dataclass(frozen=True)
class _Access:
    """What an upgrade may do: its `role` (None: refused), where it came
    from (`source`: external, local, other; empty in dev mode) and its
    device token (`token`: valid, missing, bad_token)."""

    role: str | None
    source: str = ""
    token: str = ""
    # Why a refused upgrade is refused when the token isn't the reason:
    # `origin` (a web page on another site, 403).
    refusal: str = ""


def request_source(remote: object, headers: HeaderLookup) -> str:
    """`external`: relayed by a reverse proxy (the Cloudflare tunnel
    arrives from loopback, so its address means nothing); `local`:
    loopback or Tailscale, direct; `other`: anything else (the LAN)."""
    if any(headers.get_all(h) for h in _PROXY_HEADERS):
        return "external"
    return "local" if health_source_allowed(remote) else "other"


# Distinct refused message types logged per connection (then silent).
_MAX_REFUSED_TYPES = 32


def _may_be_firmware(conn: _ConnInfo) -> bool:
    """Whether this connection may speak for the walkie (identify as
    firmware, report update status, answer console lines): the device, or
    anyone in dev mode."""
    return conn.role in (ROLE_DEVICE, ROLE_DEV)


class _NoHeaders:
    """No headers at all (a test double without a request)."""

    def get_all(self, key: str) -> list[str]:
        del key
        return []


_NO_HEADERS = _NoHeaders()


def _query_params(target: str) -> tuple[str | None, str | None, str | None]:
    """(lang, persona, audio) from an upgrade's URL; all None when the
    query string is malformed (never block a handshake over it)."""
    try:
        qs = parse_qs(urlparse(target).query)
        lang_values = qs.get("lang") or qs.get("language")
        lang = lang_values[0].strip().lower() if lang_values else None
        # T1.13: `?persona=<name>` selects which persona this connection
        # is for. Trimmed but not lowercased; persona directory names are
        # case-sensitive.
        persona_values = qs.get("persona")
        persona = persona_values[0].strip() if persona_values else None
        # `?audio=opus` asks for binary Opus audio both ways instead of
        # base64 PCM in JSON (docs/protocol.md "Opus audio").
        audio_values = qs.get("audio")
        audio = audio_values[0].strip().lower() if audio_values else None
    except Exception:
        return None, None, None
    return lang or None, persona or None, audio or None


def _wants_evict(target: str) -> bool:
    """`?evict=1` in the upgrade's URL."""
    try:
        return parse_qs(urlparse(target).query).get("evict") == ["1"]
    except ValueError:
        return False


def _http_response(connection: ServerConnection, answer: HttpAnswer) -> Response:
    """A plain HTTP answer as the handshake's response: never upgraded,
    never cached. An image's answer gets no close deadline (websockets
    sets it before the drain, so the default 10 s would cut a slow
    download's tail); the handshake limit still bounds it."""
    response = connection.respond(answer.status, "")
    response.body = answer.body
    for name, value in (
        ("Content-Length", str(len(answer.body))),
        ("Content-Type", answer.content_type),
        ("Cache-Control", "no-store"),
        *answer.headers,
    ):
        if name in response.headers:
            del response.headers[name]
        response.headers[name] = value
    if answer.long_send:
        connection.close_timeout = None
    return response


# One `server.tx.audio_stats` line per this many ms while audio flows to
# a client: what the server handed over vs what the network took
# (F-0022 in clients/firmware/docs/triage.md).
_TX_STATS_WINDOW_MS = 1000
# PCM bytes per ms of the 24 kHz 16-bit mono stream.
_PCM_BYTES_PER_MS = 48
# At most one `server.tx.audio_dropped_no_client` line per this many
# seconds while audio is sent with nobody connected.
_NO_CLIENT_DROP_LOG_S = 5.0
_SO_NWRITE = 0x1024  # macOS <sys/socket.h>: bytes in the send buffer
_TIOCOUTQ = 0x5411  # Linux <asm-generic/ioctls.h>: same, via ioctl


def _sock_unsent(ws: ServerConnection) -> int | None:
    """Bytes the kernel holds in this connection's send buffer (not yet
    sent or not yet acked): grows when the network is slower than the
    stream. None where the platform can't say."""
    try:
        sock = ws.transport.get_extra_info("socket")
        if sock is None:
            return None
        if sys.platform == "darwin":
            return int(sock.getsockopt(socket.SOL_SOCKET, _SO_NWRITE))
        if sys.platform.startswith("linux"):
            buf = fcntl.ioctl(sock.fileno(), _TIOCOUTQ, b"\0\0\0\0")
            return int(struct.unpack("i", buf)[0])
    except (OSError, AttributeError, ValueError):
        return None
    return None


@dataclass
class _ConnInfo:
    """T1.21 — per-WS-connection diagnostic record.

    Holds the live websocket plus a count of frames seen in each
    direction, plus close-time metadata once the connection ends. Stays
    in `_recent_conns` after disconnect so the diagnostic endpoint can
    return a "what happened in the last few connects" timeline.

    `conn_id` is the operator-facing id — short enough to type, long
    enough to be unique across a session. Echoed in `hello` so the PWA
    can render it in its diagnostic panel and so server log lines (which
    bind `conn_id` via structlog contextvars) can be filtered by the
    same string the operator sees client-side.
    """

    conn_id: str
    remote: str
    connected_at_ms: int
    requested_persona: str | None
    requested_language: str | None
    ws: ServerConnection | None = None  # None after close (released for GC)
    frames_tx: int = 0
    frames_rx: int = 0
    bytes_tx: int = 0
    bytes_rx: int = 0
    last_rx_at_ms: int | None = None
    last_tx_at_ms: int | None = None
    closed_at_ms: int | None = None
    close_code: int | None = None
    close_reason: str | None = None
    # Per-frame-type histogram so the diag panel can show "this conn
    # sent 5 ptt_start, 200 audio, 3 get_skills_state". Cheap; updated
    # in `_send` / `_dispatch`.
    rx_types: dict[str, int] = field(default_factory=dict)
    tx_types: dict[str, int] = field(default_factory=dict)
    # Outbound audio, current stats window (see `_tx_audio_stats`).
    tx_audio_window_start_ms: int = 0
    tx_audio_pcm_bytes: int = 0
    tx_audio_wire_bytes: int = 0
    tx_audio_sends: int = 0
    tx_audio_wait_ms: float = 0.0
    tx_audio_max_wait_ms: float = 0.0
    # Audio encoding chosen at connect: "pcm" (base64 PCM inside JSON
    # `audio`, the default) or "opus" (`?audio=opus`: binary Opus
    # packets both ways, docs/protocol.md "Opus audio").
    audio_format: str = "pcm"
    opus_tx: OpusEncoderStream | None = None
    opus_rx: OpusDecoderStream | None = None
    # Encoded packets not yet written, in encoder order. Every sender
    # drains this one queue (see `_drain_opus`), so concurrent
    # `send_audio` calls cannot reorder packets, and `audio_clear`
    # empties it so nothing encoded before the clear follows it.
    opus_out: deque[bytes] = field(default_factory=deque)
    # Sends the partial frame when no PCM came for a while (the end of
    # an earcon nothing follows). Restarted by every `send_audio`.
    opus_flush_timer: asyncio.TimerHandle | None = None
    opus_flush_task: asyncio.Task[None] | None = None
    opus_last_push_s: float = 0.0  # time.monotonic() of the last send_audio
    opus_rx_errors: int = 0
    # Set once `hello` is out. An opus client gets no binary before its
    # hello (it learns the format there), so audio for it until then is
    # dropped: a reconnect mid-song loses a few ms, never confuses it.
    hello_sent: bool = False
    # `recorder.skipped_pcm_client` said for this connection already.
    recording_skip_logged: bool = False
    # Which kind of client this is, from its device event (`device`'s
    # `shell`: `firmware`, `browser`, `home_screen`, `native_app`; a
    # `device.hello` means `firmware`). None until it says. `/health`.
    shell: str | None = None
    # What this connection may do (docs/protocol.md "Authentication and
    # roles"): `device` (its upgrade carried the device token: the
    # firmware's messages only), `local` (production, loopback or
    # Tailscale without the token: everything), `dev` (no token configured:
    # everything, as before roles). Only `device` gets console lines.
    role: str = "dev"
    # Message types refused for this role, each logged once.
    refused_types: set[str] = field(default_factory=set)

    def to_diag(self) -> dict[str, Any]:
        """Serialize to the wire-format the `diag_state` frame uses.
        Drops the live `ws` reference (not JSON-serializable, not
        useful client-side)."""
        return {
            "conn_id": self.conn_id,
            "remote": self.remote,
            "connected_at_ms": self.connected_at_ms,
            "requested_persona": self.requested_persona,
            "requested_language": self.requested_language,
            "audio_format": self.audio_format,
            "frames_tx": self.frames_tx,
            "frames_rx": self.frames_rx,
            "bytes_tx": self.bytes_tx,
            "bytes_rx": self.bytes_rx,
            "last_rx_at_ms": self.last_rx_at_ms,
            "last_tx_at_ms": self.last_tx_at_ms,
            "closed_at_ms": self.closed_at_ms,
            "close_code": self.close_code,
            "close_reason": self.close_reason,
            "rx_types": dict(self.rx_types),
            "tx_types": dict(self.tx_types),
        }


# Mic-streaming policy values (server → client in `input_mode`).
# - `assistant_ptt`: the client gates mic streaming by the user's
#   assistant-address trigger (today: PTT hold; tomorrow: wake word / VAD).
# - `skill_continuous`: a skill owns the mic via an active InputClaim;
#   the client streams mic frames continuously until the mode flips back.
# See docs/protocol.md "Mic mode" for the full semantics.
INPUT_MODE_ASSISTANT_PTT = "assistant_ptt"
INPUT_MODE_SKILL_CONTINUOUS = "skill_continuous"


class AudioServer:
    """WebSocket server: one audio client at a time.

    Clients connect, exchange PCM audio and control events, and disconnect.
    When a second connection arrives while a client is active, the older
    client is **evicted** (closed with code `1001 — Replaced by new
    client`); the new client is accepted. Rationale: a fresh connect is
    almost always a browser reload or a re-flashed device. See
    ``docs/decisions.md`` § "One WebSocket client at a time".
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_wake_word: Callable[[], Awaitable[None]],
        on_ptt_start: Callable[[], Awaitable[None]],
        on_ptt_stop: Callable[[], Awaitable[None]],
        on_audio_frame: Callable[[bytes], Awaitable[None]],
        on_reset: Callable[[], Awaitable[None]],
        on_language_select: Callable[[str | None], Awaitable[None]] | None = None,
        on_list_sessions: Callable[[], Awaitable[None]] | None = None,
        on_get_session: Callable[[int], Awaitable[None]] | None = None,
        on_delete_session: Callable[[int], Awaitable[None]] | None = None,
        # Marketplace v2 Phase A — PWA opens DeviceSheet's Skills section
        # and asks for the current state of every installed skill (enabled
        # flag, current_config, secret_keys_set, config_schema). Runtime
        # owns the builder because it has both `current_app` AND access
        # to the `huxley.skills` entry-point group.
        on_get_skills_state: Callable[[], Awaitable[None]] | None = None,
        # Marketplace v2 Phase C — registry browse. PWA opens the
        # Marketplace tab; runtime fetches/caches the static
        # huxley-registry/index.json feed + decorates with
        # installed-status; replies with `marketplace_state`.
        on_get_marketplace: Callable[[], Awaitable[None]] | None = None,
        # Marketplace v2 Phase D — restart trigger. PWA's "Restart server"
        # maintenance button + (post-Phase D) the install completion
        # callback both invoke this. Runtime initiates a graceful
        # shutdown + `os.execv` to replace the current process with a
        # fresh interpreter (picks up new venv state from `uv add`).
        on_restart_server: Callable[[], Awaitable[None]] | None = None,
        # Marketplace v2 Phase D — install request from the PWA's
        # Marketplace tab. Runtime validates, runs `uv add`, and on
        # success triggers a restart via the same path as
        # `on_restart_server`.
        on_install_skill: Callable[[str], Awaitable[None]] | None = None,
        # Marketplace v2 Phase B — write handlers. The dispatch case
        # validates types + shapes before invoking; Runtime persists
        # the change to disk and triggers _reload_current_persona,
        # which re-runs setup_all so the running skills pick up the
        # new state. The existing skills_state push (Phase A critic
        # fix #3) refreshes the PWA automatically on swap_committed.
        on_set_skill_enabled: (Callable[[str, bool], Awaitable[None]] | None) = None,
        on_set_skill_config: (Callable[[str, dict[str, Any]], Awaitable[None]] | None) = None,
        on_set_skill_secret: (Callable[[str, str, str], Awaitable[None]] | None) = None,
        on_delete_skill_secret: (Callable[[str, str], Awaitable[None]] | None) = None,
        # T1.13 — persona swap via `?persona=<name>` reconnect.
        # `on_persona_select` fires BEFORE hello on each new connection;
        # the runtime decides whether to swap to a different Application.
        # `get_hello_extras` lets the runtime inject `current_persona`
        # + `available_personas` into the hello payload (additive,
        # non-breaking; old clients ignore unknown fields). Both default
        # to None for tests / single-persona deployments that don't wire
        # a runtime layer.
        # The runtime receives BOTH the requested persona name and the
        # `?lang=` value here so a swap can construct the new Application
        # in the right language from the start, avoiding a redundant
        # disconnect+reconnect when the persona's default language
        # differs from what the client requested. The server's
        # subsequent `on_language_select` call still fires (covers
        # the same-persona-different-language case), but for a swap
        # path it short-circuits because target == current already.
        on_persona_select: (Callable[[str | None, str | None], Awaitable[None]] | None) = None,
        get_hello_extras: Callable[[], dict[str, object]] | None = None,
        # Whether anyone is listening. Fires True once a new client is
        # current and its handshake is out, False when the current client
        # goes away with no replacement (an eviction by a newer client
        # fires only the newer client's True). The coordinator pauses
        # content when nobody listens instead of streaming it into
        # `_send_raw`'s silent drop. None for tests and tools.
        on_client_presence: Callable[[bool], Awaitable[None]] | None = None,
        # The runtime's part of `GET /health` (persona, session, skills,
        # process); the server adds `client`, `walkie_last_seen` and
        # `recording`. None (tests, tools): `/health` is not served.
        get_health: Callable[[], dict[str, object]] | None = None,
        # Firmware images and the admin routes (`huxley.server.firmware`),
        # served from this directory, the device routes gated by this
        # token. None (tests, tools): `/firmware/*` and `/admin/*` are not
        # served.
        firmware_dir: Path | None = None,
        device_token: SecretStr | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_wake_word = on_wake_word
        self._on_ptt_start = on_ptt_start
        self._on_ptt_stop = on_ptt_stop
        self._on_audio_frame = on_audio_frame
        self._on_reset = on_reset
        self._on_language_select = on_language_select
        # T1.12 — session history retrieval. Optional so clients without
        # a sessions-aware app (or tests using the registry-only stub)
        # don't have to plumb empty handlers; an unset callback turns
        # the inbound message into a logged no-op rather than a crash.
        self._on_list_sessions = on_list_sessions
        self._on_get_session = on_get_session
        self._on_delete_session = on_delete_session
        self._on_get_skills_state = on_get_skills_state
        self._on_get_marketplace = on_get_marketplace
        self._on_restart_server = on_restart_server
        self._on_install_skill = on_install_skill
        self._on_set_skill_enabled = on_set_skill_enabled
        self._on_set_skill_config = on_set_skill_config
        self._on_set_skill_secret = on_set_skill_secret
        self._on_delete_skill_secret = on_delete_skill_secret
        # T1.13 — persona swap hooks (see constructor docstring above).
        self._on_persona_select = on_persona_select
        self._get_hello_extras = get_hello_extras
        self._on_client_presence = on_client_presence
        self._get_health = get_health
        # When a firmware client (`shell: firmware`) was last connected:
        # set when one identifies itself and when it goes away. In memory,
        # since this process started. `/health`'s `walkie_last_seen`.
        self._firmware_last_seen_ms: int | None = None
        # `?persona=<name>` from the URL, captured in `_process_request`
        # and consumed before hello in `_handle_connection`. Single field
        # is safe for the same reason `_pending_language` is — handshakes
        # serialize per connection and we evict old clients.
        self._pending_persona: str | None = None
        # T1.21 — `_conn` carries the live websocket plus per-connection
        # diagnostic state (conn_id, frame counts, frame-type histograms,
        # connect/close timestamps). Replaces the old `_client:
        # ServerConnection | None` so frame stats are tracked at the same
        # boundary every send/receive crosses, without scattering counters
        # across the dispatch path. Public accessors (`has_client`, etc.)
        # are preserved for compatibility.
        self._conn: _ConnInfo | None = None
        # T1.21 — recent-history buffer. After a connection closes, its
        # `_ConnInfo` lands here so `get_diag` can return the timeline
        # of recent connects + their close reasons. Bounded so a long-
        # running server can't grow unbounded.
        self._recent_conns: deque[_ConnInfo] = deque(maxlen=_DIAG_RECENT_CONNS_CAP)
        self._state = "IDLE"
        # Last-sent input mode — cached so a new client can be brought
        # up to the current mic policy on connect. Defaults to PTT
        # because a fresh client has no active claim by definition.
        self._input_mode = INPUT_MODE_ASSISTANT_PTT
        self._active_claim_id: str | None = None
        # Marketplace v2 Phase D — track stream lifecycle so
        # `_shim_install_skill` (and the maintenance restart button)
        # can refuse mid-stream restart that would kill an audiobook
        # or radio stream. Set from `send_stream_started`, cleared
        # from `send_stream_ended`. Phase D post-impl critic §1+§2.
        self._active_stream_id: str | None = None
        # Language the currently-handshaking client requested via
        # `?lang=<code>` in the WebSocket URL. Captured in
        # `_process_request` (runs before `_handle_connection`), consumed
        # in `_handle_connection` to fire `on_language_select`. Single
        # field is safe because `serve()` runs handshakes serially per
        # connection and AudioServer allows only one client at a time;
        # a second concurrent upgrade would race but is architecturally
        # excluded (we evict old clients, not parallel them).
        self._pending_language: str | None = None
        # `?audio=<format>` from the URL, same capture/consume pattern as
        # `_pending_language`. "opus" asks for binary Opus audio.
        self._pending_audio: str | None = None
        # `client_event` subscription registry: event_key → list of
        # (skill_name, handler) pairs. Skills register via
        # `register_client_event_subscriber`; the connection-handling
        # loop dispatches on each inbound `client_event`. Persisted on
        # the AudioServer (process-lifetime) so subscriptions survive
        # transient WebSocket disconnects without skills having to
        # re-subscribe. The (skill_name, handler) tuple lets
        # `unregister_client_event_subscribers` remove all of one
        # skill's subs at teardown without scanning every entry.
        self._client_event_subs: dict[
            str, list[tuple[str, Callable[[dict[str, Any]], Awaitable[None]]]]
        ] = {}
        # Gate for `_dispatch_client_event`. Flipped to False by
        # `disable_client_event_dispatch()` at the start of shutdown so
        # late-arriving messages can't trigger handlers that would
        # route through a half-stopped framework. Default-on so steady
        # state is unaffected.
        self._dispatching_enabled: bool = True
        # Recording (persona opt-in, `huxley.server.recorder`). None when
        # the running persona doesn't record: every tap is one `is None`
        # check. The context below mirrors the control frames sent to the
        # client, so a recorder attached mid-activity (a reload during a
        # stream, a swap mid-claim) starts from what is playing now.
        self._recorder: Recorder | None = None
        self._model_speaking = False
        self._active_stream_label: str | None = None
        self._claim_info: tuple[str, str, str | None] | None = None
        self._ptt_active = False
        # Audio sent while no client is connected (`send_audio` drops it):
        # counted, logged at most every `_NO_CLIENT_DROP_LOG_S`.
        self._no_client_drop_sends = 0
        self._no_client_drop_bytes = 0
        self._no_client_drop_logged_s = float("-inf")
        self._firmware: FirmwareService | None = (
            FirmwareService(firmware_dir, device_token, link=self)
            if firmware_dir is not None
            else None
        )
        # Production mode: a device token is configured. Without one every
        # connection keeps today's full behaviour (dev).
        self._device_token = configured_token(device_token)
        # Connections still writing a firmware image (aborted at shutdown,
        # so the listener's close doesn't wait out a slow download).
        self._long_sends: set[ServerConnection] = set()

    @property
    def has_client(self) -> bool:
        return self._conn is not None

    @property
    def conn_id(self) -> str | None:
        """T1.21 — current connection's id, or None during the gap
        between disconnect and the next connect. Useful to operators
        cross-referencing PWA-side log entries against server logs."""
        return self._conn.conn_id if self._conn is not None else None

    def set_recorder(self, recorder: Recorder | None) -> None:
        """The recorder of the persona now current, or None when it does
        not record. Called by the runtime when a persona becomes current
        (boot, swap, reload). A recorder replaced here (the previous
        persona's) is told to close what it has open at once; its owner
        stops it when that persona shuts down."""
        previous, self._recorder = self._recorder, recorder
        if previous is not None and previous is not recorder:
            previous.detached()
        if recorder is None or recorder is previous:
            return
        if self._model_speaking:
            recorder.model_speaking(True)
        if self._active_stream_id is not None:
            recorder.stream_started(self._active_stream_id, self._active_stream_label)
        if self._claim_info is not None:
            recorder.claim_started(*self._claim_info)
        conn = self._conn
        if conn is not None:
            recorder.connection_opened(conn.conn_id, conn.audio_format)
            if self._ptt_active:
                recorder.ptt(True)
            self._log_recording_skipped(conn)

    def detach_recorder(self, recorder: Recorder) -> bool:
        """Stop recording into `recorder`, if it is the one attached. A
        dying persona never detaches the recorder of the one that
        replaced it. True when it was attached."""
        if self._recorder is not recorder:
            return False
        self._recorder = None
        return True

    def _log_recording_skipped(self, conn: _ConnInfo) -> None:
        """Once per connection: a PCM client is never recorded (v1)."""
        if self._recorder is None or conn.audio_format == "opus" or conn.recording_skip_logged:
            return
        conn.recording_skip_logged = True
        logger.info(
            "recorder.skipped_pcm_client",
            conn_id=conn.conn_id,
            audio_format=conn.audio_format,
            note="only Opus clients are recorded",
        )

    @property
    def has_active_claim(self) -> bool:
        """Phase D — public accessor used by `_shim_install_skill`
        and `_shim_restart_server` to gate destructive ops on an
        active call. Mirrors the `_active_claim_id` lifecycle that
        `claim_started` / `claim_ended` already maintain."""
        return self._active_claim_id is not None

    @property
    def has_active_stream(self) -> bool:
        """Phase D — public accessor for active long-form audio
        playback (audiobook, radio). Mirrors `_active_stream_id` set
        by `send_stream_started` and cleared by `send_stream_ended`.
        Same destructive-op gating as `has_active_claim`."""
        return self._active_stream_id is not None

    @property
    def is_busy(self) -> bool:
        """Combined gate — true if a claim or stream is active.
        Phase D's restart paths use this to refuse mid-conversation
        installs / manual restarts."""
        return self.has_active_claim or self.has_active_stream

    async def run(self) -> None:
        async with serve(
            self._handle_connection,
            self._host,
            self._port,
            process_request=self._process_request,
            open_timeout=_HANDSHAKE_TIMEOUT_S,
        ):
            opus_error = opus_unavailable_reason()
            firmware = self._firmware
            await logger.ainfo(
                "audio_server_listening",
                url=f"ws://{self._host}:{self._port}",
                opus_available=opus_error is None,
                firmware_dir=str(firmware.directory) if firmware is not None else None,
                device_token_set=firmware.token_configured if firmware is not None else None,
            )
            if opus_error is not None:
                # Opus clients will get PCM; this says why, once, at boot.
                await logger.awarning("server.audio.opus_unavailable", reason=opus_error)
            token = self._device_token
            if token is not None and len(token.get_secret_value()) < MIN_TOKEN_CHARS:
                await logger.awarning(
                    "server.auth.device_token_short",
                    min_chars=MIN_TOKEN_CHARS,
                    note="HUXLEY_DEVICE_TOKEN is guessable through the tunnel: use 32+ random chars",
                )
            try:
                await asyncio.Future()  # run until cancelled
            finally:
                # Before serve() closes: it waits for every connection still
                # in its handshake, and an admin dev-cmd or an image download
                # is one (up to the 60 s handshake limit).
                self._stop_http_waits()

    async def _process_request(
        self,
        connection: ServerConnection,
        request: Request,
    ) -> Response | None:
        """Intercept the WebSocket upgrade to capture URL query params.

        Clients pass `?lang=<code>` to select the persona's translation
        for this session. `ServerConnection` uses `__slots__` so we can't
        attach the parsed value to the connection itself — instead we
        cache it on the server and consume it in `_handle_connection`,
        which runs immediately after the handshake completes. Returning
        `None` lets the handshake proceed normally.

        `GET /health` is answered here as plain HTTP, never upgraded and
        never touching the pending query params above, so it cannot
        disturb the voice socket (see `_health_response`). `/firmware/*`
        and `/admin/*` are answered the same way (`_firmware_response`).
        Every other path keeps the WebSocket handshake as before.
        """
        get_health = self._get_health
        path = _url_path(request.path)
        if get_health is not None and path == HEALTH_PATH:
            return self._health_response(connection, request, get_health)
        if self._firmware is not None and path is not None and is_firmware_path(path):
            return await self._firmware_response(connection, request, path, self._firmware)
        refused = self._upgrade_refusal(connection, request)
        if refused is not None:
            return refused
        del connection
        self._pending_language, self._pending_persona, self._pending_audio = _query_params(
            request.path
        )
        return None

    def _requested_params(self, ws: ServerConnection) -> tuple[str | None, str | None, str | None]:
        """(lang, persona, audio) this connection asked for, from its own
        request; the parked fields only for a test double without one."""
        request = getattr(ws, "request", None)
        if request is not None:
            return _query_params(request.path)
        return self._pending_language, self._pending_persona, self._pending_audio

    def _health_response(
        self,
        connection: ServerConnection,
        request: Request,
        get_health: Callable[[], dict[str, object]],
    ) -> Response:
        """`GET /health`: 200 with a JSON body for loopback and Tailscale,
        404 for anyone else or anything relayed by a reverse proxy.
        Read-only and synchronous: it never awaits, so the voice socket's
        handlers can't interleave with it."""
        remote = connection.remote_address
        refusal = _local_refusal(connection, request)
        if refusal is not None:
            logger.warning(
                "server.health_refused",
                remote=str(remote),
                reason=refusal[0],
                header=refusal[1],
                note="only loopback and Tailscale may read /health, never via a proxy; 404",
            )
            return connection.respond(HTTPStatus.NOT_FOUND, "Not Found\n")
        status = HTTPStatus.OK
        try:
            payload: dict[str, object] = {**get_health(), **self._health_fields()}
            body = json.dumps(payload)
        except Exception as exc:
            logger.exception("server.health_failed", remote=str(remote))
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            body = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        else:
            logger.debug("server.health", remote=str(remote), ok=payload.get("ok"))
        response = connection.respond(status, body + "\n")
        del response.headers["Content-Type"]
        response.headers["Content-Type"] = "application/json"
        response.headers["Cache-Control"] = "no-store"
        return response

    async def _firmware_response(
        self,
        connection: ServerConnection,
        request: Request,
        path: str,
        firmware: FirmwareService,
    ) -> Response:
        """`/firmware/*` (the device token decides, from any source: the
        walkie comes through the tunnel) and `/admin/*` (loopback and
        Tailscale only, never via a proxy, like `/health`). Never touches
        the pending query params, so the voice socket can't notice it."""
        remote = str(connection.remote_address)
        try:
            if path.startswith(ADMIN_PREFIX):
                refusal = _local_refusal(connection, request)
                if refusal is not None:
                    logger.warning(
                        "firmware.admin_refused",
                        remote=remote,
                        reason=refusal[0],
                        header=refusal[1],
                        path=path[:200],
                        note="only loopback and Tailscale may use /admin, never via a proxy; 404",
                    )
                    return connection.respond(HTTPStatus.NOT_FOUND, "Not Found\n")
                query = urlparse(request.path).query
                answer = await firmware.admin_http(path, query, request.headers)
            else:
                answer = await firmware.device_http(path, request.headers, remote)
        except Exception:
            logger.exception("firmware.http_failed", path=path[:200], remote=remote)
            answer = HttpAnswer(HTTPStatus.INTERNAL_SERVER_ERROR, b'{"error": "internal error"}\n')
        if answer.long_send:
            self._long_sends = {c for c in self._long_sends if not c.transport.is_closing()}
            self._long_sends.add(connection)
        return _http_response(connection, answer)

    def _stop_http_waits(self) -> None:
        """Shutdown: end pending dev-cmd waits and abort image downloads
        still draining, so closing the listener is quick."""
        if self._firmware is not None:
            self._firmware.close()
        aborted = 0
        for connection in self._long_sends:
            if not connection.transport.is_closing():
                connection.transport.abort()
                aborted += 1
        self._long_sends.clear()
        if aborted:
            logger.info("firmware.image_send_aborted", reason="shutdown", count=aborted)

    # --- production role (docs/protocol.md "Authentication and roles") ---

    def _access(self, remote: object, headers: HeaderLookup) -> _Access:
        """The role an upgrade from `remote` with `headers` gets. Dev mode
        (no device token configured): `dev`, everything as before."""
        if self._device_token is None:
            return _Access(ROLE_DEV)
        source = request_source(remote, headers)
        token = bearer_token_state(headers, self._device_token)
        if token == "valid":
            return _Access(ROLE_DEVICE, source, token)
        if source == "local":
            if not local_origin(headers):
                # A page on another site, opened in a browser on the box or
                # on the tailnet, must not get `local` (everything).
                return _Access(None, source, token, refusal="origin")
            return _Access(ROLE_LOCAL, source, token)
        return _Access(None, source, token)

    def _evicts_device(self, access: _Access, target: str) -> bool:
        """Production: a connection that isn't the device would replace a
        connected device without a local `?evict=1`."""
        current = self._conn
        return (
            self._device_token is not None
            and current is not None
            and current.role == ROLE_DEVICE
            and access.role != ROLE_DEVICE
            and not (access.source == "local" and _wants_evict(target))
        )

    def _upgrade_refusal(self, connection: ServerConnection, request: Request) -> Response | None:
        """Production: refuse an upgrade with no role (401) or one that would
        evict the connected device (409). None: the handshake goes on."""
        if self._device_token is None:
            return None
        access = self._access(connection.remote_address, request.headers)
        remote = str(connection.remote_address)
        if access.role is None:
            # Never the token: only whether one came and whether it matched.
            origin = request.headers.get_all("Origin")
            logger.warning(
                "server.auth.refused",
                reason=access.refusal or access.token,
                remote=remote,
                external=access.source == "external",
                source=access.source,
                forwarded_for=forwarded_for(request.headers),
                origin=origin[0][:100] if origin else None,
            )
            if access.refusal == "origin":
                return connection.respond(HTTPStatus.FORBIDDEN, "cross-site connection refused\n")
            response = connection.respond(HTTPStatus.UNAUTHORIZED, "device token required\n")
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        if self._evicts_device(access, request.path):
            logger.warning(
                "server.auth.evict_refused",
                role=access.role,
                remote=remote,
                device_conn_id=self._conn.conn_id if self._conn is not None else None,
                note="the walkie is connected; a local client may replace it with ?evict=1",
            )
            return connection.respond(
                HTTPStatus.CONFLICT, "the walkie is connected; add ?evict=1 to replace it\n"
            )
        return None

    def _connection_access(self, ws: ServerConnection) -> _Access:
        """The role of an accepted connection, from its own request (never a
        field shared between handshakes: two upgrades can interleave)."""
        request = getattr(ws, "request", None)  # None on test doubles
        headers: HeaderLookup = request.headers if request is not None else _NO_HEADERS
        return self._access(ws.remote_address, headers)

    async def _admit(self, ws: ServerConnection, access: _Access, conn_id: str) -> bool:
        """Last check before a connection becomes current: `_upgrade_refusal`
        decided on the HTTP request, but the device may have connected while
        this handshake finished. False (logged, socket closed 1008) when the
        connection has no role or would evict the device."""
        target = getattr(getattr(ws, "request", None), "path", "/")
        if access.role is not None and not self._evicts_device(access, target):
            return True
        reason = "no_role" if access.role is None else "evicts_device"
        await logger.awarning(
            "server.auth.evict_refused" if access.role is not None else "server.auth.refused",
            reason=reason,
            role=access.role,
            remote=str(ws.remote_address),
            late=True,
            new_conn_id=conn_id,
        )
        with contextlib.suppress(Exception):
            await ws.close(1008, "the walkie is connected" if access.role else "unauthorized")
        return False

    def _refuse_for_role(self, conn: _ConnInfo, msg_type: object) -> None:
        """A message this connection's role may not send: dropped, said once
        per type per connection."""
        name = str(msg_type)[:40]
        if name in conn.refused_types or len(conn.refused_types) >= _MAX_REFUSED_TYPES:
            return
        conn.refused_types.add(name)
        logger.warning("server.rx.refused_for_role", role=conn.role, msg_type=name)

    def device_token_presented(self, conn_id: str) -> bool:
        """Whether connection `conn_id` (the current one) opened with the
        device token (its role is `device`)."""
        conn = self._conn
        return conn is not None and conn.conn_id == conn_id and conn.role == ROLE_DEVICE

    def device_busy(self) -> bool:
        """A press, the model speaking, or a skill holding the mic (a call)."""
        return self._ptt_active or self._model_speaking or self.has_active_claim

    # --- DeviceLink (huxley.server.firmware): the connected firmware client ---

    def device_conn_id(self) -> str | None:
        """The current connection's id when it is a firmware client."""
        conn = self._conn
        return conn.conn_id if conn is not None and conn.shell == "firmware" else None

    async def send_device_event(self, conn_id: str, event: str, data: dict[str, Any]) -> bool:
        """A `server_event` to connection `conn_id`, if it is still the
        current one. True when it was written."""
        conn = self._conn
        if conn is None or conn.conn_id != conn_id:
            return False
        written = await self._send_raw({"type": "server_event", "event": event, "data": data})
        return written > 0

    async def close_device(self, conn_id: str, code: int, reason: str) -> bool:
        """Close connection `conn_id` with `code`, if it is still the
        current one (its `client_disconnected` line carries the code)."""
        conn = self._conn
        if conn is None or conn.conn_id != conn_id or conn.ws is None:
            return False
        conn.close_code, conn.close_reason = code, reason
        await conn.ws.close(code, reason)
        return True

    def _health_fields(self) -> dict[str, object]:
        """The server's part of `/health`: the current client (or the last
        one, `connected: false`), when a firmware client was last
        connected, and whether the persona's recorder is attached."""
        conn = self._conn
        shown = (
            conn if conn is not None else (self._recent_conns[-1] if self._recent_conns else None)
        )
        client: dict[str, object] | None = None
        if shown is not None:
            connected = shown is conn
            since_ms = shown.connected_at_ms
            if not connected and shown.closed_at_ms is not None:
                since_ms = shown.closed_at_ms
            client = {
                "connected": connected,
                "conn_id": shown.conn_id,
                "audio_format": shown.audio_format,
                "shell": shown.shell,
                "since": iso_utc(since_ms),
            }
        firmware_ms = self._firmware_last_seen_ms
        if conn is not None and conn.shell == "firmware":
            firmware_ms = _now_ms()  # connected right now
        return {
            "client": client,
            "walkie_last_seen": iso_utc(firmware_ms) if firmware_ms is not None else None,
            "recording": self._recorder is not None,
        }

    def _note_device(self, event: str, data: dict[str, Any]) -> None:
        """Remember what kind of client the current connection is, from the
        device event every client sends after `hello`: `device` with its
        `shell`, or the hardware contract's `device.hello` (firmware only;
        docs/protocol.md "Device contract")."""
        conn = self._conn
        if conn is None:
            return
        if event == "device.hello":
            shell = "firmware"
        elif event == "device" and isinstance(data.get("shell"), str):
            shell = str(data["shell"])
        else:
            return
        if shell == "firmware" and not _may_be_firmware(conn):
            return  # said in `_firmware_client_event`
        conn.shell = shell
        if shell == "firmware":
            self._firmware_last_seen_ms = _now_ms()

    def _note_client_gone(self, conn: _ConnInfo) -> None:
        """A connection closed or was evicted: a firmware client was last
        seen when it left."""
        if conn.shell == "firmware" and conn.closed_at_ms is not None:
            self._firmware_last_seen_ms = conn.closed_at_ms

    async def _handle_connection(self, ws: ServerConnection) -> None:
        # T1.21 — per-connection diagnostic id. Short hex prefix is
        # plenty unique within a session and easy to type when grepping
        # the log. Echoed in `hello` so the PWA's diag panel renders
        # the same id and structured log lines (which bind it via
        # contextvars below) can be filtered by the operator.
        conn_id = uuid.uuid4().hex[:8]
        access = self._connection_access(ws)
        if not await self._admit(ws, access, conn_id):
            return
        while self._conn is not None:
            # Evict the old client rather than rejecting the new one.
            # A new connection means either a browser reload or a freshly-
            # booted device — both should take priority over a stale socket.
            # A loop, re-checked after each await: another connection (the
            # walkie) can become current while the old socket closes, and
            # must then be evicted too, or refused if this one may not.
            old_conn = self._conn
            await logger.ainfo(
                "client_evicted",
                old=old_conn.remote,
                old_conn_id=old_conn.conn_id,
                new=str(ws.remote_address),
                new_conn_id=conn_id,
            )
            old_conn.close_reason = "evicted_by_new_client"
            old_conn.close_code = 1001
            old_conn.closed_at_ms = _now_ms()
            self._note_client_gone(old_conn)
            self._recent_conns.append(old_conn)
            self._conn = None
            self._ptt_active = False
            if self._recorder is not None:
                self._recorder.connection_closed(old_conn.conn_id, "evicted")
            with contextlib.suppress(Exception):
                await old_conn.ws.close(1001, "Replaced by new client") if old_conn.ws else None
            if not await self._admit(ws, access, conn_id):
                return

        # Pull the language and persona the client requested in its URL
        # and hand each to the respective callback. Persona-select runs
        # FIRST so the runtime can swap to the requested persona BEFORE we
        # serialize hello; otherwise the hello would carry the old
        # persona's name. Read from this connection's own request: the
        # fields `_process_request` parks are shared by every handshake,
        # and two can interleave (the walkie and a PWA after a restart).
        selected_language, selected_persona, requested_audio = self._requested_params(ws)
        self._pending_language = None
        self._pending_persona = None
        self._pending_audio = None
        ignored_persona: str | None = None
        if access.role == ROLE_DEVICE and selected_persona is not None:
            # The device is pinned to the configured persona (decision 4).
            ignored_persona, selected_persona = selected_persona, None

        # Build the conn record + bind contextvars BEFORE the first
        # log line so every event in this handler carries `conn_id`
        # automatically (see logging.py — `merge_contextvars` is in
        # the shared processor chain). Operators grepping `conn_id=ab12cd34`
        # get every log entry from this connection in one filter.
        # `conn` is THIS handler's record. `self._conn` is whichever client
        # is current, and a newer connection can replace it while this
        # handler is still draining. Cleanup below must only touch `conn`.
        conn = _ConnInfo(
            conn_id=conn_id,
            remote=str(ws.remote_address),
            connected_at_ms=_now_ms(),
            requested_persona=selected_persona,
            requested_language=selected_language,
            ws=ws,
            role=access.role or ROLE_DEV,
        )
        # The audio format is fixed before the connection becomes current,
        # so every audio frame it ever gets (even one racing the hello) is
        # in the format the hello announces.
        opus_error: str | None = None
        if requested_audio == "opus":
            try:
                conn.opus_tx = OpusEncoderStream()
                conn.opus_rx = OpusDecoderStream()
                conn.audio_format = "opus"
            except OpusUnavailableError as exc:
                opus_error = str(exc)
        self._conn = conn
        if self._recorder is not None:
            self._recorder.connection_opened(conn_id, conn.audio_format)
        structlog.contextvars.bind_contextvars(conn_id=conn_id)
        await logger.ainfo(
            "client_connected",
            remote=str(ws.remote_address),
            language=selected_language,
            persona=selected_persona,
            audio_format=conn.audio_format,
            role=conn.role,
        )
        if access.role in (ROLE_DEVICE, ROLE_LOCAL):
            await logger.ainfo(
                "server.auth.accepted",
                role=access.role,
                source=access.source,
                token=access.token,
                ignored_persona=ignored_persona,
            )
        self._log_recording_skipped(conn)
        if self._no_client_drop_sends:
            # What was sent into the void while nobody was connected.
            await self._log_no_client_drops(time.monotonic())
        if opus_error is not None:
            await logger.awarning(
                "server.audio.opus_unavailable", reason=opus_error, fallback="pcm"
            )
        elif requested_audio not in (None, "opus", "pcm"):
            await logger.awarning(
                "server.audio.format_unsupported", requested=requested_audio, fallback="pcm"
            )
        # T1.13 — fire the persona-select callback BEFORE hello. The
        # runtime's swap algorithm runs here; on success, current_app
        # is the new persona by the time hello goes out. On failure,
        # the runtime keeps the previous current_app and logs; we
        # continue serving the connection on the un-swapped state
        # rather than dropping it (PWA can retry the picker).
        if self._on_persona_select is not None:
            try:
                # Pass language too — runtime threads it into the new
                # Application so the OpenAI session opens in the right
                # language from the start, avoiding a "default-then-
                # disconnect-and-reconnect" cascade that leaks IDLE
                # state to the new client. Critic round 3.
                await self._on_persona_select(selected_persona, selected_language)
            except Exception:
                await logger.aexception(
                    "persona_select_failed",
                    requested=selected_persona,
                    language=selected_language,
                )
        try:
            # Handshake: hello first, then current state + input mode
            # sync so a reconnecting client knows whether a claim is
            # already active on the server (if we land mid-call, the
            # client should jump straight to continuous-mic).
            hello_payload: dict[str, object] = {
                "type": "hello",
                "protocol": PROTOCOL_VERSION,
                "language": selected_language,
                # T1.21 — server-generated per-connection id, echoed
                # so the PWA can render it in its diag panel and
                # cross-reference server logs (which bind it via
                # contextvars).
                "conn_id": conn_id,
            }
            # T1.13 — additive hello fields: current_persona +
            # available_personas. Wired via `get_hello_extras` so the
            # runtime owns the truth (we don't re-read the filesystem
            # per connection). Old clients ignore unknown keys.
            if self._get_hello_extras is not None:
                try:
                    hello_payload.update(self._get_hello_extras())
                except Exception:
                    await logger.aexception("hello_extras_failed")
            # Only for a client that asked for opus and got it; a PCM
            # client's hello stays exactly as it was.
            if conn.audio_format == "opus":
                hello_payload["audio_params"] = dict(AUDIO_PARAMS)
            await self._send_raw(hello_payload, _via_handshake=True)
            conn.hello_sent = True
            await self._send_raw({"type": "state", "value": self._state}, _via_handshake=True)
            await self._send_raw(
                {
                    "type": "input_mode",
                    "mode": self._input_mode,
                    "reason": "idle",
                    "claim_id": self._active_claim_id,
                },
                _via_handshake=True,
            )
            # Fire the language-select callback AFTER the handshake so the
            # app can reconcile the OpenAI session language with what the
            # client asked for. A reconnect may happen here (dropping the
            # old session and bringing up a new one in the chosen
            # language); we let it run to completion before reading any
            # client messages so the persona stays consistent with the
            # turns that follow.
            if self._on_language_select is not None:
                await self._on_language_select(selected_language)
            # Someone is listening again: paused content may resume. After
            # the handshake so its audio never precedes `hello`.
            if self._conn is conn:
                await self._notify_presence(True)
            async for raw in ws:
                if self._conn is not conn:
                    # Evicted while its close handshake drains (a backgrounded
                    # phone tab can take seconds). Its frames must not drive
                    # the app on behalf of the newer client.
                    await logger.ainfo("server.rx.dropped_from_evicted_client")
                    break
                await self._dispatch(raw)
        except websockets.ConnectionClosed as exc:
            # T1.21 — capture the close cause so the diag panel can
            # show "why" (1000 normal, 1001 going away, 4002 watchdog,
            # etc.) instead of the generic "client_disconnected".
            if self._conn is conn:
                conn.close_code = exc.code
                conn.close_reason = exc.reason or None
        finally:
            self._close_opus(conn)
            # T1.21 — finalize the conn record, archive into recent
            # history for the diag endpoint, then unbind contextvars.
            if self._conn is conn:
                self._ptt_active = False
                if self._recorder is not None:
                    self._recorder.connection_closed(conn_id, "disconnect")
                conn.closed_at_ms = _now_ms()
                self._note_client_gone(conn)
                conn.ws = None  # release ref for GC
                # If we got here without a ConnectionClosed exception
                # (e.g. handler raised something else), close_code stays
                # None — that's a useful diagnostic signal in itself.
                self._recent_conns.append(conn)
                await logger.ainfo(
                    "client_disconnected",
                    frames_tx=conn.frames_tx,
                    frames_rx=conn.frames_rx,
                    bytes_tx=conn.bytes_tx,
                    bytes_rx=conn.bytes_rx,
                    duration_ms=conn.closed_at_ms - conn.connected_at_ms,
                    close_code=conn.close_code,
                    close_reason=conn.close_reason,
                )
                self._conn = None
                await self._notify_presence(False)
            else:
                # Evicted earlier: the eviction path already archived and
                # logged this record. Leave `self._conn` alone. Before
                # 2026-10-10 this branch cleared whatever client was
                # current, so a slow-closing evicted tab silently cut off
                # the newer client's outbound audio.
                conn.ws = None
                await logger.ainfo("client_evicted_handler_exited")
            structlog.contextvars.unbind_contextvars("conn_id")

    async def _notify_presence(self, present: bool) -> None:
        """Tell the app whether a client is listening. Never raises: a
        failing handler must not break the connection's lifecycle."""
        if self._on_client_presence is None:
            return
        try:
            await self._on_client_presence(present)
        except Exception:
            await logger.aexception("server.client_presence_failed", present=present)

    async def _dispatch(self, raw: str | bytes) -> None:
        # T1.21 — frame-level rx accounting. Counted before any parse
        # so a malformed frame still shows up in the diag panel as
        # "1 rx" + a malformed log line, instead of silently disappearing.
        if self._conn is not None:
            raw_len = len(raw) if isinstance(raw, bytes | bytearray) else len(raw.encode())
            self._conn.frames_rx += 1
            self._conn.bytes_rx += raw_len
            self._conn.last_rx_at_ms = _now_ms()
            # An opus client's binary message is one Opus packet of mic
            # audio. Any other client's binary keeps today's handling.
            if self._conn.opus_rx is not None and not isinstance(raw, str):
                await self._on_opus_packet(self._conn, self._conn.opus_rx, bytes(raw))
                return
        try:
            msg: dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            await logger.awarning("server.rx.malformed")
            return
        # T1.21 — per-type histogram (after parse so we know the type).
        if self._conn is not None:
            msg_type_for_hist = msg.get("type")
            if isinstance(msg_type_for_hist, str):
                self._conn.rx_types[msg_type_for_hist] = (
                    self._conn.rx_types.get(msg_type_for_hist, 0) + 1
                )
            # Production: the device's token buys the firmware's messages
            # only (no admin, no sessions, no persona swap). `?lang=` at
            # connect still applies to it.
            if self._conn.role == ROLE_DEVICE and not (
                isinstance(msg_type_for_hist, str) and msg_type_for_hist in DEVICE_MESSAGE_TYPES
            ):
                self._refuse_for_role(self._conn, msg_type_for_hist)
                return
        match msg.get("type"):
            case "audio":
                pcm = base64.b64decode(msg.get("data", ""))
                await self._on_audio_frame(pcm)
            case "ptt_start":
                # A press starts a new Opus stream from the client: no
                # decoder state carries over from the last one.
                if self._conn is not None and self._conn.opus_rx is not None:
                    self._conn.opus_rx.reset()
                self._ptt_active = True
                if self._recorder is not None:
                    self._recorder.ptt(True)
                await logger.ainfo("server.rx.ptt_start")
                await self._on_ptt_start()
            case "ptt_stop":
                self._ptt_active = False
                if self._recorder is not None:
                    self._recorder.ptt(False)
                await logger.ainfo("server.rx.ptt_stop")
                await self._on_ptt_stop()
            case "wake_word":
                await logger.ainfo("server.rx.wake_word")
                await self._on_wake_word()
            case "reset":
                await logger.ainfo("server.rx.reset")
                await self._on_reset()
            case "set_language":
                # In-session language switch. The app's callback
                # typically drops the current OpenAI session and
                # reconnects with the new language so tools,
                # system_prompt, and transcription language all
                # flip together. Browser UIs usually prefer to
                # reconnect the WebSocket with `?lang=` instead
                # (cleaner and it forces a fresh state sync), but
                # this message exists for clients that don't want
                # to drop the transport.
                requested = msg.get("language")
                language = (
                    str(requested).strip().lower()
                    if isinstance(requested, str) and requested.strip()
                    else None
                )
                await logger.ainfo("server.rx.set_language", language=language)
                if self._on_language_select is not None:
                    await self._on_language_select(language)
            case "list_sessions":
                # T1.12 — PWA opens the SessionsSheet. App responds via
                # `send_sessions_list` with the persisted history rows.
                await logger.ainfo("server.rx.list_sessions")
                if self._on_list_sessions is not None:
                    await self._on_list_sessions()
            case "get_session":
                # T1.12 — PWA clicks a session preview. App responds via
                # `send_session_detail` with the full transcript.
                raw_id = msg.get("id")
                if not isinstance(raw_id, int):
                    await logger.awarning("server.rx.get_session.bad_id", raw=raw_id)
                    return
                await logger.ainfo("server.rx.get_session", session_id=raw_id)
                if self._on_get_session is not None:
                    await self._on_get_session(raw_id)
            case "delete_session":
                # T1.12 — privacy floor. PWA's SessionDetailSheet "Delete"
                # button. App responds via `send_session_deleted` once
                # the row + its turns are gone.
                raw_id = msg.get("id")
                if not isinstance(raw_id, int):
                    await logger.awarning("server.rx.delete_session.bad_id", raw=raw_id)
                    return
                await logger.ainfo("server.rx.delete_session", session_id=raw_id)
                if self._on_delete_session is not None:
                    await self._on_delete_session(raw_id)
            case "get_skills_state":
                # Marketplace v2 Phase A — PWA opens the Skills section
                # in DeviceSheet. Runtime builds the payload (entry
                # points + persona enabled-block + secrets dir) and
                # responds via `send_skills_state`.
                await logger.ainfo("server.rx.get_skills_state")
                if self._on_get_skills_state is not None:
                    await self._on_get_skills_state()
            case "get_marketplace":
                # Marketplace v2 Phase C — PWA opens the Marketplace
                # tab. Runtime fetches the registry feed (cached for
                # 1h) + decorates with installed-status, replies via
                # `send_marketplace_state`.
                await logger.ainfo("server.rx.get_marketplace")
                if self._on_get_marketplace is not None:
                    await self._on_get_marketplace()
            case "restart_server":
                # Marketplace v2 Phase D — graceful shutdown + os.execv.
                # Two callsites: the DeviceSheet Maintenance "Restart
                # server" button (manual op trigger), and the
                # install-completion path which auto-restarts after
                # `uv add` succeeds so the new entry-point is visible.
                # The handler returns immediately — the actual restart
                # happens after run()'s normal shutdown sequence
                # completes, so the response can flush before exec.
                await logger.ainfo("server.rx.restart_server")
                if self._on_restart_server is not None:
                    await self._on_restart_server()
            case "install_skill":
                # Marketplace v2 Phase D — install a `huxley-skill-*`
                # package via `uv add`. Runtime validates the package
                # name + mid-call gate, runs the subprocess, and on
                # success triggers a restart so the running interpreter
                # picks up the new entry point.
                package = msg.get("package")
                if not isinstance(package, str) or not package:
                    await logger.awarning(
                        "server.rx.install_skill.bad_args",
                        package=package,
                    )
                    return
                await logger.ainfo(
                    "server.rx.install_skill",
                    package=package,
                )
                if self._on_install_skill is not None:
                    await self._on_install_skill(package)
            case "set_skill_enabled":
                # Marketplace v2 Phase B — toggle enable/disable. The
                # PWA's SkillConfigSheet header switch sends this when
                # the user flips the toggle. Runtime mutates persona.yaml
                # (ruamel round-trip preserves comments) and reloads.
                skill_name = msg.get("skill")
                enabled = msg.get("enabled")
                if not isinstance(skill_name, str) or not isinstance(enabled, bool):
                    await logger.awarning(
                        "server.rx.set_skill_enabled.bad_args",
                        skill=skill_name,
                        enabled=enabled,
                    )
                    return
                await logger.ainfo(
                    "server.rx.set_skill_enabled",
                    skill=skill_name,
                    enabled=enabled,
                )
                if self._on_set_skill_enabled is not None:
                    await self._on_set_skill_enabled(skill_name, enabled)
            case "set_skill_config":
                # Marketplace v2 Phase B — replace a skill's config
                # block. The PWA's SkillConfigSheet "Save" button
                # sends the full edited block (replace, not merge).
                # Secret keys never appear in the payload — those go
                # through set_skill_secret to a different on-disk
                # location (values.json, not persona.yaml).
                skill_name = msg.get("skill")
                config = msg.get("config")
                if not isinstance(skill_name, str) or not isinstance(config, dict):
                    await logger.awarning(
                        "server.rx.set_skill_config.bad_args",
                        skill=skill_name,
                    )
                    return
                await logger.ainfo(
                    "server.rx.set_skill_config",
                    skill=skill_name,
                    keys=sorted(config.keys()),
                )
                if self._on_set_skill_config is not None:
                    await self._on_set_skill_config(skill_name, config)
            case "set_skill_secret":
                # Marketplace v2 Phase B — write a single secret key
                # to <persona>/data/secrets/<skill>/values.json. The
                # PWA's secret-input "Save" button sends this; the
                # value rides the WS but never touches persona.yaml.
                # Logged WITHOUT the value (key only) to keep the
                # server log free of credentials.
                skill_name = msg.get("skill")
                key = msg.get("key")
                value = msg.get("value")
                if (
                    not isinstance(skill_name, str)
                    or not isinstance(key, str)
                    or not isinstance(value, str)
                ):
                    await logger.awarning(
                        "server.rx.set_skill_secret.bad_args",
                        skill=skill_name,
                        key=key,
                    )
                    return
                await logger.ainfo(
                    "server.rx.set_skill_secret",
                    skill=skill_name,
                    key=key,
                )
                if self._on_set_skill_secret is not None:
                    await self._on_set_skill_secret(skill_name, key, value)
            case "delete_skill_secret":
                # Marketplace v2 Phase B — remove a secret key. The
                # PWA's secret-input "Clear" button sends this.
                skill_name = msg.get("skill")
                key = msg.get("key")
                if not isinstance(skill_name, str) or not isinstance(key, str):
                    await logger.awarning(
                        "server.rx.delete_skill_secret.bad_args",
                        skill=skill_name,
                        key=key,
                    )
                    return
                await logger.ainfo(
                    "server.rx.delete_skill_secret",
                    skill=skill_name,
                    key=key,
                )
                if self._on_delete_skill_secret is not None:
                    await self._on_delete_skill_secret(skill_name, key)
            case "get_diag":
                # T1.21 — PWA diagnostic panel asks for a snapshot of
                # current server state + recent connection history.
                # Bounded payload (recent_conns capped at
                # _DIAG_RECENT_CONNS_CAP); always responds even if no
                # `_conn` (would mean the connection died between rx
                # and reply, but that's a useful diagnostic signal).
                await logger.ainfo("server.rx.get_diag")
                await self._send_diag_state()
            case "ping":
                # T1.21 liveness — the PWA emits a `ping` periodically;
                # server replies `pong` with the original timestamp so
                # the client can compute round-trip latency. Unrelated
                # to WebSocket-protocol-level ping; that's at the
                # transport layer and the browser owns it.
                ts = msg.get("ts")
                await self._send_raw({"type": "pong", "ts": ts, "server_ts": _now_ms()})
            case "client_event":
                # Two consumers in parallel:
                # 1. Telemetry sink — every client_event is logged as
                #    `client.<event>` so the dev workflow's
                #    "describe symptom → read log" loop works for
                #    client-side bugs (UI state, audio queue, silence
                #    timer, thinking tone events that the server log
                #    can't otherwise see).
                # 2. Skill dispatch — any skill that registered a
                #    handler via `ctx.subscribe_client_event(key, ...)`
                #    receives the parsed `data` dict. Multiple skills
                #    can subscribe to the same key; all run concurrently,
                #    exception-isolated.
                # Both happen unconditionally, regardless of whether
                # any skill is subscribed; the telemetry log is
                # cheap and useful even for events with no listener.
                event = str(msg.get("event", "unknown"))
                raw_data = msg.get("data")
                data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
                self._note_device(event, data)
                await logger.ainfo(f"client.{event}", **client_event_log_fields(data))
                await self._firmware_client_event(event, data)
                await self._dispatch_client_event(event, data)
            case other:
                await logger.awarning("server.rx.unknown", msg_type=other)

    async def _on_opus_packet(
        self, conn: _ConnInfo, decoder: OpusDecoderStream, packet: bytes
    ) -> None:
        """Decode one inbound Opus packet and hand the PCM16 on exactly
        where a JSON `audio` message's PCM goes. A packet libopus rejects
        is logged and dropped; the connection stays up.

        Recorded as it arrived, before decoding or any gating, so a
        packet that fails to decode and a press that never commits are
        in the recording too."""
        conn.rx_types["opus"] = conn.rx_types.get("opus", 0) + 1
        if self._recorder is not None:
            self._recorder.mic_packet(conn.conn_id, packet)
        try:
            pcm = decoder.decode(packet)
        except Exception as exc:
            conn.opus_rx_errors += 1
            await logger.awarning(
                "server.rx.opus_decode_failed",
                packet_bytes=len(packet),
                error=f"{type(exc).__name__}: {exc}",
                errors=conn.opus_rx_errors,
            )
            return
        await self._on_audio_frame(pcm)

    # --- Server → client ---

    # Maximum PCM bytes per outbound `audio` message. Larger inputs
    # (most often: a buffered OpenAI Realtime audio delta of 1-3 seconds
    # of speech) get split into multiple WS messages.
    #
    # Why: a single `await ws.send(huge_payload)` keeps this connection's
    # asyncio task busy for the duration of the write — it cannot
    # interleave with the recv loop that processes mic frames from the
    # firmware. Big chunks were causing the ESP32 client to wedge on
    # outbound writes during long replies because the server stopped
    # acking inbound TCP for ~hundreds of ms while it pumped a 130 KB
    # base64-encoded audio chunk. They were also blowing the firmware's
    # WS fragment reassembler ceiling (firmware/docs/triage.md F-0012).
    #
    # 12 KB PCM ≈ 250 ms of 24 kHz mono — comfortably below the firmware's
    # decode scratch and below typical WS buffer sizes on every client.
    # Smaller would mean more JSON/base64 overhead per byte; larger
    # restarts the bursts-too-big problem. A single `asyncio.sleep(0)`
    # between chunks yields the event loop so the recv side gets a
    # chance to run, even when the chunks would otherwise queue
    # back-to-back inside the websockets library.
    _AUDIO_CHUNK_BYTES = 12 * 1024

    # Opus only: how long no new PCM means "this sound is over", so its
    # partial frame goes out zero-padded (an earcon nothing follows).
    # Above the 100 ms cadence of the real-time streams (audiobooks,
    # radio, Spotify all send 100 ms chunks): at ~100 ms the timer would
    # race every chunk. Replies and streams don't use it at all:
    # `model_speaking: false` and `stream_ended` flush at once, and no
    # timer runs while a stream is announced, so a source that stalls
    # never gets silence padded into it.
    _OPUS_IDLE_FLUSH_S = 0.25

    async def send_audio(self, pcm: bytes, *, stats: bool = True) -> None:
        """`stats=False` for the coordinator's waiting cue (T1.41): its
        blips are not a source falling behind, so they stay out of
        `server.tx.audio_stats` (the caregiver alerts read it)."""
        if not pcm:
            return
        conn = self._conn
        if conn is None or conn.ws is None:
            await self._audio_dropped_no_client(len(pcm))
            return
        if conn.opus_tx is not None:
            if conn.hello_sent:
                await self._send_audio_opus(conn, conn.opus_tx, pcm, stats=stats)
            else:
                await logger.adebug("server.tx.opus_before_hello_dropped", pcm_bytes=len(pcm))
            return
        for offset in range(0, len(pcm), self._AUDIO_CHUNK_BYTES):
            # `_send` goes to whichever client is current. If an opus
            # client took over mid-send (the walkie reconnecting), the
            # rest of this PCM must not reach it as JSON audio.
            current = self._conn
            if current is not None and current.opus_tx is not None:
                return
            chunk = pcm[offset : offset + self._AUDIO_CHUNK_BYTES]
            t0 = time.monotonic()
            wire = await self._send({"type": "audio", "data": base64.b64encode(chunk).decode()})
            if stats:
                await self._tx_audio_stats(
                    len(chunk), (time.monotonic() - t0) * 1000, wire_bytes=wire
                )
            # Yield so the recv loop and any other tasks can interleave
            # — keeps the WS bidirectional even mid-burst.
            await asyncio.sleep(0)

    async def _audio_dropped_no_client(self, pcm_bytes: int) -> None:
        """Audio for nobody: no client is connected, so it goes nowhere.
        Counted and logged (rate-limited), never silent: a source still
        playing with nobody listening shows up in the log."""
        self._no_client_drop_sends += 1
        self._no_client_drop_bytes += pcm_bytes
        now = time.monotonic()
        if now - self._no_client_drop_logged_s >= _NO_CLIENT_DROP_LOG_S:
            await self._log_no_client_drops(now)
        else:
            # The send path yields once per chunk; keep a producer that
            # does not pace itself from holding the loop.
            await asyncio.sleep(0)

    async def _log_no_client_drops(self, now: float) -> None:
        count, pcm_bytes = self._no_client_drop_sends, self._no_client_drop_bytes
        self._no_client_drop_sends = self._no_client_drop_bytes = 0
        self._no_client_drop_logged_s = now
        await logger.ainfo(
            "server.tx.audio_dropped_no_client",
            count=count,
            audio_ms=pcm_bytes // _PCM_BYTES_PER_MS,
        )

    async def _send_audio_opus(
        self, conn: _ConnInfo, encoder: OpusEncoderStream, pcm: bytes, *, stats: bool = True
    ) -> None:
        """Encode whole 60 ms frames now, keep the remainder for the next
        call (or a flush), and write the packets."""
        conn.opus_last_push_s = time.monotonic()
        self._cancel_opus_idle_flush(conn)
        try:
            conn.opus_out.extend(encoder.push(pcm))
        except Exception as exc:
            await self._opus_encode_failed(conn, exc, pcm_bytes=len(pcm))
            return
        await self._drain_opus(conn, pcm_bytes=len(pcm), stats=stats)
        # Armed after the writes: a sender stuck in flow control is not
        # idle. Not while a stream is announced (see _OPUS_IDLE_FLUSH_S).
        if (
            encoder.pending_bytes
            and self._conn is conn
            and conn.opus_tx is encoder
            and self._active_stream_id is None
        ):
            self._arm_opus_idle_flush(conn)

    async def _drain_opus(self, conn: _ConnInfo, *, pcm_bytes: int, stats: bool = True) -> None:
        """Write queued packets, one binary message each, in queue order.

        Every sender drains the one shared queue: whoever runs pops the
        next packet, and `ws.send` writes the frame before it waits on
        flow control, so the wire order is the encoder order however
        many tasks send audio at once. Same pacing as the PCM path: each
        send waits out websockets' backpressure, then yields.

        Cancelled mid-way (a barge-in or a pause cancels the task sending
        a sound), the sound was cut: what it still had queued is dropped
        and the encoder reset, as a cancelled PCM send's other chunks
        never go out. Otherwise a pause with no `audio_clear` would play
        the leftover ahead of the next sound."""
        sends = wire = 0
        wait_ms = 0.0
        try:
            while conn.opus_out and self._conn is conn and conn.ws is not None:
                packet = conn.opus_out.popleft()
                # Recorded here, in wire order: `ws.send` writes the frame
                # before its first await (it waits on flow control after
                # the write), so nothing the server does can come between
                # this line and the write. Only a socket already closing
                # raises before writing: one packet at a disconnect.
                self._record_speaker(conn, packet)
                t0 = time.monotonic()
                await conn.ws.send(packet)
                wait_ms += (time.monotonic() - t0) * 1000
                sends += 1
                wire += len(packet)
                conn.frames_tx += 1
                conn.bytes_tx += len(packet)
                conn.last_tx_at_ms = _now_ms()
                conn.tx_types["opus"] = conn.tx_types.get("opus", 0) + 1
                await asyncio.sleep(0)
        except websockets.ConnectionClosed:
            conn.opus_out.clear()
        except asyncio.CancelledError:
            dropped_packets = len(conn.opus_out)
            conn.opus_out.clear()
            dropped_pcm = 0
            if conn.opus_tx is not None:
                with contextlib.suppress(Exception):
                    dropped_pcm = conn.opus_tx.reset()
            self._cancel_opus_idle_flush(conn)
            # Sync call: no await between a cancellation and its re-raise.
            logger.debug(
                "server.tx.opus_cancelled",
                dropped_packets=dropped_packets,
                dropped_pcm_ms=dropped_pcm // _PCM_BYTES_PER_MS,
            )
            raise
        if self._conn is conn and stats:
            await self._tx_audio_stats(pcm_bytes, wait_ms, sends=sends, wire_bytes=wire)

    def _record_speaker(self, conn: _ConnInfo, packet: bytes) -> None:
        """Hand a packet just written to the client to the recorder."""
        if self._recorder is not None:
            self._recorder.speaker_packet(conn.conn_id, packet)

    async def _flush_opus(self, conn: _ConnInfo | None, reason: str) -> None:
        """Send the partial frame zero-padded to 60 ms, so the end of a
        sound is not held back for PCM that is not coming. `reason`:
        `model_speaking` (reply over), `stream_ended`, `idle` (no PCM for
        `_OPUS_IDLE_FLUSH_S`)."""
        if conn is None or conn.opus_tx is None:
            return
        if reason == "idle" and time.monotonic() - conn.opus_last_push_s < self._OPUS_IDLE_FLUSH_S:
            return  # PCM came in after the timer fired: not idle after all
        self._cancel_opus_idle_flush(conn)
        tail_ms = conn.opus_tx.pending_bytes // _PCM_BYTES_PER_MS
        try:
            packet = conn.opus_tx.flush()
        except Exception as exc:
            await self._opus_encode_failed(conn, exc, pcm_bytes=0)
            return
        if packet is None:
            return
        conn.opus_out.append(packet)
        await self._drain_opus(conn, pcm_bytes=0)
        # One line per sound end: `pad_ms` of silence closed this sound.
        await logger.ainfo(
            "server.tx.opus_flush", reason=reason, tail_ms=tail_ms, pad_ms=FRAME_MS - tail_ms
        )

    def _arm_opus_idle_flush(self, conn: _ConnInfo) -> None:
        self._cancel_opus_idle_flush(conn)
        conn.opus_flush_timer = asyncio.get_running_loop().call_later(
            self._OPUS_IDLE_FLUSH_S, self._opus_idle_flush_due, conn
        )

    def _cancel_opus_idle_flush(self, conn: _ConnInfo) -> None:
        if conn.opus_flush_timer is not None:
            conn.opus_flush_timer.cancel()
            conn.opus_flush_timer = None

    def _opus_idle_flush_due(self, conn: _ConnInfo) -> None:
        conn.opus_flush_timer = None
        if self._conn is conn:
            # Referenced from `conn` so the task isn't collected mid-run.
            conn.opus_flush_task = asyncio.get_running_loop().create_task(
                self._flush_opus(conn, "idle")
            )

    async def _opus_encode_failed(
        self, conn: _ConnInfo, exc: Exception, *, pcm_bytes: int
    ) -> None:
        """libopus refused a frame. Drop what is pending and start the
        encoder fresh; the audio that follows still plays."""
        dropped = 0
        if conn.opus_tx is not None:
            with contextlib.suppress(Exception):
                dropped = conn.opus_tx.reset()
        await logger.awarning(
            "server.tx.opus_encode_failed",
            error=f"{type(exc).__name__}: {exc}",
            pcm_bytes=pcm_bytes,
            dropped_pcm_bytes=dropped,
        )

    def _close_opus(self, conn: _ConnInfo) -> None:
        """Connection over: stop its flush timer and release the codecs."""
        self._cancel_opus_idle_flush(conn)
        conn.opus_out.clear()
        conn.opus_tx = None
        conn.opus_rx = None

    async def _tx_audio_stats(
        self, pcm_bytes: int, wait_ms: float, *, sends: int = 1, wire_bytes: int = 0
    ) -> None:
        """Count outbound audio; once a second, log what went out.
        `audio_ms` is PCM duration handed to `send_audio`, whatever the
        encoding; `bytes_sent` is what went on the wire for it (JSON +
        base64 for PCM, Opus packets for opus: ~64 KB/s vs ~4 KB/s).
        `sends` counts WebSocket messages. `send_wait_ms` is time spent
        in `ws.send` (websockets waits there when its buffer is over the
        high-water mark: the network isn't keeping up); `sock_unsent` is
        what the kernel still holds. Audio sent at real time (`audio_ms`
        ~ `window_ms`) with a board that received less means the
        network; less sent means the source was late."""
        conn = self._conn
        if conn is None or conn.ws is None:
            return
        now = _now_ms()
        if conn.tx_audio_window_start_ms == 0:
            conn.tx_audio_window_start_ms = now
        conn.tx_audio_pcm_bytes += pcm_bytes
        conn.tx_audio_wire_bytes += wire_bytes
        conn.tx_audio_sends += sends
        conn.tx_audio_wait_ms += wait_ms
        conn.tx_audio_max_wait_ms = max(conn.tx_audio_max_wait_ms, wait_ms)
        window = now - conn.tx_audio_window_start_ms
        if window < _TX_STATS_WINDOW_MS:
            return
        await logger.ainfo(
            "server.tx.audio_stats",
            window_ms=window,
            audio_ms=conn.tx_audio_pcm_bytes // _PCM_BYTES_PER_MS,
            audio_format=conn.audio_format,
            bytes_sent=conn.tx_audio_wire_bytes,
            sends=conn.tx_audio_sends,
            send_wait_ms=round(conn.tx_audio_wait_ms),
            max_send_wait_ms=round(conn.tx_audio_max_wait_ms),
            write_buffer=conn.ws.transport.get_write_buffer_size(),
            sock_unsent=_sock_unsent(conn.ws),
        )
        conn.tx_audio_window_start_ms = now
        conn.tx_audio_pcm_bytes = conn.tx_audio_wire_bytes = conn.tx_audio_sends = 0
        conn.tx_audio_wait_ms = conn.tx_audio_max_wait_ms = 0.0

    async def send_state(self, state: str) -> None:
        self._state = state
        await logger.ainfo("server.tx.state", value=state)
        await self._send({"type": "state", "value": state})

    async def send_status(self, message: str) -> None:
        await logger.adebug("server.tx.status", message=message)
        await self._send({"type": "status", "message": message})

    async def send_transcript(self, role: str, text: str) -> None:
        await self._send({"type": "transcript", "role": role, "text": text})

    async def send_model_speaking(self, value: bool) -> None:
        if not value:
            # Opus: the reply's last partial frame goes out before the
            # client hears that the reply is over.
            await self._flush_opus(self._conn, "model_speaking")
        # After the flush: the padded tail belongs to what just ended.
        self._model_speaking = value
        if self._recorder is not None:
            self._recorder.model_speaking(value)
        await logger.ainfo("server.tx.model_speaking", value=value)
        await self._send({"type": "model_speaking", "value": value})

    async def send_sessions_list(self, sessions: list[dict[str, Any]]) -> None:
        """T1.12 — reply to inbound `list_sessions`. The app converts
        `Storage.SessionMeta` to plain dicts before calling so the
        server stays free of storage-domain types."""
        await logger.ainfo("server.tx.sessions_list", count=len(sessions))
        await self._send({"type": "sessions_list", "sessions": sessions})

    async def send_session_detail(self, session_id: int, turns: list[dict[str, Any]]) -> None:
        """T1.12 — reply to inbound `get_session`. Turns are pre-serialized
        dicts (see `send_sessions_list` rationale)."""
        await logger.ainfo(
            "server.tx.session_detail", session_id=session_id, turn_count=len(turns)
        )
        await self._send({"type": "session_detail", "id": session_id, "turns": turns})

    async def send_session_deleted(self, session_id: int) -> None:
        """T1.12 — confirm an inbound `delete_session` succeeded so the
        PWA can remove the row from its local list state."""
        await logger.ainfo("server.tx.session_deleted", session_id=session_id)
        await self._send({"type": "session_deleted", "id": session_id})

    async def send_marketplace_state(self, payload: dict[str, Any]) -> None:
        """Marketplace v2 Phase C — reply to inbound `get_marketplace`.

        `payload` is built by `huxley.marketplace.fetch_marketplace`
        and includes: `skills` (list of registry entries augmented
        with `installed: bool`), `registry_version`, `generated_at`,
        `fetched_at_ms`, `stale`, `error`."""
        skills = payload.get("skills") if isinstance(payload, dict) else None
        count = len(skills) if isinstance(skills, list) else 0
        await logger.ainfo(
            "server.tx.marketplace_state",
            count=count,
            stale=payload.get("stale"),
            error=payload.get("error"),
        )
        await self._send({"type": "marketplace_state", **payload})

    async def send_install_event(self, payload: dict[str, Any]) -> None:
        """Marketplace v2 Phase D — install lifecycle events.

        `payload` includes `kind` ("started" | "complete"), `package`,
        `ok: bool` (only meaningful on `kind=complete`), `error_code`,
        `error_message`, and `restart_required: bool`. The PWA branches
        on `kind` + `ok`; the human `error_message` goes to the UI,
        the `error_code` is for branching logic.

        Phase D planning critic §E.1 collapsed `install_progress` and
        `install_complete` into a single discriminated frame so future
        kinds (`queued`, `rolled_back`, etc.) slot in additively
        without new top-level frame types.
        """
        await logger.ainfo(
            "server.tx.install_event",
            kind=payload.get("kind"),
            package=payload.get("package"),
            ok=payload.get("ok"),
            error_code=payload.get("error_code"),
        )
        await self._send({"type": "install_event", **payload})

    async def send_skills_state(self, payload: dict[str, Any]) -> None:
        """Marketplace v2 Phase A — reply to inbound `get_skills_state`.

        `payload` is built by `huxley.skills_state.build_skills_state`
        and includes: `persona` (directory basename or None during lazy
        boot) and `skills` (list of per-skill records). Phase B adds
        push frames after writes; Phase A is request/response only."""
        skills = payload.get("skills") if isinstance(payload, dict) else None
        count = len(skills) if isinstance(skills, list) else 0
        await logger.ainfo("server.tx.skills_state", count=count)
        await self._send({"type": "skills_state", **payload})

    async def send_dev_event(self, kind: str, payload: dict[str, Any]) -> None:
        """Broadcast a dev-observability event for the dev UI to visualize.

        Additive channel on top of the production protocol. Production clients
        (ESP32) ignore unknown message types. See docs/protocol.md.
        """
        await self._send({"type": "dev_event", "kind": kind, "payload": payload})

    async def send_server_event(self, event: str, data: dict[str, Any] | None = None, /) -> None:
        """Push a generic `server_event` to the connected client.

        Symmetric counterpart to inbound `client_event`: same shape on
        the wire (`{type: server_event, event, data}`). No-op (with
        debug log) if no client is connected. Clients that don't
        recognize `server_event` log-and-ignore on their side, so emit
        is safe to call even without a per-client capability check —
        the worst case is a recipient that drops the message.
        """
        if self._conn is None:
            await logger.adebug("server.tx.server_event.no_client", event_name=event)
            return
        await self._send(
            {"type": "server_event", "event": event, "data": data if data is not None else {}}
        )

    # --- client_event subscription registry ---

    def register_client_event_subscriber(
        self,
        skill_name: str,
        key: str,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Register a skill's handler for a given `client_event` key.

        Called from `SkillContext.subscribe_client_event`. The
        registry is process-lifetime so subscriptions survive
        transient WebSocket disconnects (a skill subscribed at startup
        is still subscribed when the user reloads the PWA mid-session).
        """
        self._client_event_subs.setdefault(key, []).append((skill_name, handler))

    def unregister_client_event_subscribers(self, skill_name: str) -> None:
        """Remove every subscription owned by `skill_name`.

        Called from the framework's shutdown path immediately before
        `skill.teardown()` so a buggy teardown can't fire handlers
        that are about to be torn down. Idempotent.
        """
        for key in list(self._client_event_subs.keys()):
            kept = [(sk, h) for (sk, h) in self._client_event_subs[key] if sk != skill_name]
            if kept:
                self._client_event_subs[key] = kept
            else:
                del self._client_event_subs[key]

    def disable_client_event_dispatch(self) -> None:
        """Stop dispatching inbound `client_event` to skill subscribers.

        Called at the start of shutdown — BEFORE the per-skill
        unregister loop and BEFORE `teardown_all` — so a `client_event`
        arriving mid-shutdown can't fire a still-registered handler
        whose backing skill (or shared focus_manager / coordinator)
        is mid-stop. Inbound messages still receive the existing
        `client.<event>` telemetry log; only the skill-dispatch fan-out
        is gated. Idempotent.
        """
        self._dispatching_enabled = False

    async def _firmware_client_event(self, event: str, data: dict[str, Any]) -> None:
        """The device contract's update and console events, to the firmware
        service (framework plumbing, never a skill). Never raises into the
        receive loop."""
        conn = self._conn
        if self._firmware is None or conn is None:
            return
        if not _may_be_firmware(conn):
            # Production: only the device's own socket speaks for the
            # walkie. A local client's `device.hello` could otherwise end a
            # pending image's accept flow (it would then roll back).
            if event in ("device.hello", "firmware.status", "dev.cmd_result"):
                self._refuse_for_role(conn, f"client_event:{event}")
            return
        try:
            await self._firmware.on_client_event(conn.conn_id, event, data)
        except Exception:
            await logger.aexception("firmware.event_failed", event_name=event)

    async def _dispatch_client_event(self, event: str, data: dict[str, Any]) -> None:
        """Invoke every registered handler for `event` concurrently.

        Exception isolation: a handler raising must not block other
        handlers or affect the inbound message-dispatch loop. We use
        `asyncio.gather(return_exceptions=True)` so all results
        materialize, then walk results to log failures via
        `aexception` (preserving the traceback).

        **Snapshot before iterating.** A handler that calls
        `ctx.subscribe_client_event(<same key>, ...)` from inside its
        body would otherwise mutate the live list we're iterating —
        `setdefault(key, []).append(...)` returns the existing list
        when the key already exists. Without a snapshot the post-gather
        `zip(subs, results, strict=True)` raises ValueError because
        `len(subs) > len(results)`, that propagates up through
        `_dispatch` into the recv loop (which only catches
        `ConnectionClosed`), the recv loop dies, and the client gets
        evicted with no diagnostic. Snapshotting via `list(...)` is a
        ~5ns tuple copy that closes the entire class. (Round-3 review,
        2026-04-29.)

        **Gated by `_dispatching_enabled`.** During shutdown the
        framework flips this off BEFORE the per-skill unregister loop
        starts, so a `client_event` arriving mid-shutdown can't trigger
        a still-registered handler that would route through a
        half-stopped FocusManager / coordinator. Empty fast-path so the
        gate is free in steady state.
        """
        if not self._dispatching_enabled:
            return
        subs = list(self._client_event_subs.get(event, ()))
        if not subs:
            return
        results = await asyncio.gather(
            *(handler(data) for (_, handler) in subs),
            return_exceptions=True,
        )
        for (skill_name, _), result in zip(subs, results, strict=True):
            if isinstance(result, BaseException):
                await logger.aexception(
                    "client_event.handler_failed",
                    event_name=event,
                    skill=skill_name,
                    exc_info=result,
                )

    async def send_set_volume(self, level: int) -> None:
        await logger.ainfo("server.tx.set_volume", level=level)
        await self._send({"type": "set_volume", "level": level})

    async def send_input_mode(
        self,
        mode: str,
        *,
        reason: str,
        claim_id: str | None = None,
    ) -> None:
        """Tell the client to switch mic-streaming policy.

        Cached locally so a reconnecting client gets the current mode in
        the initial-sync burst (see `_handle_connection`). Emits a
        structured log because every mode flip is load-bearing for the
        "why did the call go silent?" debugging path.
        """
        if mode not in (INPUT_MODE_ASSISTANT_PTT, INPUT_MODE_SKILL_CONTINUOUS):
            msg = f"send_input_mode: unknown mode {mode!r}"
            raise ValueError(msg)
        self._input_mode = mode
        self._active_claim_id = claim_id
        await logger.ainfo(
            "server.tx.input_mode",
            mode=mode,
            reason=reason,
            claim_id=claim_id,
        )
        await self._send(
            {
                "type": "input_mode",
                "mode": mode,
                "reason": reason,
                "claim_id": claim_id,
            },
        )

    async def send_claim_started(
        self, claim_id: str, skill: str, title: str | None = None
    ) -> None:
        """Observability + UI message — lets the client render a
        "call connecting" indicator with a human-readable label.

        `title` carries the skill's display name for the claim (e.g.,
        the contact's name on a Telegram call). UIs show this instead
        of a generic status while `input_mode=skill_continuous`. Falls
        back to `null` when the skill didn't supply one; client
        renders a generic label in that case.

        The behavioral signal remains `input_mode=skill_continuous`;
        `title` is presentation-only.
        """
        self._claim_info = (claim_id, skill, title)
        if self._recorder is not None:
            self._recorder.claim_started(claim_id, skill, title)
        await logger.ainfo(
            "server.tx.claim_started",
            claim_id=claim_id,
            skill=skill,
            title=title,
        )
        await self._send(
            {
                "type": "claim_started",
                "claim_id": claim_id,
                "skill": skill,
                "title": title,
            },
        )

    async def send_claim_ended(self, claim_id: str, end_reason: str) -> None:
        """Observability message — claim has terminated for the given
        reason ("natural", "user_ptt", "preempted", "error"). The
        behavioral signal is `input_mode=assistant_ptt` fired
        immediately after.
        """
        if self._claim_info is not None and self._claim_info[0] == claim_id:
            self._claim_info = None
        if self._recorder is not None:
            self._recorder.claim_ended(claim_id, end_reason)
        await logger.ainfo("server.tx.claim_ended", claim_id=claim_id, end_reason=end_reason)
        await self._send(
            {"type": "claim_ended", "claim_id": claim_id, "end_reason": end_reason},
        )

    async def send_audio_clear(self) -> None:
        """Tell the client to immediately drop any queued audio.

        Fires on seek so the audiobook player can start at the new position
        without the client first playing the tail of the old queue.

        Opus: the partial frame and any packets not yet written are
        dropped and the encoder reset (OPUS_RESET_STATE) before the clear
        goes out, with no await in between, so nothing encoded from the
        cut-off audio follows the clear and the next sound starts from a
        fresh encoder.
        """
        conn = self._conn
        dropped_packets = dropped_pcm = 0
        if self._recorder is not None:
            self._recorder.audio_clear()
        if conn is not None and conn.opus_tx is not None:
            self._cancel_opus_idle_flush(conn)
            dropped_packets = len(conn.opus_out)
            conn.opus_out.clear()
            dropped_pcm = conn.opus_tx.reset()
        await self._send({"type": "audio_clear"})
        if conn is not None and conn.opus_tx is not None:
            await logger.adebug(
                "server.tx.opus_clear",
                dropped_packets=dropped_packets,
                dropped_pcm_ms=dropped_pcm // _PCM_BYTES_PER_MS,
            )

    async def send_stream_started(
        self, stream_id: str, label: str | None, preroll_ms: int = 0
    ) -> None:
        """Tell the client that a long-form audio stream has begun.

        Triggers the "playing" orb state and waveform visualizer. The
        `label` (e.g. "Don Quixote", "Radio Clasica") is shown in the
        status line. `stream_id` correlates with the matching
        `stream_ended` message. `preroll_ms` is the duration of any
        earcon/intro the stream factory yields before actual content;
        the client hides the waveform visualizer for that many ms.
        """
        # Before the log's await, so packets the stream's pump writes
        # meanwhile are already tagged with it.
        self._active_stream_id = stream_id
        self._active_stream_label = label
        if self._recorder is not None:
            self._recorder.stream_started(stream_id, label)
        await logger.ainfo(
            "server.tx.stream_started",
            stream_id=stream_id,
            label=label,
            preroll_ms=preroll_ms,
        )
        await self._send(
            {
                "type": "stream_started",
                "stream_id": stream_id,
                "label": label,
                "preroll_ms": preroll_ms,
            }
        )

    async def send_stream_ended(self, stream_id: str, end_reason: str) -> None:
        """Tell the client that the long-form audio stream has ended.

        `end_reason`: "natural" (played to completion) or "interrupted"
        (user PTT, new tool call, or session reset).
        """
        # Opus: the stream's last partial frame goes out before the end.
        await self._flush_opus(self._conn, "stream_ended")
        # Right after the flush: its padded tail is the stream's last
        # packet; whatever plays from here on is not the stream.
        if self._recorder is not None:
            self._recorder.stream_ended(stream_id, end_reason)
        await logger.ainfo("server.tx.stream_ended", stream_id=stream_id, end_reason=end_reason)
        if self._active_stream_id == stream_id:
            self._active_stream_id = None
            self._active_stream_label = None
        await self._send(
            {"type": "stream_ended", "stream_id": stream_id, "end_reason": end_reason},
        )

    async def _send_diag_state(self) -> None:
        """T1.21 — reply to a `get_diag` frame with a snapshot of
        observability state.

        Payload:
        - `now_ms`: server wall clock so the PWA can compute clock
          skew (server vs `Date.now()`). Critical when correlating
          client-side log timestamps with server log timestamps.
        - `current`: the live `_ConnInfo` (or null if between connects).
        - `recent`: bounded history of recently-closed connections,
          oldest-first. Lets the operator see "did this connect get
          evicted" at a glance.
        - `state`: app state, input mode, claim/stream ids, eviction-
          relevant bits. Mirrors what the existing `state` and
          `input_mode` frames carry but in one snapshot.
        - `runtime`: optional dict from runtime hook (current_app
          persona, skills count, install in progress) — pulled via
          `get_hello_extras` since it's the same data surface.
        """
        runtime_extras: dict[str, object] = {}
        if self._get_hello_extras is not None:
            try:
                runtime_extras = self._get_hello_extras()
            except Exception:
                # Don't crash the diag reply over a runtime hiccup —
                # the diag is for debugging, not for breaking things.
                await logger.aexception("diag.runtime_extras_failed")
        await self._send_raw(
            {
                "type": "diag_state",
                "now_ms": _now_ms(),
                "current": self._conn.to_diag() if self._conn is not None else None,
                "recent": [c.to_diag() for c in self._recent_conns],
                "state": {
                    "app_state": self._state,
                    "input_mode": self._input_mode,
                    "active_claim_id": self._active_claim_id,
                    "active_stream_id": self._active_stream_id,
                    "dispatching_enabled": self._dispatching_enabled,
                },
                "runtime": runtime_extras,
            }
        )

    async def _send(self, msg: dict[str, Any]) -> int:
        """Public send used by every `send_*` callsite. Goes through
        `_send_raw` so frame counters / type histograms / last-tx
        timestamps update for every TX, including the ones from skills
        (`server_event`, `set_volume`, etc.). Pre-T1.21 this was the
        only send path; now `_send_raw` handles the actual write so
        the handshake's pre-dispatch sends stay accounted for too.
        Returns the bytes written (0 if dropped).
        """
        return await self._send_raw(msg)

    async def _send_raw(
        self,
        msg: dict[str, Any],
        *,
        _via_handshake: bool = False,
    ) -> int:
        """T1.21 — single chokepoint for every outbound JSON frame (opus
        audio packets go through `_drain_opus`, same accounting).

        - Updates `_conn` frame counters + `tx_types` histogram +
          `last_tx_at_ms` so the diag panel + `client_disconnected`
          summary log line have a correct count.
        - Drops the frame silently if no `_conn` (e.g. between an
          eviction and the new connection's hello). Pre-T1.21 the
          equivalent silent-drop was at `if self._client is None: return`.
        - Suppresses `ConnectionClosed` so a race between dispatch
          deciding to send and the WS dying mid-frame doesn't crash
          the handler.

        `_via_handshake=True` is for the three sends inside
        `_handle_connection` BEFORE the dispatch loop starts; counted
        the same way but documented because they're written before
        any inbound message has hit `_dispatch`.

        Returns the bytes written, 0 when the frame was dropped.
        """
        del _via_handshake  # documentation only; counted normally
        conn = self._conn
        if conn is None or conn.ws is None:
            return 0
        encoded = json.dumps(msg)
        with contextlib.suppress(websockets.ConnectionClosed):
            await conn.ws.send(encoded)
            conn.frames_tx += 1
            conn.bytes_tx += len(encoded)
            conn.last_tx_at_ms = _now_ms()
            msg_type = msg.get("type")
            if isinstance(msg_type, str):
                conn.tx_types[msg_type] = conn.tx_types.get(msg_type, 0) + 1
            return len(encoded)
        return 0

"""WebSocket audio server — the single interface point for all clients.

One grandpa client (browser, ESP32, future hardware) connects at a time
on the default path. The client owns all audio I/O (mic capture, speaker
playback). This server owns the OpenAI session, tool dispatch, state
machine, and storage.

T1.4 Stage 2 commit 4 adds **call routes** on the same port:

- `GET /call/ring?from=<name>` — out-of-band trigger that fires the
  calls skill's ring handler. Used by Mario's web app to ring grandpa.
  Authenticates via `X-Shared-Secret` header. Returns 200 if the ring
  was accepted, 409 if grandpa is already on a call.
- `WS  /call?secret=<secret>` — caller's WebSocket. Same shared-secret
  auth via query param (browsers can't easily set headers on WS
  upgrade). The CallsSkill takes ownership of the connection from
  there; PCM relay between this WS and grandpa's default WS happens
  inside the skill.

Both endpoints are optional — `on_ring` and `on_caller_connected` are
constructor params; if either is None, the corresponding route returns
503. AudioServer doesn't know about "calls" — it just owns the routing
substrate. The skill registers the hooks via Application wiring.

Protocol — grandpa client -> server (path "/")
    {"type": "audio",     "data": "<base64 PCM16 24 kHz>"}
    {"type": "ptt_start"}
    {"type": "ptt_stop"}
    {"type": "wake_word"}
    {"type": "reset"}                     # dev: disconnect + fresh OpenAI session

Protocol — server -> grandpa client
    {"type": "hello",          "protocol": PROTOCOL_VERSION}  # first message
    {"type": "audio",          "data": "<base64 PCM16 24 kHz>"}
    {"type": "state",          "value": "IDLE"|"CONNECTING"|"CONVERSING"}
    {"type": "status",         "message": "..."}
    {"type": "transcript",     "role": "user"|"assistant", "text": "..."}
    {"type": "model_speaking", "value": bool}
    {"type": "set_volume",     "level": int}  # 0-100, client-controlled
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import structlog
import websockets
import websockets.http11
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

# Localhost cookie jars from dev tools (React DevTools, HMR clients, etc.) can
# push the WebSocket upgrade's Cookie header past websockets v16's default 8 KB
# line limit, causing all handshakes to fail with SecurityError("line too long").
# Bump to 64 KB — more than enough for any real header, still bounded.
websockets.http11.MAX_LINE_LENGTH = 64 * 1024

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger()

PROTOCOL_VERSION = 1


class AudioServer:
    """WebSocket server: one audio client at a time.

    Clients connect, exchange PCM audio and control events, and disconnect.
    A second connection attempt while a client is active is rejected with
    1008 Policy Violation.
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
        # T1.4 Stage 2 commit 4 — optional call routes. If both are None,
        # the `/call/ring` and `/call` endpoints return 503.
        on_ring: Callable[[dict[str, str]], Awaitable[bool]] | None = None,
        on_caller_connected: Callable[[ServerConnection], Awaitable[None]] | None = None,
        ring_secret: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_wake_word = on_wake_word
        self._on_ptt_start = on_ptt_start
        self._on_ptt_stop = on_ptt_stop
        self._on_audio_frame = on_audio_frame
        self._on_reset = on_reset
        self._on_ring = on_ring
        self._on_caller_connected = on_caller_connected
        self._ring_secret = ring_secret
        self._client: ServerConnection | None = None
        self._state = "IDLE"

    @property
    def has_client(self) -> bool:
        return self._client is not None

    async def run(self) -> None:
        async with serve(
            self._handle_connection,
            self._host,
            self._port,
            process_request=self._process_request,
        ):
            await logger.ainfo(
                "audio_server_listening",
                url=f"ws://{self._host}:{self._port}",
                calls_enabled=self._on_ring is not None,
            )
            await asyncio.Future()  # run until cancelled

    async def _process_request(
        self,
        connection: ServerConnection,
        request: Request,
    ) -> Response | None:
        """Pre-handshake hook. Intercept HTTP-only routes; let WebSocket
        upgrades fall through with `None`.

        websockets v16 rejects non-GET requests at parse time — only GET
        ever reaches this hook. That's why `/call/ring` is documented as
        GET (with auth via header), not POST. Functionally the same for
        an internal trigger endpoint.
        """
        _ = connection  # signature requirement; we don't need it here
        # urlsplit because request.path includes the query string.
        split = urlsplit(request.path)
        path = split.path
        if path == "/call/ring":
            return await self._handle_ring_request(request, query=split.query)
        # All other paths (default "/" and "/call") proceed to the WS
        # handshake. Routing happens in `_handle_connection`.
        return None

    async def _handle_ring_request(
        self,
        request: Request,
        *,
        query: str,
    ) -> Response:
        """HTTP `GET /call/ring` — fires the calls skill's ring handler.

        Auth: `X-Shared-Secret` header must match the configured secret.
        Returns:
            200 ringing — skill accepted; ring earcon + announcement
                          starting on grandpa's device.
            401 bad secret — header missing or wrong value.
            409 busy — skill rejected (e.g., grandpa already on a call).
            503 calls disabled — server has no `on_ring` callback.
        """
        if self._on_ring is None or self._ring_secret is None:
            await logger.awarning("server.rx.ring_disabled")
            return _http_response(503, b"calls disabled\n")
        secret = request.headers.get("X-Shared-Secret", "")
        if secret != self._ring_secret:
            await logger.awarning("server.rx.ring_unauthorized")
            return _http_response(401, b"bad secret\n")
        params = {k: v[0] for k, v in parse_qs(query).items()}
        await logger.ainfo("server.rx.ring", params=params)
        try:
            accepted = await self._on_ring(params)
        except Exception:
            await logger.aexception("server.rx.ring_handler_failed")
            return _http_response(500, b"internal error\n")
        if accepted:
            return _http_response(200, b"ringing\n")
        return _http_response(409, b"busy\n")

    async def _handle_connection(self, ws: ServerConnection) -> None:
        # WS path-based routing: `/call` is the caller-side connection
        # for a phone-call flow (handled by the calls skill); everything
        # else is the grandpa-default audio client.
        # `ws.request` is set by the time the handler runs (post-handshake).
        assert ws.request is not None
        path = urlsplit(ws.request.path).path
        if path == "/call":
            await self._handle_caller_connection(ws)
            return

        if self._client is not None:
            await ws.close(1008, "Server busy — one client at a time")
            return

        self._client = ws
        await logger.ainfo("client_connected", remote=str(ws.remote_address))
        try:
            # Handshake: hello first, then current state sync.
            await ws.send(json.dumps({"type": "hello", "protocol": PROTOCOL_VERSION}))
            await ws.send(json.dumps({"type": "state", "value": self._state}))
            async for raw in ws:
                await self._dispatch(raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._client = None
            await logger.ainfo("client_disconnected")

    async def _handle_caller_connection(self, ws: ServerConnection) -> None:
        """Caller-side WebSocket (Mario's web app calling grandpa).

        Auth: `?secret=<value>` query param — browsers can't easily set
        custom headers on a WebSocket upgrade. Closing with 1008 (policy
        violation) on auth failure is the standard WS rejection code.

        On success, the connection is handed to the calls skill via
        `on_caller_connected`. The skill owns lifecycle from there:
        reading caller PCM frames, forwarding them as the
        `InputClaim.on_mic_frame`, and dispatching grandpa's mic to the
        caller's WS. AudioServer never touches the connection again.
        """
        if self._on_caller_connected is None or self._ring_secret is None:
            await ws.close(1008, "Calls disabled")
            return
        assert ws.request is not None
        params = {k: v[0] for k, v in parse_qs(urlsplit(ws.request.path).query).items()}
        secret = params.get("secret", "")
        if secret != self._ring_secret:
            await logger.awarning(
                "server.rx.caller_unauthorized",
                remote=str(ws.remote_address),
            )
            await ws.close(1008, "Bad secret")
            return
        await logger.ainfo(
            "server.rx.caller_connected",
            remote=str(ws.remote_address),
        )
        try:
            await self._on_caller_connected(ws)
        except Exception:
            await logger.aexception("server.rx.caller_handler_failed")
        finally:
            await logger.ainfo("server.rx.caller_disconnected")

    async def _dispatch(self, raw: str | bytes) -> None:
        try:
            msg: dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            await logger.awarning("server.rx.malformed")
            return
        match msg.get("type"):
            case "audio":
                pcm = base64.b64decode(msg.get("data", ""))
                await self._on_audio_frame(pcm)
            case "ptt_start":
                await logger.ainfo("server.rx.ptt_start")
                await self._on_ptt_start()
            case "ptt_stop":
                await logger.ainfo("server.rx.ptt_stop")
                await self._on_ptt_stop()
            case "wake_word":
                await logger.ainfo("server.rx.wake_word")
                await self._on_wake_word()
            case "reset":
                await logger.ainfo("server.rx.reset")
                await self._on_reset()
            case "client_event":
                # Pure observability — client telemetry sink. No behavioral
                # effect on the server. The client emits events that the
                # server log can't otherwise see (UI state, audio queue,
                # silence timer, thinking tone). Logged as client.<event>
                # so the dev workflow's "describe symptom → read log" loop
                # works for client-side bugs too.
                event = str(msg.get("event", "unknown"))
                raw_data = msg.get("data")
                data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
                await logger.ainfo(f"client.{event}", **data)
            case other:
                await logger.awarning("server.rx.unknown", msg_type=other)

    # --- Server → client ---

    async def send_audio(self, pcm: bytes) -> None:
        await self._send({"type": "audio", "data": base64.b64encode(pcm).decode()})

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
        await logger.ainfo("server.tx.model_speaking", value=value)
        await self._send({"type": "model_speaking", "value": value})

    async def send_dev_event(self, kind: str, payload: dict[str, Any]) -> None:
        """Broadcast a dev-observability event for the dev UI to visualize.

        Additive channel on top of the production protocol. Production clients
        (ESP32) ignore unknown message types. See docs/protocol.md.
        """
        await self._send({"type": "dev_event", "kind": kind, "payload": payload})

    async def send_set_volume(self, level: int) -> None:
        await logger.ainfo("server.tx.set_volume", level=level)
        await self._send({"type": "set_volume", "level": level})

    async def send_audio_clear(self) -> None:
        """Tell the client to immediately drop any queued audio.

        Fires on seek so the audiobook player can start at the new position
        without the client first playing the tail of the old queue.
        """
        await self._send({"type": "audio_clear"})

    async def _send(self, msg: dict[str, Any]) -> None:
        if self._client is None:
            return
        with contextlib.suppress(websockets.ConnectionClosed):
            await self._client.send(json.dumps(msg))


def _http_response(status: int, body: bytes) -> Response:
    """Build an HTTP response for `process_request`. websockets' Response
    expects a `Headers` object — convenience wrapper to keep the call
    sites in `_handle_ring_request` short."""
    reason = {
        200: "OK",
        401: "Unauthorized",
        409: "Conflict",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }.get(status, "Error")
    headers = Headers(
        [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ]
    )
    return Response(status, reason, headers, body)

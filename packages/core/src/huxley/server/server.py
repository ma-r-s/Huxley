"""WebSocket audio server — the single interface point for all clients.

One client (browser, ESP32, future hardware) connects at a time. The client
owns all audio I/O (mic capture, speaker playback). This server owns the
OpenAI session, tool dispatch, state machine, and storage.

Protocol — client -> server
    {"type": "audio",     "data": "<base64 PCM16 24 kHz>"}
    {"type": "ptt_start"}
    {"type": "ptt_stop"}
    {"type": "wake_word"}

Protocol — server -> client
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

import structlog
import websockets
import websockets.http11
from websockets.asyncio.server import ServerConnection, serve

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
    ) -> None:
        self._host = host
        self._port = port
        self._on_wake_word = on_wake_word
        self._on_ptt_start = on_ptt_start
        self._on_ptt_stop = on_ptt_stop
        self._on_audio_frame = on_audio_frame
        self._client: ServerConnection | None = None
        self._state = "IDLE"

    @property
    def has_client(self) -> bool:
        return self._client is not None

    async def run(self) -> None:
        async with serve(self._handle_connection, self._host, self._port):
            await logger.ainfo(
                "audio_server_listening",
                url=f"ws://{self._host}:{self._port}",
            )
            await asyncio.Future()  # run until cancelled

    async def _handle_connection(self, ws: ServerConnection) -> None:
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
        await logger.adebug("server.tx.set_volume", level=level)
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

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
    {"type": "client_event", "event": "<name>", "data": {...}}  # telemetry only

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

PROTOCOL_VERSION = 2

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
    ) -> None:
        self._host = host
        self._port = port
        self._on_wake_word = on_wake_word
        self._on_ptt_start = on_ptt_start
        self._on_ptt_stop = on_ptt_stop
        self._on_audio_frame = on_audio_frame
        self._on_reset = on_reset
        self._client: ServerConnection | None = None
        self._state = "IDLE"
        # Last-sent input mode — cached so a new client can be brought
        # up to the current mic policy on connect. Defaults to PTT
        # because a fresh client has no active claim by definition.
        self._input_mode = INPUT_MODE_ASSISTANT_PTT
        self._active_claim_id: str | None = None

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
            # Evict the old client rather than rejecting the new one.
            # A new connection means either a browser reload or a freshly-
            # booted device — both should take priority over a stale socket.
            await logger.ainfo(
                "client_evicted",
                old=str(self._client.remote_address),
                new=str(ws.remote_address),
            )
            old = self._client
            self._client = None
            with contextlib.suppress(Exception):
                await old.close(1001, "Replaced by new client")

        self._client = ws
        await logger.ainfo("client_connected", remote=str(ws.remote_address))
        try:
            # Handshake: hello first, then current state + input mode
            # sync so a reconnecting client knows whether a claim is
            # already active on the server (if we land mid-call, the
            # client should jump straight to continuous-mic).
            await ws.send(json.dumps({"type": "hello", "protocol": PROTOCOL_VERSION}))
            await ws.send(json.dumps({"type": "state", "value": self._state}))
            await ws.send(
                json.dumps(
                    {
                        "type": "input_mode",
                        "mode": self._input_mode,
                        "reason": "idle",
                        "claim_id": self._active_claim_id,
                    },
                ),
            )
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

    async def send_claim_started(self, claim_id: str, skill: str) -> None:
        """Observability message — pure telemetry for clients that want
        to render a "call connecting" UI. The behavioral signal is
        `input_mode=skill_continuous`; this is flavor on top.
        """
        await logger.ainfo("server.tx.claim_started", claim_id=claim_id, skill=skill)
        await self._send(
            {"type": "claim_started", "claim_id": claim_id, "skill": skill},
        )

    async def send_claim_ended(self, claim_id: str, end_reason: str) -> None:
        """Observability message — claim has terminated for the given
        reason ("natural", "user_ptt", "preempted", "error"). The
        behavioral signal is `input_mode=assistant_ptt` fired
        immediately after.
        """
        await logger.ainfo("server.tx.claim_ended", claim_id=claim_id, end_reason=end_reason)
        await self._send(
            {"type": "claim_ended", "claim_id": claim_id, "end_reason": end_reason},
        )

    async def send_audio_clear(self) -> None:
        """Tell the client to immediately drop any queued audio.

        Fires on seek so the audiobook player can start at the new position
        without the client first playing the tail of the old queue.
        """
        await self._send({"type": "audio_clear"})

    async def send_stream_started(self, stream_id: str, label: str | None) -> None:
        """Tell the client that a long-form audio stream has begun.

        Triggers the "playing" orb state and waveform visualizer. The
        `label` (e.g. "Don Quixote", "Radio Clasica") is shown in the
        status line. `stream_id` correlates with the matching
        `stream_ended` message.
        """
        await logger.ainfo("server.tx.stream_started", stream_id=stream_id, label=label)
        await self._send({"type": "stream_started", "stream_id": stream_id, "label": label})

    async def send_stream_ended(self, stream_id: str, end_reason: str) -> None:
        """Tell the client that the long-form audio stream has ended.

        `end_reason`: "natural" (played to completion) or "interrupted"
        (user PTT, new tool call, or session reset).
        """
        await logger.ainfo("server.tx.stream_ended", stream_id=stream_id, end_reason=end_reason)
        await self._send(
            {"type": "stream_ended", "stream_id": stream_id, "end_reason": end_reason},
        )

    async def _send(self, msg: dict[str, Any]) -> None:
        if self._client is None:
            return
        with contextlib.suppress(websockets.ConnectionClosed):
            await self._client.send(json.dumps(msg))

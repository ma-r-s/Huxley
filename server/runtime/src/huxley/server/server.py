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
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

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

    from websockets.http11 import Request

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
        self._on_set_skill_enabled = on_set_skill_enabled
        self._on_set_skill_config = on_set_skill_config
        self._on_set_skill_secret = on_set_skill_secret
        self._on_delete_skill_secret = on_delete_skill_secret
        # T1.13 — persona swap hooks (see constructor docstring above).
        self._on_persona_select = on_persona_select
        self._get_hello_extras = get_hello_extras
        # `?persona=<name>` from the URL, captured in `_process_request`
        # and consumed before hello in `_handle_connection`. Single field
        # is safe for the same reason `_pending_language` is — handshakes
        # serialize per connection and we evict old clients.
        self._pending_persona: str | None = None
        self._client: ServerConnection | None = None
        self._state = "IDLE"
        # Last-sent input mode — cached so a new client can be brought
        # up to the current mic policy on connect. Defaults to PTT
        # because a fresh client has no active claim by definition.
        self._input_mode = INPUT_MODE_ASSISTANT_PTT
        self._active_claim_id: str | None = None
        # Language the currently-handshaking client requested via
        # `?lang=<code>` in the WebSocket URL. Captured in
        # `_process_request` (runs before `_handle_connection`), consumed
        # in `_handle_connection` to fire `on_language_select`. Single
        # field is safe because `serve()` runs handshakes serially per
        # connection and AudioServer allows only one client at a time;
        # a second concurrent upgrade would race but is architecturally
        # excluded (we evict old clients, not parallel them).
        self._pending_language: str | None = None
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
            )
            await asyncio.Future()  # run until cancelled

    def _process_request(
        self,
        connection: ServerConnection,
        request: Request,
    ) -> None:
        """Intercept the WebSocket upgrade to capture URL query params.

        Clients pass `?lang=<code>` to select the persona's translation
        for this session. `ServerConnection` uses `__slots__` so we can't
        attach the parsed value to the connection itself — instead we
        cache it on the server and consume it in `_handle_connection`,
        which runs immediately after the handshake completes. Returning
        `None` lets the handshake proceed normally.
        """
        del connection
        try:
            parsed = urlparse(request.path)
            qs = parse_qs(parsed.query)
            lang_values = qs.get("lang") or qs.get("language")
            lang = lang_values[0].strip().lower() if lang_values else None
            self._pending_language = lang or None
            # T1.13 — `?persona=<name>` selects which persona this
            # connection is for. Trimmed but not lowercased; persona
            # directory names are case-sensitive.
            persona_values = qs.get("persona")
            persona = persona_values[0].strip() if persona_values else None
            self._pending_persona = persona or None
        except Exception:
            # Never block a handshake over a malformed query string;
            # fall back to persona default.
            self._pending_language = None
            self._pending_persona = None
        return None

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
        # Pull the language and persona the client requested in its URL
        # (captured during `_process_request`) and hand each to the
        # respective callback. Persona-select runs FIRST so the runtime
        # can swap to the requested persona BEFORE we serialize hello —
        # otherwise the hello would carry the old persona's name.
        selected_language = self._pending_language
        selected_persona = self._pending_persona
        self._pending_language = None
        self._pending_persona = None
        await logger.ainfo(
            "client_connected",
            remote=str(ws.remote_address),
            language=selected_language,
            persona=selected_persona,
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
            await ws.send(json.dumps(hello_payload))
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
            # Fire the language-select callback AFTER the handshake so the
            # app can reconcile the OpenAI session language with what the
            # client asked for. A reconnect may happen here (dropping the
            # old session and bringing up a new one in the chosen
            # language); we let it run to completion before reading any
            # client messages so the persona stays consistent with the
            # turns that follow.
            if self._on_language_select is not None:
                await self._on_language_select(selected_language)
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
                await logger.ainfo(f"client.{event}", **data)
                await self._dispatch_client_event(event, data)
            case other:
                await logger.awarning("server.rx.unknown", msg_type=other)

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

    async def send_audio(self, pcm: bytes) -> None:
        if not pcm:
            return
        for offset in range(0, len(pcm), self._AUDIO_CHUNK_BYTES):
            chunk = pcm[offset : offset + self._AUDIO_CHUNK_BYTES]
            await self._send({"type": "audio", "data": base64.b64encode(chunk).decode()})
            # Yield so the recv loop and any other tasks can interleave
            # — keeps the WS bidirectional even mid-burst.
            await asyncio.sleep(0)

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
        if self._client is None:
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
        await logger.ainfo("server.tx.stream_ended", stream_id=stream_id, end_reason=end_reason)
        await self._send(
            {"type": "stream_ended", "stream_id": stream_id, "end_reason": end_reason},
        )

    async def _send(self, msg: dict[str, Any]) -> None:
        if self._client is None:
            return
        with contextlib.suppress(websockets.ConnectionClosed):
            await self._client.send(json.dumps(msg))

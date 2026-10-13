"""`OpenAIRealtimeProvider` — a `VoiceProvider` for the OpenAI Realtime API.

Thin transport. Owns the WebSocket connection lifecycle, builds the
initial `session.update`, and translates the Realtime wire format into
the framework's provider-neutral `VoiceProviderCallbacks`. All tool
dispatch, audio sequencing, and interrupt handling live on the
`TurnCoordinator`.

Other providers (a Whisper → Chat Completions → TTS chain, third-party
services) implement the same `VoiceProvider` protocol and slot in
anywhere this class does.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import json
import time
from typing import TYPE_CHECKING, Any, ClassVar

import structlog
import websockets

from huxley.summarize import context_header_for, summarize_transcript
from huxley.voice.openai_protocol import (
    AudioDeltaEvent,
    ClientEventType,
    ErrorEvent,
    FunctionCallEvent,
    ServerEventType,
    TranscriptEvent,
    find_refusal_code,
    parse_server_event,
)

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

    from huxley.config import Settings
    from huxley.cost import CostTracker
    from huxley.persona import PersonaSpec
    from huxley.storage.db import Storage
    from huxley.voice.provider import VoiceProviderCallbacks
    from huxley_sdk import SkillRegistry

logger = structlog.get_logger()

REALTIME_API_URL = "wss://api.openai.com/v1/realtime"

# Two causes look identical on the wire. T1.25: the model had been shut
# down for months while this hint still blamed billing.
_MODEL_UNAVAILABLE_HINT = (
    "Model unavailable. Either OpenAI retired it (check "
    "developers.openai.com/api/docs/deprecations and set HUXLEY_OPENAI_MODEL) "
    "or the account lacks Realtime access (needs >=5 USD lifetime spend)."
)

# `response.metadata` key carrying the cancel generation a response was
# requested in. The server echoes metadata on `response.created` and
# `response.done`, which is how a late event from a cancelled response is
# told apart from the current one.
_GENERATION_KEY = "huxley_gen"

# Tool outputs are logged up to this many characters.
_TOOL_OUTPUT_LOG_CHARS = 300

# Output audio is PCM16 mono at 24 kHz.
_PCM_BYTES_PER_SECOND = 24_000 * 2


class OpenAIRealtimeProvider:
    """`VoiceProvider` implementation for the OpenAI Realtime API.

    Owns the WebSocket; translates Realtime events into the provider-neutral
    callback set. Persists recent transcripts so reconnects can re-inject
    a short summary for continuity.
    """

    # Session refresh. OpenAI ends a Realtime session at 60 minutes
    # (`session_expired`), whatever is happening. After
    # `Settings.session_refresh_minutes` the provider reconnects itself at
    # the first idle moment instead: no response live or pending, the user
    # not mid-press, the last reply done playing, and no traffic either way
    # for `_REFRESH_QUIET_S`. The pauses are generous: the reconnect takes
    # a few seconds, and a follow-up question usually comes within
    # seconds of the answer ending. There are 10 minutes of slack.
    _REFRESH_POLL_S: ClassVar[float] = 1.0
    _REFRESH_QUIET_S: ClassVar[float] = 10.0
    # The client plays a reply after it has all arrived; time past the
    # estimated end of playback before the session counts as idle.
    _REFRESH_PLAYBACK_MARGIN_S: ClassVar[float] = 10.0
    # Uncommitted user audio blocks a refresh only this long after its last
    # frame, so a press abandoned without commit can't block it forever.
    _INPUT_PENDING_MAX_S: ClassVar[float] = 30.0
    # The conversation summary carried into the next session; past this,
    # the raw transcript tail is carried instead.
    _SUMMARY_TIMEOUT_S: ClassVar[float] = 10.0
    # A held-back `response.create` is sent anyway after this long, so a
    # `response.done` that never comes can't silence every later press.
    _DEFERRED_CREATE_MAX_S: ClassVar[float] = 5.0

    def __init__(
        self,
        config: Settings,
        persona: PersonaSpec,
        skill_registry: SkillRegistry,
        storage: Storage,
        callbacks: VoiceProviderCallbacks,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._config = config
        self._persona = persona
        self._skills = skill_registry
        self._storage = storage
        self._cb = callbacks
        self._cost_tracker = cost_tracker
        self._ws: ClientConnection | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._transcript_lines: list[str] = []
        # Computed by `disconnect(save_summary=True)` BEFORE the
        # receive task is cancelled; consumed by the receive task's
        # `finally` so `on_session_end(summary)` carries the freshly-
        # generated summary. Pre-T1.12 the provider wrote the summary
        # to storage directly AFTER firing on_session_end, creating a
        # race where the framework's `_on_session_end` saw the
        # previous session's summary. The locked design moves storage
        # writes to the framework: it calls `Storage.end_session(
        # session_id, summary)` from `_on_session_end` — a fresh
        # summary comes through the callback every time.
        self._pending_summary: str | None = None
        # Active session's resolved persona (language-collapsed). Set on
        # every connect(); used by disconnect() to pick the localized
        # summarization prompt so a reconnect fed back the summary in
        # the right language. Defaults to the persona's default language
        # so the pre-connect path (none yet) stays well-defined.
        self._resolved = persona.resolve()
        # Suspend flag — set by `suspend()`, cleared by `resume()`. While
        # True, the receive loop drops content events (audio deltas, tool
        # calls, transcripts) from the LLM and `send_user_audio` discards
        # any mic frames that sneak past the MicRouter. Lifecycle events
        # (response.done, audio_done, errors) still fire so the coordinator
        # can unwind an in-flight turn cleanly. See suspend()/resume().
        self._suspended: bool = False
        # Cancel generation: bumped by `cancel_current_response`, stamped on
        # every `response.create` as metadata. A response from an older
        # generation is stale: the coordinator already unwound its turn.
        self._generation = 0
        # Cleared if OpenAI ever rejects `response.metadata`; stale
        # detection then falls back to the response live at the cancel.
        self._send_metadata = True
        self._deferred_watchdog: asyncio.Task[None] | None = None
        self._reset_session_state()

    def _reset_session_state(self) -> None:
        """Per-session bookkeeping, cleared on every connect."""
        # The server allows one live response per conversation; a
        # `response.create` sent while one is live fails with
        # `conversation_already_has_active_response` (2026-10-12: three
        # presses got no answer). Set on `response.created`, cleared on
        # its `response.done`.
        self._active_response_id: str | None = None
        # Monotonic time a `response.create` was held back because a
        # response was live; sent on that response's `response.done`.
        self._create_deferred_at: float | None = None
        # One retry per create after `conversation_already_has_active_response`.
        self._create_retried = False
        self._stale_response_ids: set[str] = set()
        # `suspend()` dropped a create held back for a live turn: let the
        # next `response.done` through, even a stale one, so that turn
        # closes instead of waiting in COMMITTING.
        self._forward_next_done = False
        now = time.monotonic()
        self._session_started_at = now
        self._last_activity_at = now
        self._last_audio_append_at = 0.0
        # User audio appended since the last commit / clear.
        self._input_pending = False
        # Estimated monotonic time the client finishes playing the reply
        # audio sent so far (deltas arrive faster than real time).
        self._playback_until = 0.0

    @property
    def is_connected(self) -> bool:
        return self._ws is not None

    async def connect(self, language: str | None = None) -> None:
        """Open WebSocket and configure the session.

        `language` resolves the persona to a specific translation. `None`
        (or an unsupported code) falls back to the persona's default
        language — see `PersonaSpec.resolve`.
        """
        # Resolve the persona for this session's language and stash on
        # self — disconnect() reads `_resolved.language_code` to pick
        # the summarization prompt.
        self._resolved = self._persona.resolve(language)

        model = self._config.openai_model
        if not self._config.openai_api_key:
            msg = "HUXLEY_OPENAI_API_KEY is required — set it in .env"
            raise RuntimeError(msg)
        url = f"{REALTIME_API_URL}?model={model}"

        try:
            # GA interface: no `OpenAI-Beta` header. The beta interface was
            # shut down 2026-05-12 (docs/triage.md T1.25).
            self._ws = await websockets.connect(
                url,
                additional_headers={
                    "Authorization": f"Bearer {self._config.openai_api_key}",
                },
            )
        except websockets.InvalidStatus as exc:
            status = exc.response.status_code
            if status == 401:
                msg = "Invalid OpenAI API key — check HUXLEY_OPENAI_API_KEY"
                raise RuntimeError(msg) from exc
            # The exception's text is only "HTTP 429"; the reason is in the
            # body. Name a refusal (no credit, a spend limit) in the error,
            # so the app plays the persona's "unavailable" sound at the next
            # press and `connection_failed` says why (T1.43).
            body = bytes(exc.response.body).decode("utf-8", "replace")
            refused = find_refusal_code(body)
            if refused is not None:
                msg = f"OpenAI refused the session: {refused} (HTTP {status})"
                raise RuntimeError(msg) from exc
            raise

        # Build instructions from three layers:
        #   1. the persona system prompt + named constraint snippets,
        #      resolved for the active language
        #   2. skill-contributed context (e.g. audiobook catalog) so the LLM
        #      has baseline awareness of available resources without needing
        #      extra tool calls for "what books do you have?" style questions
        #   3. the last conversation summary for continuity across sessions,
        #      introduced by a language-matched header
        summary = await self._storage.get_latest_summary()
        instructions = self._resolved.system_prompt_with_constraints

        skill_context = self._skills.get_prompt_context()
        if skill_context:
            instructions += f"\n\n{skill_context}"

        if summary:
            header = context_header_for(self._resolved.language_code)
            instructions += f"\n\n{header} {summary}"

        # Voice: env-var override wins if set, otherwise the persona decides.
        voice = self._config.openai_voice or self._resolved.voice

        self._reset_session_state()
        await self._send(
            ClientEventType.SESSION_UPDATE,
            {"session": self._session_config(instructions, voice)},
        )

        # The receive loop outlives whichever client connection happened
        # to open this session. Inheriting that handler's `conn_id` stamped
        # a stale id on every provider/coordinator line for the rest of the
        # session, which misdirected the 2026-10-10 phone diagnosis.
        receive_ctx = contextvars.copy_context()
        receive_ctx.run(structlog.contextvars.unbind_contextvars, "conn_id")
        self._receive_task = asyncio.create_task(self._receive_loop(), context=receive_ctx)
        # A previous session that the server closed (no `disconnect()`)
        # still has its refresh task sleeping; it must not end this one.
        if self._refresh_task is not None:
            self._refresh_task.cancel()
        self._refresh_task = asyncio.create_task(self._refresh_loop(), context=receive_ctx.copy())
        await logger.ainfo(
            "session_connected",
            model=model,
            language=self._resolved.language_code,
        )

    def _session_config(self, instructions: str, voice: str) -> dict[str, Any]:
        """The GA `session.update` payload.

        Audio is PCM16 at 24 kHz both ways, matching the client wire
        format, so no resampling happens anywhere in the server.
        """
        pcm_24k = {"type": "audio/pcm", "rate": 24000}
        return {
            "type": "realtime",
            "instructions": instructions,
            "output_modalities": ["audio"],
            "tools": self._skills.get_all_tool_definitions(),
            "audio": {
                "input": {
                    "format": pcm_24k,
                    # Force the persona's transcription language. Without
                    # `language`, Whisper auto-detects per utterance and with
                    # heavy regional accents it flips languages on short
                    # inputs; the hint eliminates the ambiguity.
                    "transcription": {
                        "model": "whisper-1",
                        "language": self._resolved.transcription_language,
                    },
                    # PTT mode — disable server VAD, we commit manually on PTT release
                    "turn_detection": None,
                },
                "output": {"format": pcm_24k, "voice": voice},
            },
        }

    async def disconnect(self, *, save_summary: bool = True) -> None:
        """Close the WebSocket connection.

        When `save_summary=True` and the session has any transcript
        content, computes the LLM-backed summary BEFORE cancelling the
        receive task. The receive task's `finally` reads
        `self._pending_summary` and passes it to
        `on_session_end(summary)` so the framework attributes the
        summary to the right session row in storage. Pre-T1.12 the
        summary was written to storage directly here AFTER the
        callback fired — a race where the framework's
        `_on_session_end` read the previous session's summary.
        """
        # Capture refs and clear instance attrs synchronously before any
        # awaits. connect() may be scheduled (via on_session_end → auto-
        # reconnect) and run during the awaits below; clearing first ensures
        # it writes to clean state and we don't clobber the new connection.
        refresh_task = self._refresh_task
        receive_task = self._receive_task
        ws = self._ws
        self._refresh_task = None
        self._receive_task = None
        self._ws = None

        # The refresh task calls disconnect() itself; cancelling it then
        # would abort this very call at its first await. That is what
        # happened to the old 55-minute timeout: `session_timeout` was
        # logged, nothing disconnected, OpenAI expired the session 5
        # minutes later (every hour on 2026-10-11).
        if refresh_task is not None and refresh_task is not asyncio.current_task():
            refresh_task.cancel()

        if save_summary and self._transcript_lines:
            self._pending_summary = await self._summarize()

        if receive_task:
            receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receive_task

        if ws:
            await ws.close()

        self._transcript_lines.clear()
        await logger.ainfo("session_disconnected")

    async def _summarize(self) -> str:
        """Summary of this session for the next one's instructions.

        LLM-backed summarization first (T1.5), bounded by
        `_SUMMARY_TIMEOUT_S`; falls back to the raw transcript tail if the
        call fails, returns nothing or takes too long. Some context beats
        no context. Language from the resolved persona so the summary
        matches the language the reconnect will run in.
        """
        summary: str | None
        try:
            summary = await asyncio.wait_for(
                summarize_transcript(
                    self._transcript_lines,
                    self._config.openai_api_key or "",
                    language=self._resolved.language_code,
                ),
                self._SUMMARY_TIMEOUT_S,
            )
        except TimeoutError:
            await logger.awarning("session.summary_timeout", timeout_s=self._SUMMARY_TIMEOUT_S)
            summary = None
        if summary is None:
            summary = "\n".join(self._transcript_lines[-20:])
        return summary

    async def commit_and_request_response(self) -> None:
        """Commit the user's audio buffer and ask the model to respond."""
        if not self._ws:
            return
        await logger.ainfo("session.tx.commit")
        await self._send(ClientEventType.INPUT_AUDIO_BUFFER_COMMIT, {})
        self._input_pending = False
        await self._request_response()

    async def request_response(self) -> None:
        """Ask the model to generate another response (chained follow-up)."""
        if not self._ws:
            return
        await self._request_response()

    async def _request_response(self) -> None:
        """Send `response.create` now, or once the live response is done.

        After an interrupt the cancelled response is still live on the
        server until its `response.done`; a create sent before that is
        rejected and the press goes unanswered. Holding it back costs the
        few ms the cancel takes.
        """
        if self._active_response_id is not None:
            self._defer_create()
            await logger.ainfo(
                "session.tx.response_create_deferred",
                active_response_id=self._active_response_id,
            )
            return
        await self._send_response_create()

    def _defer_create(self) -> None:
        """Hold a `response.create` until the live response is done."""
        if self._create_deferred_at is not None:
            return
        deferred_at = time.monotonic()
        self._create_deferred_at = deferred_at
        self._deferred_watchdog = asyncio.create_task(self._force_deferred_create(deferred_at))

    async def _force_deferred_create(self, deferred_at: float) -> None:
        """Send a held-back create if its `response.done` never came."""
        await asyncio.sleep(self._DEFERRED_CREATE_MAX_S)
        if self._create_deferred_at != deferred_at or self._ws is None:
            return  # sent, dropped, or the session is gone
        await logger.awarning(
            "session.tx.response_create_forced",
            waited_ms=int((time.monotonic() - deferred_at) * 1000),
            active_response_id=self._active_response_id,
        )
        self._active_response_id = None
        await self._send_response_create(deferred_ms=int(self._DEFERRED_CREATE_MAX_S * 1000))

    async def _send_response_create(self, *, deferred_ms: int | None = None) -> None:
        # This create supersedes any held back one (e.g. a retry still
        # pending): never two for one request.
        self._create_deferred_at = None
        fields: dict[str, Any] = {"generation": self._generation}
        if deferred_ms is not None:
            fields["deferred_ms"] = deferred_ms
        await logger.ainfo("session.tx.response_create", **fields)
        payload: dict[str, Any] = {}
        if self._send_metadata:
            payload["response"] = {"metadata": {_GENERATION_KEY: str(self._generation)}}
        await self._send(ClientEventType.RESPONSE_CREATE, payload)

    async def cancel_current_response(self) -> None:
        """Cancel any in-progress model response.

        The coordinator owns the `response_cancelled` drop flag — this method
        only sends the API-level cancel + input-buffer clear. See
        `TurnCoordinator.interrupt()` for the full 6-step sequence.

        Everything the cancelled response still emits (audio, tool calls,
        `response.done`) is dropped here instead of reaching the
        coordinator: its turn is already unwound, and a late
        `response.done` used to end the NEXT press's turn (2026-10-12
        06:44, `coord.response_done state=committing` 0 ms after the
        commit). A create still held back for the interrupted turn is
        dropped too; the next press asks for its own.
        """
        if not self._ws:
            return
        self._generation += 1
        if self._active_response_id is not None:
            self._stale_response_ids.add(self._active_response_id)
        dropped_create = self._create_deferred_at is not None
        self._create_deferred_at = None
        self._forward_next_done = False  # whatever turn needed it is unwound
        # The coordinator clears the client's playback queue with every cancel.
        self._playback_until = 0.0
        await logger.ainfo(
            "session.tx.cancel",
            active_response_id=self._active_response_id,
            dropped_deferred_create=dropped_create,
        )
        await self._send(ClientEventType.RESPONSE_CANCEL, {})
        await self._send(ClientEventType.INPUT_AUDIO_BUFFER_CLEAR, {})
        self._input_pending = False

    async def suspend(self) -> None:
        """Stop user-audio forwarding and any in-flight response.

        Sends `response.cancel` + `input_audio_buffer.clear` — the spike
        (docs/research/realtime-suspend.md) showed that without this,
        OpenAI keeps generating server-side and buffers hundreds of KB
        of stale audio that would play on resume. Sets `_suspended` so
        the receive loop drops late in-flight content events (network
        race: a few deltas may already be over the wire when the cancel
        lands). Idempotent; no-op if not connected or already suspended.
        """
        if not self._ws or self._suspended:
            return
        await logger.ainfo("session.tx.suspend")
        self._suspended = True
        # No response may start while suspended. Unlike
        # `cancel_current_response`, the cancelled response's lifecycle
        # events still reach the coordinator (see the docstring above).
        # A create held back for a turn is dropped; that turn then needs
        # a `response.done` to close, even if the live one is stale.
        if self._create_deferred_at is not None:
            self._create_deferred_at = None
            self._forward_next_done = True
        # Order: cancel generation first so the server stops emitting,
        # then clear pending input buffer so an uncommitted PTT doesn't
        # leak into the resumed session.
        await self._send(ClientEventType.RESPONSE_CANCEL, {})
        await self._send(ClientEventType.INPUT_AUDIO_BUFFER_CLEAR, {})
        self._input_pending = False

    async def resume(self) -> None:
        """Unpause user-audio forwarding.

        Clears the suspend flag. Zero wire traffic — the next PTT commit
        naturally validates the session is still alive. Idempotent;
        no-op if not suspended.
        """
        if not self._suspended:
            return
        await logger.ainfo("session.tx.resume")
        self._suspended = False

    async def send_user_audio(self, pcm: bytes) -> None:
        """Send a PCM16 audio frame from the user's mic to the Realtime API.

        Drops silently while suspended — the `MicRouter` should be routing
        frames to a skill handler in that case, but this is a belt-and-
        suspenders guard against a frame sneaking through during the
        suspend/swap window.
        """
        if not self._ws or self._suspended:
            return
        encoded = base64.b64encode(pcm).decode("ascii")
        await self._send(
            ClientEventType.INPUT_AUDIO_BUFFER_APPEND,
            {"audio": encoded},
        )
        self._input_pending = True
        self._last_audio_append_at = time.monotonic()

    async def send_conversation_message(self, text: str) -> None:
        """Inject a user-role text message into the conversation without audio.

        Used by the coordinator after natural audio stream completion to
        trigger an LLM narration (e.g., announcing a book finished).
        """
        if not self._ws:
            return
        await logger.ainfo("session.tx.conversation_message", text_len=len(text))
        await self._send(
            ClientEventType.CONVERSATION_ITEM_CREATE,
            {
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                }
            },
        )

    async def send_tool_output(self, call_id: str, output: str) -> None:
        """Post a tool call's output back to OpenAI as a conversation item."""
        if not self._ws:
            return
        await logger.ainfo(
            "session.tx.tool_output",
            call_id=call_id,
            output=output[:_TOOL_OUTPUT_LOG_CHARS],
            output_len=len(output),
        )
        await self._send(
            ClientEventType.CONVERSATION_ITEM_CREATE,
            {
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                }
            },
        )

    async def _send(self, event_type: ClientEventType, payload: dict[str, Any]) -> None:
        if not self._ws:
            return
        msg = {"type": event_type.value, **payload}
        self._last_activity_at = time.monotonic()
        try:
            await self._ws.send(json.dumps(msg))
        except websockets.ConnectionClosed as exc:
            reason = str(exc)
            if "does not exist or you do not have access" in reason:
                await logger.aerror(
                    "realtime_api_access_denied",
                    model=self._config.openai_model,
                    hint=_MODEL_UNAVAILABLE_HINT,
                )
            raise

    async def _handle_server_event(self, data: dict[str, Any]) -> None:
        """Dispatch one server event dict to the right callback.

        Extracted from `_receive_loop` so integration tests can drive the
        provider with crafted event dicts (see
        `tests/integration/test_session_replay.py`) without needing a real
        WebSocket. Exercises the full parse + cost-track + dispatch path.

        While suspended (`_suspended=True`), drops content events (audio
        deltas, tool calls, transcripts) from any in-flight response.
        Lifecycle events (response.done, audio.done, errors) still fire
        so the coordinator can close out a cancelled turn's state. See
        `suspend()` for the contract rationale.

        Audio, tool calls, audio done and `response.done` from a response
        cancelled by `cancel_current_response` (stale) are dropped: see
        that method. Its transcript is kept: it was partly heard, and it
        belongs in the session history and summary.
        """
        event_type = data.get("type", "")
        self._last_activity_at = time.monotonic()
        if event_type == ServerEventType.RESPONSE_CREATED.value:
            await self._on_response_created(data)
            return
        stale = self._is_stale(data)
        parsed = parse_server_event(data)

        if isinstance(parsed, AudioDeltaEvent):
            if self._suspended:
                # Race: delta arrived after our response.cancel. Drop the
                # audio so it doesn't play on resume. Log at debug to keep
                # the suspend path diagnosable without noisy info logs.
                await logger.adebug(
                    "session.rx.audio_delta_dropped_while_suspended",
                    bytes=len(parsed.delta),
                )
            elif stale:
                await logger.adebug(
                    "session.rx.stale_dropped", type=event_type, bytes=len(parsed.delta)
                )
            else:
                audio_bytes = base64.b64decode(parsed.delta)
                now = time.monotonic()
                self._playback_until = (
                    max(self._playback_until, now) + len(audio_bytes) / _PCM_BYTES_PER_SECOND
                )
                await self._cb.on_audio_delta(audio_bytes)

        elif isinstance(parsed, FunctionCallEvent):
            if self._suspended:
                await logger.ainfo(
                    "session.rx.tool_call_dropped_while_suspended",
                    name=parsed.name,
                    call_id=parsed.call_id,
                )
            elif stale:
                await logger.ainfo(
                    "session.rx.stale_dropped",
                    type=event_type,
                    name=parsed.name,
                    call_id=parsed.call_id,
                )
            else:
                try:
                    args = json.loads(parsed.arguments)
                except json.JSONDecodeError:
                    args = {}
                await logger.ainfo(
                    "session.rx.tool_call",
                    name=parsed.name,
                    call_id=parsed.call_id,
                    args=args,
                )
                await self._cb.on_tool_call(parsed.call_id, parsed.name, args)

        elif isinstance(parsed, TranscriptEvent):
            self._transcript_lines.append(parsed.transcript)
            await logger.ainfo(
                "transcript",
                role=parsed.role,
                text=parsed.transcript,
            )
            if self._cb.on_transcript and not self._suspended:
                await self._cb.on_transcript(parsed.role, parsed.transcript)

        elif isinstance(parsed, ErrorEvent):
            if parsed.code == "response_cancel_not_active":
                await logger.ainfo("session.rx.error", code=parsed.code)
            elif parsed.code == "input_audio_buffer_commit_empty":
                await logger.ainfo(
                    "session.rx.error",
                    code=parsed.code,
                    message=parsed.message,
                )
                # The coordinator drops this turn; no answer for it later.
                self._create_deferred_at = None
                await self._cb.on_commit_failed()
            elif parsed.code == "conversation_already_has_active_response":
                # A create raced a response we hadn't heard of yet. Send
                # it again when that response is done, once.
                retry = not self._create_retried
                await logger.awarning(
                    "session.rx.error",
                    code=parsed.code,
                    message=parsed.message,
                    will_retry=retry,
                )
                if retry:
                    self._create_retried = True
                    self._defer_create()
            elif (refused := find_refusal_code(parsed.code, parsed.type)) is not None:
                # No credit or a spend limit: this answer won't come, and
                # neither will the next. The coordinator ends the turn with
                # the persona's "unavailable" sound instead of silence.
                await logger.aerror(
                    "session.rx.error",
                    code=parsed.code,
                    message=parsed.message,
                    error_type=parsed.type,
                )
                await self._response_refused(refused, via="error")
            elif self._send_metadata and "metadata" in f"{parsed.param} {parsed.message}":
                # Not verifiable without the live API: if OpenAI ever
                # refuses `response.metadata`, every create would fail.
                # Stop sending it and resend this one.
                self._send_metadata = False
                await logger.aerror(
                    "session.rx.metadata_rejected",
                    code=parsed.code,
                    message=parsed.message,
                    param=parsed.param,
                )
                await self._send_response_create()
            else:
                await logger.aerror(
                    "session.rx.error",
                    code=parsed.code,
                    message=parsed.message,
                    error_type=parsed.type,
                )
                if parsed.code == "model_not_found":
                    await logger.aerror(
                        "session.rx.error",
                        model=self._config.openai_model,
                        hint=_MODEL_UNAVAILABLE_HINT,
                    )

        if event_type == ServerEventType.RESPONSE_OUTPUT_AUDIO_DONE.value and not stale:
            await self._cb.on_audio_done()

        if event_type == ServerEventType.RESPONSE_DONE.value:
            await self._on_response_done(data, stale=stale)

    async def _on_response_created(self, data: dict[str, Any]) -> None:
        response = data.get("response") or {}
        response_id = response.get("id")
        stale = self._from_old_generation(response)
        if isinstance(response_id, str):
            self._active_response_id = response_id
            if stale:
                self._stale_response_ids.add(response_id)
        self._create_retried = False
        await logger.ainfo("session.rx.response_created", response_id=response_id, stale=stale)

    async def _on_response_done(self, data: dict[str, Any], *, stale: bool) -> None:
        response = data.get("response") or {}
        if self._cost_tracker is not None:
            usage = response.get("usage")
            if usage:
                try:
                    await self._cost_tracker.record(usage)
                except Exception:
                    # Cost tracking failure must never affect the
                    # session; log and proceed.
                    await logger.aexception("cost.record_failed")
        response_id = response.get("id")
        if response_id is None or response_id == self._active_response_id:
            self._active_response_id = None
        if isinstance(response_id, str):
            self._stale_response_ids.discard(response_id)
        error = _failed_response_error(response)
        fields: dict[str, Any] = {}
        if error is not None:
            fields["error_code"], fields["error_type"] = error
        await logger.ainfo(
            "session.rx.response_done",
            response_id=response_id,
            status=response.get("status"),
            stale=stale,
            **fields,
        )
        refused = None if stale or error is None else find_refusal_code(*error)
        if refused is not None:
            # A refused response (no credit, a spend limit) may come as a
            # failed `response.done` instead of an `error`. Not forwarded
            # as a done: the coordinator ends the turn on the refusal, and a
            # done arriving after a new press would end that press's turn.
            # A create held back for this turn would be refused too.
            self._create_deferred_at = None
            self._forward_next_done = False
            await self._response_refused(refused, via="response_done")
            return
        # Send a held-back create before telling the coordinator, so a
        # follow-up the coordinator asks for queues behind it.
        if self._create_deferred_at is not None and self._active_response_id is None:
            waited_ms = int((time.monotonic() - self._create_deferred_at) * 1000)
            self._create_deferred_at = None
            await self._send_response_create(deferred_ms=waited_ms)
        if stale and self._forward_next_done:
            await logger.ainfo("session.rx.stale_done_forwarded", response_id=response_id)
        if not stale or self._forward_next_done:
            self._forward_next_done = False
            await self._cb.on_response_done()

    async def _response_refused(self, code: str, *, via: str) -> None:
        """OpenAI refused to answer for the account (no credit, a spend
        limit): tell the app, which ends the live turn audibly."""
        await logger.aerror("session.response_refused", code=code, via=via)
        if self._cb.on_response_refused is not None:
            await self._cb.on_response_refused(code)

    def _from_old_generation(self, response: dict[str, Any]) -> bool:
        """True if `response` was requested before the latest cancel.

        Reads the generation stamped in its metadata; a response without
        one (metadata not echoed) counts as current, the pre-2026-10-12
        behavior.
        """
        metadata = response.get("metadata")
        if not isinstance(metadata, dict):
            return False
        try:
            return int(metadata.get(_GENERATION_KEY, "")) < self._generation
        except (TypeError, ValueError):
            return False

    def _is_stale(self, data: dict[str, Any]) -> bool:
        """True if a server event belongs to a cancelled response."""
        response = data.get("response")
        if isinstance(response, dict):
            if response.get("id") in self._stale_response_ids:
                return True
            return self._from_old_generation(response)
        return data.get("response_id") in self._stale_response_ids

    async def _receive_loop(self) -> None:
        """Parse WebSocket messages and forward to the coordinator callbacks."""
        ws = self._ws
        assert ws is not None
        try:
            async for raw_msg in ws:
                data = json.loads(raw_msg)
                await self._handle_server_event(data)
            # A clean close (code 1000) ends the iteration without raising.
            await logger.ainfo(
                "session_connection_closed",
                code=ws.close_code,
                reason=ws.close_reason,
                age_s=self._session_age_s(),
            )
        except websockets.ConnectionClosed as exc:
            await logger.ainfo(
                "session_connection_closed",
                code=exc.rcvd.code if exc.rcvd else None,
                reason=exc.rcvd.reason if exc.rcvd else None,
                sent_code=exc.sent.code if exc.sent else None,
                age_s=self._session_age_s(),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await logger.aexception("session_receive_error")
        finally:
            # The server closed this session (no `disconnect()`, which clears
            # `_ws` first): its refresh task must not outlive it. Due and
            # polling, it would otherwise go idle 3 s after the close and
            # disconnect whatever `_ws` is by then, possibly the session
            # the auto-reconnect is opening.
            if self._ws is ws and self._refresh_task is not None:
                self._refresh_task.cancel()
                self._refresh_task = None
            # Read AND clear the pending summary so a subsequent natural
            # close (e.g. a quick auto-reconnect that immediately exits
            # again) doesn't carry the previous session's summary into a
            # new row. None passes through cleanly when no summary was
            # generated — the storage row's `summary` stays null.
            summary = self._pending_summary
            self._pending_summary = None
            await self._cb.on_session_end(summary)

    def _session_age_s(self) -> int:
        return int(time.monotonic() - self._session_started_at)

    def _refresh_blocker(self, now: float) -> str | None:
        """Why the session can't be refreshed at `now`; None when idle."""
        # A skill holds the mic (a call): a disconnect ends the claim with
        # ERROR, i.e. hangs up the call. Wait until it's over.
        if self._suspended:
            return "input_claimed"
        if self._active_response_id is not None:
            return "response_active"
        if self._create_deferred_at is not None:
            return "response_pending"
        if self._input_pending and now - self._last_audio_append_at < self._INPUT_PENDING_MAX_S:
            return "user_audio"
        if now < self._playback_until + self._REFRESH_PLAYBACK_MARGIN_S:
            return "reply_playing"
        if now - self._last_activity_at < self._REFRESH_QUIET_S:
            return "recent_traffic"
        return None

    async def _refresh_loop(self) -> None:
        """Reconnect at the first idle moment after `session_refresh_minutes`.

        Timed from session start, not from the last activity: OpenAI's
        60-minute limit counts the whole session, so the old activity-reset
        timer never fired in a session in use (2026-10-12 07:31 and 08:31,
        `session_expired` seconds after turns).

        The summary is computed while the session is still up, so the
        window where a press finds no session is just the reconnect; if
        anything happened meanwhile, it waits for the next idle moment.
        """
        try:
            await asyncio.sleep(self._config.session_refresh_minutes * 60)
            due_logged = False
            while True:
                while (blocker := self._refresh_blocker(time.monotonic())) is not None:
                    if not due_logged:
                        await logger.ainfo(
                            "session.refresh_due",
                            age_s=self._session_age_s(),
                            waiting_for=blocker,
                        )
                        due_logged = True
                    await asyncio.sleep(self._REFRESH_POLL_S)
                summary = await self._summarize() if self._transcript_lines else None
                if self._refresh_blocker(time.monotonic()) is None:
                    break
            await logger.ainfo(
                "session.refresh",
                age_s=self._session_age_s(),
                quiet_s=round(time.monotonic() - self._last_activity_at, 1),
            )
        except asyncio.CancelledError:
            return
        self._pending_summary = summary
        await self.disconnect(save_summary=False)


def _failed_response_error(response: dict[str, Any]) -> tuple[str, str] | None:
    """(code, type) of the error of a `response.done` whose status is
    `failed` (`status_details.error`), or None. Missing parts are ""."""
    if response.get("status") != "failed":
        return None
    details = response.get("status_details")
    error = details.get("error") if isinstance(details, dict) else None
    if not isinstance(error, dict):
        return None
    return str(error.get("code") or ""), str(error.get("type") or "")

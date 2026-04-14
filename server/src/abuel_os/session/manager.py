"""WebSocket session manager for the OpenAI Realtime API.

Handles connection lifecycle, audio streaming, tool call dispatch,
and conversation context persistence.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from typing import TYPE_CHECKING, Any

import structlog
import websockets

from abuel_os.session.protocol import (
    AudioDeltaEvent,
    ClientEventType,
    ErrorEvent,
    FunctionCallEvent,
    ServerEventType,
    TranscriptEvent,
    parse_server_event,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from websockets.asyncio.client import ClientConnection

    from abuel_os.config import Settings
    from abuel_os.skills import SkillRegistry
    from abuel_os.storage.db import Storage

logger = structlog.get_logger()

REALTIME_API_URL = "wss://api.openai.com/v1/realtime"


class SessionManager:
    """Manages the WebSocket connection to OpenAI Realtime API.

    Responsibilities:
    - Connect/disconnect lifecycle
    - Stream audio to/from the API
    - Dispatch tool calls to the SkillRegistry
    - Notify the orchestrator of side effects (playback, timeout)
    - Persist conversation transcripts for reconnection context
    """

    def __init__(
        self,
        config: Settings,
        skill_registry: SkillRegistry,
        storage: Storage,
        on_audio_delta: Callable[[bytes], Awaitable[None]],
        on_tool_action: Callable[[str], Awaitable[None]],
        on_session_end: Callable[[], Awaitable[None]],
        on_model_done: Callable[[], Awaitable[None]] | None = None,
        on_transcript: Callable[[str, str], Awaitable[None]] | None = None,
        on_dev_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._skills = skill_registry
        self._storage = storage
        self._on_audio_delta = on_audio_delta
        self._on_tool_action = on_tool_action
        self._on_session_end = on_session_end
        self._on_model_done = on_model_done
        self._on_transcript = on_transcript
        self._on_dev_event = on_dev_event
        self._ws: ClientConnection | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._timeout_task: asyncio.Task[None] | None = None
        self._pending_action_task: asyncio.Task[None] | None = None
        self._transcript_lines: list[str] = []
        self._is_model_speaking = False
        # Set True after cancel_response — discards stale audio deltas that
        # OpenAI may emit before it processes the cancel.
        self._response_cancelled = False
        # Side effect (e.g. start_playback) deferred until AFTER the model's
        # verbal acknowledgement finishes. See _handle_function_call.
        self._pending_tool_action: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._ws is not None

    @property
    def is_model_speaking(self) -> bool:
        return self._is_model_speaking

    async def connect(self) -> None:
        """Open WebSocket and configure the session."""
        model = self._config.openai_model
        url = f"{REALTIME_API_URL}?model={model}"

        try:
            self._ws = await websockets.connect(
                url,
                additional_headers={
                    "Authorization": f"Bearer {self._config.openai_api_key}",
                    "OpenAI-Beta": "realtime=v1",
                },
            )
        except websockets.InvalidStatus as exc:
            if exc.response.status_code == 401:
                msg = "Invalid OpenAI API key — check ABUEL_OPENAI_API_KEY"
                raise RuntimeError(msg) from exc
            raise

        # Configure session. Build instructions from three layers:
        #   1. the static system prompt (persona + nunca-decir-no contract)
        #   2. skill-contributed context (e.g. audiobook catalog) so the LLM
        #      has baseline awareness of available resources without needing
        #      extra tool calls for _"¿qué libros tienes?"_ style questions
        #   3. the last conversation summary for continuity across sessions
        summary = await self._storage.get_latest_summary()
        instructions = self._config.system_prompt

        skill_context = self._skills.get_prompt_context()
        if skill_context:
            instructions += f"\n\n{skill_context}"

        if summary:
            instructions += f"\n\nContexto de la conversación anterior: {summary}"

        await self._send(
            ClientEventType.SESSION_UPDATE,
            {
                "session": {
                    "instructions": instructions,
                    "voice": self._config.openai_voice,
                    "tools": self._skills.get_all_tool_definitions(),
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    # Force Spanish transcription. Without `language`, Whisper
                    # auto-detects per utterance and with heavy llanero accents
                    # it flips to English on short inputs. Abuelo only speaks
                    # Spanish — the hint eliminates the ambiguity.
                    "input_audio_transcription": {
                        "model": "whisper-1",
                        "language": "es",
                    },
                    # PTT mode — disable server VAD, we commit manually on PTT release
                    "turn_detection": None,
                }
            },
        )

        self._receive_task = asyncio.create_task(self._receive_loop())
        self._reset_timeout()
        await logger.ainfo("session_connected", model=model)

    async def disconnect(self, *, save_summary: bool = True) -> None:
        """Close the WebSocket connection."""
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None

        if self._receive_task:
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task
            self._receive_task = None

        if save_summary and self._transcript_lines:
            transcript = "\n".join(self._transcript_lines[-20:])
            await self._storage.save_summary(transcript)

        if self._ws:
            await self._ws.close()
            self._ws = None

        self._transcript_lines.clear()
        self._is_model_speaking = False
        await logger.ainfo("session_disconnected")

    async def commit_and_respond(self) -> None:
        """Commit the audio buffer and ask the model to respond (PTT release)."""
        if not self._ws:
            return
        self._response_cancelled = False
        await self._send(ClientEventType.INPUT_AUDIO_BUFFER_COMMIT, {})
        await self._send(ClientEventType.RESPONSE_CREATE, {})
        self._reset_timeout()

    async def cancel_response(self) -> None:
        """Cancel any in-progress model response (e.g. user interrupts via PTT).

        Sets `_response_cancelled` so the receive loop discards any audio
        deltas that arrive in the brief window before OpenAI actually
        processes the cancel. Also clears `_pending_tool_action` so any
        deferred side effect (e.g. start_playback queued behind the ack)
        is cancelled too — the user changed their mind mid-ack.
        """
        if not self._ws:
            return
        self._response_cancelled = True
        self._is_model_speaking = False
        self._pending_tool_action = None
        await self._send(ClientEventType.RESPONSE_CANCEL, {})
        await self._send(ClientEventType.INPUT_AUDIO_BUFFER_CLEAR, {})

    async def send_audio(self, pcm_data: bytes) -> None:
        """Send a PCM16 audio frame to the Realtime API."""
        if not self._ws:
            return
        encoded = base64.b64encode(pcm_data).decode("ascii")
        await self._send(
            ClientEventType.INPUT_AUDIO_BUFFER_APPEND,
            {"audio": encoded},
        )
        self._reset_timeout()

    async def _send(self, event_type: ClientEventType, payload: dict[str, Any]) -> None:
        if not self._ws:
            return
        msg = {"type": event_type.value, **payload}
        try:
            await self._ws.send(json.dumps(msg))
        except websockets.ConnectionClosed as exc:
            reason = str(exc)
            if "does not exist or you do not have access" in reason:
                await logger.aerror(
                    "realtime_api_access_denied",
                    model=self._config.openai_model,
                    hint=(
                        "The Realtime API requires Tier 1 access (≥$5 lifetime spend). "
                        "Add credits at platform.openai.com/settings/billing"
                    ),
                )
            raise

    async def _receive_loop(self) -> None:
        """Process incoming WebSocket messages."""
        assert self._ws is not None
        try:
            async for raw_msg in self._ws:
                data = json.loads(raw_msg)
                event_type = data.get("type", "")
                parsed = parse_server_event(data)

                # Drop stale audio deltas that arrived after a cancel — the
                # user interrupted, they don't want to hear the tail.
                if isinstance(parsed, AudioDeltaEvent) and not self._response_cancelled:
                    self._is_model_speaking = True
                    audio_bytes = base64.b64decode(parsed.delta)
                    await self._on_audio_delta(audio_bytes)

                elif isinstance(parsed, FunctionCallEvent):
                    await self._handle_function_call(parsed)

                elif isinstance(parsed, TranscriptEvent):
                    self._transcript_lines.append(parsed.transcript)
                    if self._on_transcript:
                        await self._on_transcript(parsed.role, parsed.transcript)

                elif isinstance(parsed, ErrorEvent):
                    await logger.aerror(
                        "realtime_api_error",
                        message=parsed.message,
                        error_type=parsed.type,
                        code=parsed.code,
                    )
                    if parsed.code == "model_not_found":
                        await logger.aerror(
                            "realtime_api_access_hint",
                            hint=(
                                "The Realtime API requires Tier 1 access (≥$5 lifetime spend). "
                                "Add a payment method and purchase credits at "
                                "platform.openai.com/settings/billing"
                            ),
                        )

                # response.audio.done → the model finished speaking. Drive
                # the UI "model speaking" indicator off this event.
                if (
                    event_type == ServerEventType.RESPONSE_AUDIO_DONE.value
                    and not self._response_cancelled
                ):
                    self._is_model_speaking = False
                    if self._on_model_done:
                        await self._on_model_done()

                # response.done → the FULL response is complete (audio +
                # function calls). Fire any deferred side effect from the
                # most recent tool call. Detached via create_task so the
                # side effect (which may disconnect this very session)
                # doesn't try to cancel the task that's currently running it.
                if (
                    event_type == "response.done"
                    and not self._response_cancelled
                    and self._pending_tool_action is not None
                ):
                    action = self._pending_tool_action
                    self._pending_tool_action = None
                    self._pending_action_task = asyncio.create_task(
                        self._fire_pending_action(action)
                    )

        except websockets.ConnectionClosed:
            await logger.ainfo("session_connection_closed")
        except asyncio.CancelledError:
            raise
        except Exception:
            await logger.aexception("session_receive_error")
        finally:
            await self._on_session_end()

    async def _handle_function_call(self, event: FunctionCallEvent) -> None:
        """Dispatch a tool call and wire the response + deferred side effect.

        Order of operations:
        1. Dispatch skill → get ToolResult (e.g. audiobook pre-loaded paused)
        2. Send function_call_output to OpenAI
        3. Emit dev_event (observability)
        4. Stash any side effect as `_pending_tool_action` — do NOT fire yet
        5. Request a response — the model narrates `result.output.message`
           as a verbal acknowledgement
        6. When the ack finishes (`response.audio.done`), `_receive_loop`
           fires the pending action as a detached task so the state
           transition + session disconnect + player resume run cleanly

        This is why the book doesn't jump in without warning: the
        `start_playback` transition is held until the ack is fully spoken.
        """
        await logger.ainfo("function_call_received", name=event.name)

        try:
            args = json.loads(event.arguments)
        except json.JSONDecodeError:
            args = {}

        result = await self._skills.dispatch(event.name, args)

        # 2. Send function output back to the API
        await self._send(
            ClientEventType.CONVERSATION_ITEM_CREATE,
            {
                "item": {
                    "type": "function_call_output",
                    "call_id": event.call_id,
                    "output": result.output,
                }
            },
        )

        # 3. Emit dev event (observability) — see docs/protocol.md (dev_event)
        if self._on_dev_event is not None:
            await self._on_dev_event(
                "tool_call",
                {
                    "name": event.name,
                    "args": args,
                    "output": result.output,
                    "action": result.action.value,
                },
            )

        # 4. Queue the side effect (don't fire it yet).
        # For terminal actions (start_playback), the model has ALREADY
        # narrated its ack BEFORE calling the tool — that's what the tool
        # description tells it to do. A response.create here would generate
        # a redundant second ack. The pending action fires on `response.done`
        # instead, which lets the current response finish cleanly.
        if result.action.value != "none":
            self._pending_tool_action = result.action.value
        else:
            # Non-terminal tool call (e.g. search). Ask the model to continue
            # so it can narrate the tool output.
            self._response_cancelled = False
            await self._send(ClientEventType.RESPONSE_CREATE, {})

    async def _fire_pending_action(self, action: str) -> None:
        """Run a deferred side effect in a detached task.

        Wrapped in this helper (rather than calling `self._on_tool_action`
        directly inside `create_task`) so mypy sees a concrete `Coroutine`,
        and so we have one reference to store on `self._pending_action_task`
        to prevent garbage collection (RUF006).
        """
        await self._on_tool_action(action)

    def _reset_timeout(self) -> None:
        """Reset the silence/max-session timeout."""
        if self._timeout_task:
            self._timeout_task.cancel()
        self._timeout_task = asyncio.create_task(self._timeout_loop())

    async def _timeout_loop(self) -> None:
        """Disconnect after max conversation time."""
        try:
            await asyncio.sleep(self._config.conversation_max_minutes * 60)
            await logger.ainfo("session_timeout")
            await self.disconnect(save_summary=True)
        except asyncio.CancelledError:
            pass

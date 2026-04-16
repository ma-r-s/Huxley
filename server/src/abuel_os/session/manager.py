"""WebSocket session manager for the OpenAI Realtime API.

Thin transport — owns the WebSocket connection lifecycle, builds the
initial `session.update`, and shuttles events between OpenAI and the
`TurnCoordinator`. All tool dispatch, audio sequencing, and interrupt
handling live on the coordinator. The session manager just forwards
typed events to callbacks.
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
    """Owns the WebSocket to OpenAI Realtime API; forwards events to callbacks.

    Responsibilities:
    - Connect/disconnect lifecycle (including the session.update config)
    - Send client events: audio append, commit, response.create, response.cancel,
      conversation.item.create (function output)
    - Receive loop: parse server events and fire the matching callback
    - Persist conversation transcripts for reconnection context

    Non-responsibilities (owned by `TurnCoordinator`):
    - Tool dispatch
    - Audio sequencing, factory invocation, interrupt ordering
    - The `response_cancelled` drop flag
    - Tracking "is the model currently speaking"
    """

    def __init__(
        self,
        config: Settings,
        skill_registry: SkillRegistry,
        storage: Storage,
        on_audio_delta: Callable[[bytes], Awaitable[None]],
        on_function_call: Callable[[str, str, dict[str, Any]], Awaitable[None]],
        on_response_done: Callable[[], Awaitable[None]],
        on_audio_done: Callable[[], Awaitable[None]],
        on_commit_failed: Callable[[], Awaitable[None]],
        on_session_end: Callable[[], Awaitable[None]],
        on_transcript: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._skills = skill_registry
        self._storage = storage
        self._on_audio_delta = on_audio_delta
        self._on_function_call = on_function_call
        self._on_response_done = on_response_done
        self._on_audio_done = on_audio_done
        self._on_commit_failed = on_commit_failed
        self._on_session_end = on_session_end
        self._on_transcript = on_transcript
        self._ws: ClientConnection | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._timeout_task: asyncio.Task[None] | None = None
        self._transcript_lines: list[str] = []

    @property
    def is_connected(self) -> bool:
        return self._ws is not None

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

        # Build instructions from three layers:
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
        await logger.ainfo("session_disconnected")

    async def commit_and_respond(self) -> None:
        """Commit the audio buffer and request a response (PTT release)."""
        if not self._ws:
            return
        await self._send(ClientEventType.INPUT_AUDIO_BUFFER_COMMIT, {})
        await self._send(ClientEventType.RESPONSE_CREATE, {})
        self._reset_timeout()

    async def request_response(self) -> None:
        """Ask the model to generate another response (chained follow-up)."""
        if not self._ws:
            return
        await self._send(ClientEventType.RESPONSE_CREATE, {})

    async def cancel_response(self) -> None:
        """Cancel any in-progress model response.

        The coordinator owns the `response_cancelled` drop flag — this method
        only sends the API-level cancel + input-buffer clear. See
        `TurnCoordinator.interrupt()` for the full 6-step sequence.
        """
        if not self._ws:
            return
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

    async def send_function_output(self, call_id: str, output: str) -> None:
        """Post a tool call's output back to OpenAI as a conversation item."""
        if not self._ws:
            return
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
        """Parse WebSocket messages and forward to the coordinator callbacks."""
        assert self._ws is not None
        try:
            async for raw_msg in self._ws:
                data = json.loads(raw_msg)
                event_type = data.get("type", "")
                parsed = parse_server_event(data)

                if isinstance(parsed, AudioDeltaEvent):
                    audio_bytes = base64.b64decode(parsed.delta)
                    await self._on_audio_delta(audio_bytes)

                elif isinstance(parsed, FunctionCallEvent):
                    try:
                        args = json.loads(parsed.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    await self._on_function_call(parsed.call_id, parsed.name, args)

                elif isinstance(parsed, TranscriptEvent):
                    self._transcript_lines.append(parsed.transcript)
                    if self._on_transcript:
                        await self._on_transcript(parsed.role, parsed.transcript)

                elif isinstance(parsed, ErrorEvent):
                    if parsed.code == "response_cancel_not_active":
                        await logger.ainfo("realtime_api_cancel_noop")
                    elif parsed.code == "input_audio_buffer_commit_empty":
                        await logger.ainfo(
                            "realtime_api_commit_rejected",
                            message=parsed.message,
                        )
                        await self._on_commit_failed()
                    else:
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
                                    "The Realtime API requires Tier 1 access "
                                    "(≥$5 lifetime spend). Add a payment method "
                                    "and purchase credits at "
                                    "platform.openai.com/settings/billing"
                                ),
                            )

                if event_type == ServerEventType.RESPONSE_AUDIO_DONE.value:
                    await self._on_audio_done()

                if event_type == ServerEventType.RESPONSE_DONE.value:
                    await self._on_response_done()

        except websockets.ConnectionClosed:
            await logger.ainfo("session_connection_closed")
        except asyncio.CancelledError:
            raise
        except Exception:
            await logger.aexception("session_receive_error")
        finally:
            await self._on_session_end()

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

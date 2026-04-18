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
import json
from typing import TYPE_CHECKING, Any

import structlog
import websockets

from huxley.voice.openai_protocol import (
    AudioDeltaEvent,
    ClientEventType,
    ErrorEvent,
    FunctionCallEvent,
    ServerEventType,
    TranscriptEvent,
    parse_server_event,
)

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

    from huxley.config import Settings
    from huxley.persona import PersonaSpec
    from huxley.storage.db import Storage
    from huxley.voice.provider import VoiceProviderCallbacks
    from huxley_sdk import SkillRegistry

logger = structlog.get_logger()

REALTIME_API_URL = "wss://api.openai.com/v1/realtime"


class OpenAIRealtimeProvider:
    """`VoiceProvider` implementation for the OpenAI Realtime API.

    Owns the WebSocket; translates Realtime events into the provider-neutral
    callback set. Persists recent transcripts so reconnects can re-inject
    a short summary for continuity.
    """

    def __init__(
        self,
        config: Settings,
        persona: PersonaSpec,
        skill_registry: SkillRegistry,
        storage: Storage,
        callbacks: VoiceProviderCallbacks,
    ) -> None:
        self._config = config
        self._persona = persona
        self._skills = skill_registry
        self._storage = storage
        self._cb = callbacks
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
        if not self._config.openai_api_key:
            msg = "HUXLEY_OPENAI_API_KEY is required — set it in .env"
            raise RuntimeError(msg)
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
                msg = "Invalid OpenAI API key — check HUXLEY_OPENAI_API_KEY"
                raise RuntimeError(msg) from exc
            raise

        # Build instructions from three layers:
        #   1. the persona system prompt + named constraint snippets
        #   2. skill-contributed context (e.g. audiobook catalog) so the LLM
        #      has baseline awareness of available resources without needing
        #      extra tool calls for _"¿qué libros tienes?"_ style questions
        #   3. the last conversation summary for continuity across sessions
        summary = await self._storage.get_latest_summary()
        instructions = self._persona.system_prompt_with_constraints

        skill_context = self._skills.get_prompt_context()
        if skill_context:
            instructions += f"\n\n{skill_context}"

        if summary:
            instructions += f"\n\nContexto de la conversación anterior: {summary}"

        # Voice: env-var override wins if set, otherwise the persona decides.
        voice = self._config.openai_voice or self._persona.voice

        await self._send(
            ClientEventType.SESSION_UPDATE,
            {
                "session": {
                    "instructions": instructions,
                    "voice": voice,
                    "tools": self._skills.get_all_tool_definitions(),
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    # Force the persona's transcription language. Without
                    # `language`, Whisper auto-detects per utterance and with
                    # heavy regional accents it flips languages on short
                    # inputs; the hint eliminates the ambiguity.
                    "input_audio_transcription": {
                        "model": "whisper-1",
                        "language": self._persona.transcription_language,
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
        # Capture refs and clear instance attrs synchronously before any
        # awaits. connect() may be scheduled (via on_session_end → auto-
        # reconnect) and run during the awaits below; clearing first ensures
        # it writes to clean state and we don't clobber the new connection.
        timeout_task = self._timeout_task
        receive_task = self._receive_task
        ws = self._ws
        self._timeout_task = None
        self._receive_task = None
        self._ws = None

        if timeout_task:
            timeout_task.cancel()

        if receive_task:
            receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receive_task

        if save_summary and self._transcript_lines:
            transcript = "\n".join(self._transcript_lines[-20:])
            await self._storage.save_summary(transcript)

        if ws:
            await ws.close()

        self._transcript_lines.clear()
        await logger.ainfo("session_disconnected")

    async def commit_and_request_response(self) -> None:
        """Commit the user's audio buffer and ask the model to respond."""
        if not self._ws:
            return
        await logger.ainfo("session.tx.commit")
        await self._send(ClientEventType.INPUT_AUDIO_BUFFER_COMMIT, {})
        await self._send(ClientEventType.RESPONSE_CREATE, {})
        self._reset_timeout()

    async def request_response(self) -> None:
        """Ask the model to generate another response (chained follow-up)."""
        if not self._ws:
            return
        await logger.ainfo("session.tx.response_create")
        await self._send(ClientEventType.RESPONSE_CREATE, {})

    async def cancel_current_response(self) -> None:
        """Cancel any in-progress model response.

        The coordinator owns the `response_cancelled` drop flag — this method
        only sends the API-level cancel + input-buffer clear. See
        `TurnCoordinator.interrupt()` for the full 6-step sequence.
        """
        if not self._ws:
            return
        await logger.ainfo("session.tx.cancel")
        await self._send(ClientEventType.RESPONSE_CANCEL, {})
        await self._send(ClientEventType.INPUT_AUDIO_BUFFER_CLEAR, {})

    async def send_user_audio(self, pcm: bytes) -> None:
        """Send a PCM16 audio frame from the user's mic to the Realtime API."""
        if not self._ws:
            return
        encoded = base64.b64encode(pcm).decode("ascii")
        await self._send(
            ClientEventType.INPUT_AUDIO_BUFFER_APPEND,
            {"audio": encoded},
        )
        self._reset_timeout()

    async def send_tool_output(self, call_id: str, output: str) -> None:
        """Post a tool call's output back to OpenAI as a conversation item."""
        if not self._ws:
            return
        await logger.ainfo("session.tx.tool_output", call_id=call_id)
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
                    await self._cb.on_audio_delta(audio_bytes)

                elif isinstance(parsed, FunctionCallEvent):
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
                    if self._cb.on_transcript:
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
                        await self._cb.on_commit_failed()
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
                                hint=(
                                    "The Realtime API requires Tier 1 access "
                                    "(>=5 USD lifetime spend). Add a payment "
                                    "method and purchase credits at "
                                    "platform.openai.com/settings/billing"
                                ),
                            )

                if event_type == ServerEventType.RESPONSE_AUDIO_DONE.value:
                    await self._cb.on_audio_done()

                if event_type == ServerEventType.RESPONSE_DONE.value:
                    await self._cb.on_response_done()

        except websockets.ConnectionClosed:
            await logger.ainfo("session_connection_closed")
        except asyncio.CancelledError:
            raise
        except Exception:
            await logger.aexception("session_receive_error")
        finally:
            await self._cb.on_session_end()

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

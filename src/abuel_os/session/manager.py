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
    ) -> None:
        self._config = config
        self._skills = skill_registry
        self._storage = storage
        self._on_audio_delta = on_audio_delta
        self._on_tool_action = on_tool_action
        self._on_session_end = on_session_end
        self._ws: ClientConnection | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._timeout_task: asyncio.Task[None] | None = None
        self._transcript_lines: list[str] = []
        self._is_model_speaking = False

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

        # Configure session
        summary = await self._storage.get_latest_summary()
        instructions = self._config.system_prompt
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
                    "input_audio_transcription": {"model": "whisper-1"},
                    # PTT mode — we commit manually on Space release
                    "turn_detection": {"type": "none"},
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
            # Store the last conversation for reconnection context
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
        await self._send(ClientEventType.INPUT_AUDIO_BUFFER_COMMIT, {})
        await self._send(ClientEventType.RESPONSE_CREATE, {})
        self._reset_timeout()

    async def cancel_response(self) -> None:
        """Cancel any in-progress model response (e.g. user interrupts via PTT)."""
        if not self._ws:
            return
        await self._send(ClientEventType.RESPONSE_CANCEL, {})
        await self._send(ClientEventType.INPUT_AUDIO_BUFFER_CLEAR, {})
        self._is_model_speaking = False

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

                if isinstance(parsed, AudioDeltaEvent):
                    self._is_model_speaking = True
                    audio_bytes = base64.b64decode(parsed.delta)
                    await self._on_audio_delta(audio_bytes)

                elif isinstance(parsed, FunctionCallEvent):
                    await self._handle_function_call(parsed)

                elif isinstance(parsed, TranscriptEvent):
                    self._transcript_lines.append(parsed.transcript)

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

                if event_type == ServerEventType.RESPONSE_AUDIO_DONE.value:
                    self._is_model_speaking = False

        except websockets.ConnectionClosed:
            await logger.ainfo("session_connection_closed")
        except asyncio.CancelledError:
            raise
        except Exception:
            await logger.aexception("session_receive_error")
        finally:
            await self._on_session_end()

    async def _handle_function_call(self, event: FunctionCallEvent) -> None:
        """Dispatch a tool call to the skill registry and send the result back."""
        await logger.ainfo("function_call_received", name=event.name)

        try:
            args = json.loads(event.arguments)
        except json.JSONDecodeError:
            args = {}

        result = await self._skills.dispatch(event.name, args)

        # Send function output back to the API
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
        # Trigger the model to continue
        await self._send(ClientEventType.RESPONSE_CREATE, {})

        # Handle side effects
        if result.action.value != "none":
            await self._on_tool_action(result.action.value)

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

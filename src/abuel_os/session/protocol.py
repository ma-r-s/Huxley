"""Typed schemas for OpenAI Realtime API events.

These mirror the server-sent events we need to handle. We don't model
every possible event — just the ones relevant to AbuelOS.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ServerEventType(Enum):
    """Server-sent event types we handle."""

    SESSION_CREATED = "session.created"
    SESSION_UPDATED = "session.updated"
    RESPONSE_AUDIO_DELTA = "response.audio.delta"
    RESPONSE_AUDIO_DONE = "response.audio.done"
    RESPONSE_AUDIO_TRANSCRIPT_DONE = "response.audio_transcript.done"
    RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE = "response.function_call_arguments.done"
    RESPONSE_DONE = "response.done"
    INPUT_AUDIO_BUFFER_SPEECH_STARTED = "input_audio_buffer.speech_started"
    INPUT_AUDIO_BUFFER_SPEECH_STOPPED = "input_audio_buffer.speech_stopped"
    CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED = (
        "conversation.item.input_audio_transcription.completed"
    )
    ERROR = "error"


class ClientEventType(Enum):
    """Client-sent event types."""

    SESSION_UPDATE = "session.update"
    INPUT_AUDIO_BUFFER_APPEND = "input_audio_buffer.append"
    INPUT_AUDIO_BUFFER_COMMIT = "input_audio_buffer.commit"
    INPUT_AUDIO_BUFFER_CLEAR = "input_audio_buffer.clear"
    CONVERSATION_ITEM_CREATE = "conversation.item.create"
    RESPONSE_CREATE = "response.create"
    RESPONSE_CANCEL = "response.cancel"


@dataclass(frozen=True, slots=True)
class FunctionCallEvent:
    """Parsed from response.function_call_arguments.done."""

    call_id: str
    name: str
    arguments: str  # JSON string — caller must parse


@dataclass(frozen=True, slots=True)
class AudioDeltaEvent:
    """Parsed from response.audio.delta."""

    delta: str  # base64-encoded audio


@dataclass(frozen=True, slots=True)
class TranscriptEvent:
    """Parsed from transcription completion events."""

    transcript: str


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    """Parsed from error events."""

    message: str
    type: str = ""
    code: str = ""


def parse_server_event(
    data: dict[str, Any],
) -> FunctionCallEvent | AudioDeltaEvent | TranscriptEvent | ErrorEvent | None:
    """Parse a raw server event dict into a typed event, or None if unhandled."""
    event_type = data.get("type", "")

    if event_type == ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE.value:
        return FunctionCallEvent(
            call_id=data.get("call_id", ""),
            name=data.get("name", ""),
            arguments=data.get("arguments", "{}"),
        )

    if event_type == ServerEventType.RESPONSE_AUDIO_DELTA.value:
        return AudioDeltaEvent(delta=data.get("delta", ""))

    if event_type in (
        ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE.value,
        ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED.value,
    ):
        return TranscriptEvent(transcript=data.get("transcript", ""))

    if event_type == ServerEventType.ERROR.value:
        error = data.get("error", {})
        return ErrorEvent(
            message=error.get("message", "Unknown error"),
            type=error.get("type", ""),
            code=error.get("code", ""),
        )

    return None

"""Typed schemas for OpenAI Realtime API events (GA interface).

These mirror the server-sent events we need to handle. We don't model
every possible event — just the ones relevant to Huxley.

The beta interface (`OpenAI-Beta: realtime=v1`, `response.audio.*`
event names) was shut down 2026-05-12; see docs/triage.md T1.25.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

# OpenAI's codes for refusing the account rather than one request (T1.43).
# Credit: the prepaid balance is gone. Limit: a monthly spend cap was
# reached (the organization's, a project's, or OpenAI's own approved
# limit; `billing_hard_limit_reached` is the old name). Every answer fails
# until someone tops up or the month turns, so the provider reports these
# as a refusal instead of a passing error. Same lists as huxley-watch
# (server/watch/src/huxley_watch/rules.py): keep them in step.
CREDIT_CODES = frozenset({"insufficient_quota", "credit_balance_exhausted"})
LIMIT_CODES = frozenset(
    {
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "billing_hard_limit_reached",
    }
)
REFUSAL_CODES = CREDIT_CODES | LIMIT_CODES


def find_refusal_code(*texts: str | None) -> str | None:
    """The first refusal code found in any of `texts` (an error's code or
    type, a handshake body, a traceback), or None. Substring match: the
    codes are distinctive, and a body or traceback carries them inline."""
    for text in texts:
        if not text:
            continue
        for code in sorted(REFUSAL_CODES):
            if code in text:
                return code
    return None


class ServerEventType(Enum):
    """Server-sent event types we handle."""

    SESSION_CREATED = "session.created"
    SESSION_UPDATED = "session.updated"
    RESPONSE_CREATED = "response.created"
    RESPONSE_OUTPUT_AUDIO_DELTA = "response.output_audio.delta"
    RESPONSE_OUTPUT_AUDIO_DONE = "response.output_audio.done"
    RESPONSE_OUTPUT_AUDIO_TRANSCRIPT_DONE = "response.output_audio_transcript.done"
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
    """Parsed from response.output_audio.delta."""

    delta: str  # base64-encoded audio


@dataclass(frozen=True, slots=True)
class TranscriptEvent:
    """Parsed from transcription completion events."""

    transcript: str
    role: str = "assistant"


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    """Parsed from error events."""

    message: str
    type: str = ""
    code: str = ""
    param: str = ""  # the request field at fault, when the server names one


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

    if event_type == ServerEventType.RESPONSE_OUTPUT_AUDIO_DELTA.value:
        return AudioDeltaEvent(delta=data.get("delta", ""))

    if event_type == ServerEventType.RESPONSE_OUTPUT_AUDIO_TRANSCRIPT_DONE.value:
        return TranscriptEvent(transcript=data.get("transcript", ""), role="assistant")

    if event_type == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED.value:
        return TranscriptEvent(transcript=data.get("transcript", ""), role="user")

    if event_type == ServerEventType.ERROR.value:
        error = data.get("error", {})
        return ErrorEvent(
            message=error.get("message", "Unknown error"),
            type=error.get("type", ""),
            code=error.get("code", ""),
            param=error.get("param") or "",
        )

    return None

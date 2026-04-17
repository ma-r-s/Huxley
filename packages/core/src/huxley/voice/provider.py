"""`VoiceProvider` — the transport abstraction for real-time voice LLMs.

A voice provider owns the conversation transport: the WebSocket (or whatever
else) to the LLM service, the session lifecycle, and the translation
between provider-specific wire events and the framework's internal
callbacks. The `TurnCoordinator` depends on this protocol, never on a
concrete provider.

Providers receive incoming events from the LLM and dispatch them through
the callback set handed in at construction (see `VoiceProviderCallbacks`).
Outgoing operations — `send_user_audio`, `send_tool_output`,
`commit_and_request_response`, `cancel_current_response`,
`request_response` — are methods the coordinator calls directly on the
provider.

Concrete providers today: `huxley.voice.openai_realtime.OpenAIRealtimeProvider`
for production, `huxley.voice.stub.StubVoiceProvider` for tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True, slots=True)
class VoiceProviderCallbacks:
    """Callbacks the provider fires as it receives events from the LLM.

    The coordinator supplies these at provider construction so the provider
    can push events up without holding a coordinator reference directly.
    """

    # PCM16 chunk of assistant audio.
    on_audio_delta: Callable[[bytes], Awaitable[None]]

    # The LLM asked to invoke a tool. Args is the parsed JSON object.
    on_tool_call: Callable[[str, str, dict[str, Any]], Awaitable[None]]

    # A response (one round of model speech + tool calls) finished.
    on_response_done: Callable[[], Awaitable[None]]

    # The current audio span ended (the model stopped speaking this round).
    on_audio_done: Callable[[], Awaitable[None]]

    # The user's audio buffer couldn't be committed (e.g. too short). The
    # coordinator aborts the turn without waiting for a response.
    on_commit_failed: Callable[[], Awaitable[None]]

    # The transport dropped; the coordinator should unwind any live turn.
    on_session_end: Callable[[], Awaitable[None]]

    # Optional: transcript lines (user or assistant). `None` means the
    # provider doesn't produce transcripts.
    on_transcript: Callable[[str, str], Awaitable[None]] | None = None


@runtime_checkable
class VoiceProvider(Protocol):
    """Transport for real-time voice conversation with an LLM.

    Structural protocol — a concrete provider satisfies it by defining
    these methods. Method semantics below are provider-neutral; each
    concrete provider translates to/from its wire format.
    """

    @property
    def is_connected(self) -> bool:
        """True iff the transport is open and ready to send/receive."""
        ...

    async def connect(self) -> None:
        """Open the transport, negotiate the session, start receiving.

        Raises on failure; callers must catch and decide on retry policy.
        """
        ...

    async def disconnect(self, *, save_summary: bool = False) -> None:
        """Close the transport. `save_summary=True` asks the provider to
        emit a one-shot summary of the session (if it supports that) before
        tearing down — used for continuity across reconnects."""
        ...

    async def send_user_audio(self, pcm: bytes) -> None:
        """Append a PCM16 chunk from the user's microphone to the input buffer."""
        ...

    async def send_tool_output(self, call_id: str, output: str) -> None:
        """Return the output of a tool the LLM invoked via `on_tool_call`."""
        ...

    async def commit_and_request_response(self) -> None:
        """Seal the user's audio turn and ask the LLM to respond.

        Called when the user releases PTT. Combines "commit input buffer"
        and "request response" because they're one logical step —
        providers that expose them separately still surface one method.
        """
        ...

    async def cancel_current_response(self) -> None:
        """Abort the in-flight response (if any). Safe to call when no
        response is active — the provider should no-op rather than error."""
        ...

    async def request_response(self) -> None:
        """Ask the LLM for another response in the same turn (for
        multi-round tool-narration flows). The provider assumes the input
        buffer has already been committed."""
        ...

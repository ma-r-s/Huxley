"""`StubVoiceProvider` — a deterministic `VoiceProvider` for tests.

Satisfies the `VoiceProvider` protocol without any network or LLM. Tests
drive the provider explicitly with `emit_*` methods to simulate incoming
events from the LLM, and assert on the `sent` log of outgoing calls the
coordinator made against the provider.

Use this to write end-to-end coordinator tests without mocking the seven
callbacks the old `AsyncMock` shape required.

Typical flow:

    provider = StubVoiceProvider()
    coordinator = TurnCoordinator(..., provider=provider)
    provider.install_callbacks_from(coordinator)  # or pass via VoiceProviderCallbacks
    await provider.connect()

    await coordinator.on_ptt_start()
    ...
    await provider.emit_tool_call("call_1", "get_current_time", {})
    await provider.emit_response_done()

    assert provider.sent == [
        ("commit_and_request_response",),
        ("send_tool_output", "call_1", '{"time": "..."}'),
        ("request_response",),
    ]
"""

from __future__ import annotations

from typing import Any

from huxley.voice.provider import VoiceProviderCallbacks


class StubVoiceProvider:
    """In-memory `VoiceProvider` for tests — deterministic and introspectable."""

    def __init__(self, callbacks: VoiceProviderCallbacks | None = None) -> None:
        # Default no-op callbacks so the provider is usable before the
        # coordinator wires it up. Tests either pass `callbacks` up front
        # or call `install_callbacks(...)` after constructing the
        # coordinator.
        self._callbacks: VoiceProviderCallbacks = callbacks or _noop_callbacks()
        self._connected = False
        # Log of every outgoing method call the coordinator made. Each
        # entry is a tuple of (method_name, *args).
        self.sent: list[tuple[Any, ...]] = []
        # User audio frames the coordinator forwarded during LISTENING.
        self.user_audio: list[bytes] = []

    def install_callbacks(self, callbacks: VoiceProviderCallbacks) -> None:
        """Swap the callback set after construction.

        Useful when the coordinator is built after the provider — the test
        constructs the provider, then the coordinator (which needs the
        provider), then wires callbacks that reference the coordinator.
        """
        self._callbacks = callbacks

    # --- VoiceProvider protocol surface ---

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True
        self.sent.append(("connect",))

    async def disconnect(self, *, save_summary: bool = False) -> None:
        self._connected = False
        self.sent.append(("disconnect", save_summary))

    async def send_user_audio(self, pcm: bytes) -> None:
        self.user_audio.append(pcm)
        # Intentionally NOT appended to `sent` — user audio is high-volume
        # and would clutter the log; use `user_audio` for explicit assertions.

    async def send_tool_output(self, call_id: str, output: str) -> None:
        self.sent.append(("send_tool_output", call_id, output))

    async def commit_and_request_response(self) -> None:
        self.sent.append(("commit_and_request_response",))

    async def cancel_current_response(self) -> None:
        self.sent.append(("cancel_current_response",))

    async def request_response(self) -> None:
        self.sent.append(("request_response",))

    async def send_conversation_message(self, text: str) -> None:
        self.sent.append(("send_conversation_message", text))

    # --- Test-driver surface: emit events the provider "received" ---

    async def emit_audio_delta(self, pcm: bytes) -> None:
        await self._callbacks.on_audio_delta(pcm)

    async def emit_tool_call(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        await self._callbacks.on_tool_call(call_id, name, args)

    async def emit_response_done(self) -> None:
        await self._callbacks.on_response_done()

    async def emit_audio_done(self) -> None:
        await self._callbacks.on_audio_done()

    async def emit_commit_failed(self) -> None:
        await self._callbacks.on_commit_failed()

    async def emit_session_end(self) -> None:
        await self._callbacks.on_session_end()

    async def emit_transcript(self, role: str, text: str) -> None:
        if self._callbacks.on_transcript:
            await self._callbacks.on_transcript(role, text)


async def _noop_bytes(_pcm: bytes) -> None:
    return None


async def _noop_tool_call(_cid: str, _name: str, _args: dict[str, Any]) -> None:
    return None


async def _noop_void() -> None:
    return None


def _noop_callbacks() -> VoiceProviderCallbacks:
    return VoiceProviderCallbacks(
        on_audio_delta=_noop_bytes,
        on_tool_call=_noop_tool_call,
        on_response_done=_noop_void,
        on_audio_done=_noop_void,
        on_commit_failed=_noop_void,
        on_session_end=_noop_void,
        on_transcript=None,
    )

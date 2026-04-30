"""Tests for `OpenAIRealtimeProvider` — the thin-transport VoiceProvider.

Owns only the WebSocket transport + the initial session.update config.
Tool dispatch, audio sequencing, and interrupt logic live on
`TurnCoordinator` and are covered elsewhere. These tests exercise the
surviving transport surface: basic state, audio/tool-output sends, and
transcript persistence across disconnect.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from huxley.config import Settings
from huxley.storage.db import Storage
from huxley.voice.openai_protocol import ClientEventType
from huxley.voice.openai_realtime import OpenAIRealtimeProvider
from huxley.voice.provider import VoiceProviderCallbacks
from huxley_sdk import SkillRegistry

if TYPE_CHECKING:
    from pathlib import Path

    from huxley.persona import PersonaSpec


def _callbacks() -> VoiceProviderCallbacks:
    return VoiceProviderCallbacks(
        on_audio_delta=AsyncMock(),
        on_tool_call=AsyncMock(),
        on_response_done=AsyncMock(),
        on_audio_done=AsyncMock(),
        on_commit_failed=AsyncMock(),
        on_session_end=AsyncMock(),
    )


@pytest.fixture
def provider_deps(tmp_path: Path, persona: PersonaSpec) -> dict[str, Any]:
    return {
        "config": Settings(openai_api_key="test-key"),
        "persona": persona,
        "skill_registry": SkillRegistry(),
        "storage": Storage(tmp_path / "test.db"),
        "callbacks": _callbacks(),
    }


@pytest.fixture
def provider(provider_deps: dict[str, Any]) -> OpenAIRealtimeProvider:
    return OpenAIRealtimeProvider(**provider_deps)


class TestProviderState:
    def test_not_connected_initially(self, provider: OpenAIRealtimeProvider) -> None:
        assert not provider.is_connected


class TestSendUserAudio:
    async def test_base64_encodes(self, provider: OpenAIRealtimeProvider) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        provider._ws = mock_ws

        pcm = b"\x00\x01\x02\x03"
        await provider.send_user_audio(pcm)

        assert len(sent) == 1
        assert sent[0]["type"] == ClientEventType.INPUT_AUDIO_BUFFER_APPEND.value
        assert base64.b64decode(sent[0]["audio"]) == pcm

    async def test_noop_when_disconnected(self, provider: OpenAIRealtimeProvider) -> None:
        await provider.send_user_audio(b"\x00\x01")


class TestSendToolOutput:
    async def test_posts_conversation_item(self, provider: OpenAIRealtimeProvider) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        provider._ws = mock_ws

        await provider.send_tool_output("call_abc", '{"result": "ok"}')

        assert len(sent) == 1
        msg = sent[0]
        assert msg["type"] == ClientEventType.CONVERSATION_ITEM_CREATE.value
        assert msg["item"]["type"] == "function_call_output"
        assert msg["item"]["call_id"] == "call_abc"
        assert msg["item"]["output"] == '{"result": "ok"}'

    async def test_noop_when_disconnected(self, provider: OpenAIRealtimeProvider) -> None:
        await provider.send_tool_output("call_x", "{}")


class TestCommitAndRequestResponse:
    async def test_sends_commit_then_create(self, provider: OpenAIRealtimeProvider) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        provider._ws = mock_ws

        await provider.commit_and_request_response()

        types = [m["type"] for m in sent]
        assert types == [
            ClientEventType.INPUT_AUDIO_BUFFER_COMMIT.value,
            ClientEventType.RESPONSE_CREATE.value,
        ]


class TestRequestResponse:
    async def test_sends_response_create_only(self, provider: OpenAIRealtimeProvider) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        provider._ws = mock_ws

        await provider.request_response()

        assert len(sent) == 1
        assert sent[0]["type"] == ClientEventType.RESPONSE_CREATE.value


class TestCancelCurrentResponse:
    async def test_sends_cancel_and_buffer_clear(self, provider: OpenAIRealtimeProvider) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        provider._ws = mock_ws

        await provider.cancel_current_response()

        types = [m["type"] for m in sent]
        assert types == [
            ClientEventType.RESPONSE_CANCEL.value,
            ClientEventType.INPUT_AUDIO_BUFFER_CLEAR.value,
        ]


class TestSessionEndSummary:
    """T1.12: the provider stashes the LLM-generated summary on
    `_pending_summary` BEFORE cancelling the receive task; the receive
    loop's `finally` reads it and passes it to `on_session_end(summary)`.
    Storage is no longer written by the provider — the framework's
    `_on_session_end` calls `Storage.end_session(id, summary)` against
    the active session id captured in `_on_transcript`.

    These tests narrow on the disconnect-side contract (compute + stash);
    the receive-loop firing path is exercised by the integration tests
    (e.g. `test_session_replay`) where a real receive task runs."""

    async def test_disconnect_with_save_stashes_summary(
        self, provider_deps: dict[str, Any]
    ) -> None:
        storage = provider_deps["storage"]
        await storage.init()

        provider = OpenAIRealtimeProvider(**provider_deps)
        provider._transcript_lines = [
            "Hola",
            "Quiero leer un libro",
            "El de García Márquez",
        ]

        await provider.disconnect(save_summary=True)

        # When the LLM summarization call fails (test-key is bogus), the
        # provider falls back to the raw transcript tail — which still
        # contains the named-entity content the caller cares about.
        assert provider._pending_summary is not None
        assert "García Márquez" in provider._pending_summary

        # Storage is NOT written by the provider any more — that's the
        # framework's job in `_on_session_end`. The receive loop's
        # finally fires on_session_end which the framework wires to
        # `Storage.end_session(active_id, summary)`.
        assert await storage.get_latest_summary() is None
        await storage.close()

    async def test_disconnect_without_save_leaves_pending_summary_none(
        self, provider_deps: dict[str, Any]
    ) -> None:
        storage = provider_deps["storage"]
        await storage.init()

        provider = OpenAIRealtimeProvider(**provider_deps)
        provider._transcript_lines = ["Some transcript"]

        await provider.disconnect(save_summary=False)

        assert provider._pending_summary is None
        assert await storage.get_latest_summary() is None
        await storage.close()


class TestSuspendResume:
    """Provider-side half of T1.4 Stage 2 `InputClaim`.

    Contract in docs/research/realtime-suspend.md — summary:
    - suspend: cancels in-flight response, clears input buffer, sets flag
    - resume: clears flag, zero wire traffic
    - Both idempotent
    - While suspended: send_user_audio drops, receive-loop drops content
      events (audio/tool_call/transcript) but lifecycle events pass
    """

    async def test_suspend_sends_cancel_and_clear(self, provider: OpenAIRealtimeProvider) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        provider._ws = mock_ws

        await provider.suspend()
        types = [m["type"] for m in sent]
        # Cancel first (stop server-side generation), then clear input
        # buffer (don't let uncommitted audio leak into resume). Order is
        # asserted because the spike showed the reverse order left a small
        # race where a commit event could land between the two.
        assert types == [
            ClientEventType.RESPONSE_CANCEL.value,
            ClientEventType.INPUT_AUDIO_BUFFER_CLEAR.value,
        ]
        assert provider._suspended is True

    async def test_suspend_is_idempotent(self, provider: OpenAIRealtimeProvider) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        provider._ws = mock_ws

        await provider.suspend()
        await provider.suspend()
        # Second suspend emitted nothing extra.
        assert len(sent) == 2  # from the first suspend only

    async def test_suspend_noop_when_disconnected(self, provider: OpenAIRealtimeProvider) -> None:
        # No WebSocket — should not raise, should not flip flag (no-op).
        await provider.suspend()
        assert provider._suspended is False

    async def test_resume_clears_flag_and_sends_nothing(
        self, provider: OpenAIRealtimeProvider
    ) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        provider._ws = mock_ws

        await provider.suspend()
        sent.clear()
        await provider.resume()
        assert provider._suspended is False
        # Resume is silent — no session.update, no wake-up ping, nothing.
        assert sent == []

    async def test_resume_is_idempotent(self, provider: OpenAIRealtimeProvider) -> None:
        # Resume-without-suspend is a no-op, not an error.
        await provider.resume()
        await provider.resume()
        assert provider._suspended is False

    async def test_send_user_audio_drops_while_suspended(
        self, provider: OpenAIRealtimeProvider
    ) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        provider._ws = mock_ws

        await provider.suspend()
        sent.clear()  # drop the suspend's own wire traffic

        await provider.send_user_audio(b"\x01\x02\x03\x04")
        # Nothing made it to the wire.
        assert sent == []

        # After resume, audio flows again.
        await provider.resume()
        await provider.send_user_audio(b"\x05\x06\x07\x08")
        assert len(sent) == 1
        assert sent[0]["type"] == ClientEventType.INPUT_AUDIO_BUFFER_APPEND.value

    async def test_audio_delta_dropped_while_suspended(
        self, provider_deps: dict[str, Any]
    ) -> None:
        """A response.audio.delta arriving after our cancel (network race)
        must not reach the coordinator — its audio would play stale on
        resume. This is the worst bug from the spike if not guarded."""
        callbacks = _callbacks()
        provider_deps["callbacks"] = callbacks
        provider = OpenAIRealtimeProvider(**provider_deps)
        provider._ws = AsyncMock()  # needed for suspend to run
        await provider.suspend()

        # Simulate a delta arriving from the wire after suspend.
        pcm = b"\xaa\xbb" * 10
        event = {
            "type": "response.audio.delta",
            "delta": base64.b64encode(pcm).decode("ascii"),
        }
        await provider._handle_server_event(event)

        callbacks.on_audio_delta.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_audio_delta_flows_after_resume(self, provider_deps: dict[str, Any]) -> None:
        callbacks = _callbacks()
        provider_deps["callbacks"] = callbacks
        provider = OpenAIRealtimeProvider(**provider_deps)
        provider._ws = AsyncMock()
        await provider.suspend()
        await provider.resume()

        pcm = b"\xaa\xbb" * 10
        event = {
            "type": "response.audio.delta",
            "delta": base64.b64encode(pcm).decode("ascii"),
        }
        await provider._handle_server_event(event)

        callbacks.on_audio_delta.assert_awaited_once_with(pcm)  # type: ignore[attr-defined]

    async def test_tool_call_dropped_while_suspended(self, provider_deps: dict[str, Any]) -> None:
        """Tool calls from a cancelled response shouldn't dispatch. The
        coordinator's tool-dispatch machinery assumes a live turn context
        that doesn't exist during a claim."""
        callbacks = _callbacks()
        provider_deps["callbacks"] = callbacks
        provider = OpenAIRealtimeProvider(**provider_deps)
        provider._ws = AsyncMock()
        await provider.suspend()

        event = {
            "type": "response.function_call_arguments.done",
            "call_id": "call_xyz",
            "name": "play_audiobook",
            "arguments": '{"title":"ghost"}',
        }
        await provider._handle_server_event(event)

        callbacks.on_tool_call.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_response_done_still_fires_while_suspended(
        self, provider_deps: dict[str, Any]
    ) -> None:
        """Lifecycle events MUST pass through so the coordinator can close
        out a cancelled turn's state. Only content is dropped."""
        callbacks = _callbacks()
        provider_deps["callbacks"] = callbacks
        provider = OpenAIRealtimeProvider(**provider_deps)
        provider._ws = AsyncMock()
        await provider.suspend()

        await provider._handle_server_event({"type": "response.done"})
        callbacks.on_response_done.assert_awaited_once()  # type: ignore[attr-defined]

    async def test_audio_done_still_fires_while_suspended(
        self, provider_deps: dict[str, Any]
    ) -> None:
        callbacks = _callbacks()
        provider_deps["callbacks"] = callbacks
        provider = OpenAIRealtimeProvider(**provider_deps)
        provider._ws = AsyncMock()
        await provider.suspend()

        await provider._handle_server_event({"type": "response.audio.done"})
        callbacks.on_audio_done.assert_awaited_once()  # type: ignore[attr-defined]

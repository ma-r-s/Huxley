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


class TestTranscript:
    async def test_disconnect_saves_summary(self, provider_deps: dict[str, Any]) -> None:
        storage = provider_deps["storage"]
        await storage.init()

        provider = OpenAIRealtimeProvider(**provider_deps)
        provider._transcript_lines = ["Hola", "Quiero leer un libro", "El de García Márquez"]

        await provider.disconnect(save_summary=True)

        summary = await storage.get_latest_summary()
        assert summary is not None
        assert "García Márquez" in summary
        await storage.close()

    async def test_disconnect_without_save(self, provider_deps: dict[str, Any]) -> None:
        storage = provider_deps["storage"]
        await storage.init()

        provider = OpenAIRealtimeProvider(**provider_deps)
        provider._transcript_lines = ["Some transcript"]

        await provider.disconnect(save_summary=False)

        summary = await storage.get_latest_summary()
        assert summary is None
        await storage.close()

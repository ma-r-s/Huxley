"""Tests for the session manager."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from abuel_os.config import Settings
from abuel_os.session.manager import SessionManager
from abuel_os.session.protocol import ClientEventType
from abuel_os.skills import SkillRegistry
from abuel_os.storage.db import Storage
from abuel_os.types import ToolAction, ToolDefinition, ToolResult
from tests.conftest import FakeSkill

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def session_deps(tmp_path: Path) -> dict[str, Any]:
    """Create all SessionManager dependencies."""
    config = Settings(
        openai_api_key="test-key",
        db_path=tmp_path / "test.db",
        audiobook_library_path=tmp_path / "audiobooks",
    )
    registry = SkillRegistry()
    storage = Storage(tmp_path / "test.db")

    return {
        "config": config,
        "skill_registry": registry,
        "storage": storage,
        "on_audio_delta": AsyncMock(),
        "on_tool_action": AsyncMock(),
        "on_session_end": AsyncMock(),
    }


@pytest.fixture
def session(session_deps: dict[str, Any]) -> SessionManager:
    return SessionManager(**session_deps)


class TestSessionManagerState:
    def test_not_connected_initially(self, session: SessionManager) -> None:
        assert not session.is_connected

    def test_not_speaking_initially(self, session: SessionManager) -> None:
        assert not session.is_model_speaking


class TestSessionManagerToolDispatch:
    async def test_function_call_dispatches_to_skill(self, session_deps: dict[str, Any]) -> None:
        skill = FakeSkill(
            name="test_skill",
            tools=[ToolDefinition(name="test_tool", description="Test")],
            result=ToolResult(output='{"result": "ok"}'),
        )
        session_deps["skill_registry"].register(skill)
        session = SessionManager(**session_deps)

        # Simulate a WebSocket that captures sent messages
        sent_messages: list[dict[str, Any]] = []
        mock_ws = AsyncMock()

        async def capture_send(msg: str) -> None:
            sent_messages.append(json.loads(msg))

        mock_ws.send = capture_send
        session._ws = mock_ws

        # Simulate receiving a function call event
        from abuel_os.session.protocol import FunctionCallEvent

        event = FunctionCallEvent(
            call_id="call_123",
            name="test_tool",
            arguments='{"arg": "value"}',
        )
        await session._handle_function_call(event)

        # Verify skill was called
        assert len(skill.handle_calls) == 1
        assert skill.handle_calls[0] == ("test_tool", {"arg": "value"})

        # Verify function output was sent back
        output_msg = sent_messages[0]
        assert output_msg["type"] == ClientEventType.CONVERSATION_ITEM_CREATE.value
        assert output_msg["item"]["call_id"] == "call_123"
        assert output_msg["item"]["output"] == '{"result": "ok"}'

        # Verify response.create was sent to continue
        assert sent_messages[1]["type"] == ClientEventType.RESPONSE_CREATE.value

    async def test_function_call_with_action_defers_orchestrator_notification(
        self, session_deps: dict[str, Any]
    ) -> None:
        """Side effects are deferred until `response.audio.done` fires.

        Before this change `_handle_function_call` fired `on_tool_action`
        synchronously — but that killed the session before the model could
        verbally acknowledge the tool call ("Ahí le pongo el libro, don").
        The action is now staged as `_pending_tool_action` and fired from
        the receive loop once the model finishes speaking.
        """
        skill = FakeSkill(
            name="player",
            tools=[ToolDefinition(name="play", description="Play")],
            result=ToolResult(output="{}", action=ToolAction.START_PLAYBACK),
        )
        session_deps["skill_registry"].register(skill)
        session = SessionManager(**session_deps)

        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        session._ws = mock_ws

        from abuel_os.session.protocol import FunctionCallEvent

        event = FunctionCallEvent(call_id="c1", name="play", arguments="{}")
        await session._handle_function_call(event)

        # The action is staged, not yet fired — the model hasn't finished
        # its acknowledgement yet.
        session_deps["on_tool_action"].assert_not_awaited()
        assert session._pending_tool_action == "start_playback"


class TestSessionManagerAudio:
    async def test_send_audio_base64_encodes(self, session: SessionManager) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        session._ws = mock_ws

        pcm_data = b"\x00\x01\x02\x03"
        await session.send_audio(pcm_data)

        assert len(sent) == 1
        assert sent[0]["type"] == ClientEventType.INPUT_AUDIO_BUFFER_APPEND.value
        decoded = base64.b64decode(sent[0]["audio"])
        assert decoded == pcm_data

    async def test_send_audio_noop_when_disconnected(self, session: SessionManager) -> None:
        # Should not raise
        await session.send_audio(b"\x00\x01")


class TestSessionManagerTranscript:
    async def test_disconnect_saves_summary(
        self, session_deps: dict[str, Any], tmp_path: Path
    ) -> None:
        storage = session_deps["storage"]
        await storage.init()

        session = SessionManager(**session_deps)
        session._transcript_lines = ["Hola", "Quiero leer un libro", "El de García Márquez"]

        await session.disconnect(save_summary=True)

        summary = await storage.get_latest_summary()
        assert summary is not None
        assert "García Márquez" in summary
        await storage.close()

    async def test_disconnect_without_save(
        self, session_deps: dict[str, Any], tmp_path: Path
    ) -> None:
        storage = session_deps["storage"]
        await storage.init()

        session = SessionManager(**session_deps)
        session._transcript_lines = ["Some transcript"]

        await session.disconnect(save_summary=False)

        summary = await storage.get_latest_summary()
        assert summary is None
        await storage.close()

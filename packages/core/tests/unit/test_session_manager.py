"""Tests for the thin-transport SessionManager.

With the v3 turn coordinator in place, the session manager owns only the
WebSocket transport + the initial session.update config. Tool dispatch,
audio sequencing, and interrupt logic live on `TurnCoordinator` — and
are covered by `test_turn_coordinator.py`. The tests here exercise the
surviving transport surface: basic state, audio/function-output sends,
and transcript persistence across disconnect.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from huxley.config import Settings
from huxley.session.manager import SessionManager
from huxley.session.protocol import ClientEventType
from huxley.storage.db import Storage
from huxley_sdk import SkillRegistry

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
        "on_function_call": AsyncMock(),
        "on_response_done": AsyncMock(),
        "on_audio_done": AsyncMock(),
        "on_commit_failed": AsyncMock(),
        "on_session_end": AsyncMock(),
    }


@pytest.fixture
def session(session_deps: dict[str, Any]) -> SessionManager:
    return SessionManager(**session_deps)


class TestSessionManagerState:
    def test_not_connected_initially(self, session: SessionManager) -> None:
        assert not session.is_connected


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
        await session.send_audio(b"\x00\x01")


class TestSessionManagerFunctionOutput:
    """`send_function_output` posts a conversation.item.create (function output)."""

    async def test_send_function_output_posts_conversation_item(
        self, session: SessionManager
    ) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        session._ws = mock_ws

        await session.send_function_output("call_abc", '{"result": "ok"}')

        assert len(sent) == 1
        msg = sent[0]
        assert msg["type"] == ClientEventType.CONVERSATION_ITEM_CREATE.value
        assert msg["item"]["type"] == "function_call_output"
        assert msg["item"]["call_id"] == "call_abc"
        assert msg["item"]["output"] == '{"result": "ok"}'

    async def test_send_function_output_noop_when_disconnected(
        self, session: SessionManager
    ) -> None:
        await session.send_function_output("call_x", "{}")


class TestSessionManagerResponseCreate:
    """`commit_and_respond` and `request_response` both drive `response.create`."""

    async def test_commit_and_respond_sends_commit_then_create(
        self, session: SessionManager
    ) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        session._ws = mock_ws

        await session.commit_and_respond()

        types = [m["type"] for m in sent]
        assert types == [
            ClientEventType.INPUT_AUDIO_BUFFER_COMMIT.value,
            ClientEventType.RESPONSE_CREATE.value,
        ]

    async def test_request_response_only_sends_response_create(
        self, session: SessionManager
    ) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        session._ws = mock_ws

        await session.request_response()

        assert len(sent) == 1
        assert sent[0]["type"] == ClientEventType.RESPONSE_CREATE.value


class TestSessionManagerCancelResponse:
    async def test_cancel_sends_response_cancel_and_buffer_clear(
        self, session: SessionManager
    ) -> None:
        sent: list[dict[str, Any]] = []

        async def capture(msg: str) -> None:
            sent.append(json.loads(msg))

        mock_ws = AsyncMock()
        mock_ws.send = capture
        session._ws = mock_ws

        await session.cancel_response()

        types = [m["type"] for m in sent]
        assert types == [
            ClientEventType.RESPONSE_CANCEL.value,
            ClientEventType.INPUT_AUDIO_BUFFER_CLEAR.value,
        ]


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

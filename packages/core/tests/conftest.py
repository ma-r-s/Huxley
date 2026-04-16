"""Shared test fixtures for the Huxley core package."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from huxley.config import Settings
from huxley.storage.db import Storage
from huxley_sdk import SkillContext, ToolDefinition, ToolResult
from huxley_sdk.testing import FakeSkill

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _dummy_ctx() -> SkillContext:
    """Build a no-op SkillContext for tests that need to call skill.setup(ctx).

    Stage 1 stand-in: in stages 2+ this becomes the real `_build_skill_context`
    helper from app.py. For now skills accept ctx but ignore most fields.
    """
    return SkillContext(
        logger=MagicMock(),
        storage=MagicMock(),
        persona_data_dir=Path("/tmp"),
        config={},
    )


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
async def storage(tmp_db_path: Path) -> AsyncIterator[Storage]:
    s = Storage(tmp_db_path)
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="test-key",
        db_path=tmp_path / "test.db",
        audiobook_library_path=tmp_path / "audiobooks",
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe",
    )


@pytest.fixture
def fake_skill() -> FakeSkill:
    return FakeSkill()


@pytest.fixture
def playback_skill() -> FakeSkill:
    async def noop_stream() -> Any:
        if False:
            yield b""

    return FakeSkill(
        name="audiobooks",
        tools=[
            ToolDefinition(
                name="play_audiobook",
                description="Play an audiobook",
                parameters={
                    "type": "object",
                    "properties": {"book_id": {"type": "string"}},
                },
            ),
        ],
        result=ToolResult(output='{"playing": true}', audio_factory=noop_stream),
    )

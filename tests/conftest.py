"""Shared test fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from abuel_os.config import Settings
from abuel_os.storage.db import Storage
from abuel_os.types import ToolAction, ToolDefinition, ToolResult

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
async def storage(tmp_db_path: Path) -> Storage:
    s = Storage(tmp_db_path)
    await s.init()
    yield s  # type: ignore[misc]
    await s.close()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="test-key",
        db_path=tmp_path / "test.db",
        audiobook_library_path=tmp_path / "audiobooks",
        mpv_socket_path=str(tmp_path / "mpv.sock"),
    )


class FakeSkill:
    """A minimal skill implementation for testing the registry."""

    def __init__(
        self,
        name: str = "fake",
        tools: list[ToolDefinition] | None = None,
        result: ToolResult | None = None,
    ) -> None:
        self._name = name
        self._tools = tools or [
            ToolDefinition(
                name="fake_tool",
                description="A fake tool for testing",
                parameters={
                    "type": "object",
                    "properties": {"arg": {"type": "string"}},
                },
            )
        ]
        self._result = result or ToolResult(output='{"ok": true}')
        self.handle_calls: list[tuple[str, dict[str, Any]]] = []
        self.setup_called = False
        self.teardown_called = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def tools(self) -> list[ToolDefinition]:
        return self._tools

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        self.handle_calls.append((tool_name, args))
        return self._result

    async def setup(self) -> None:
        self.setup_called = True

    async def teardown(self) -> None:
        self.teardown_called = True


@pytest.fixture
def fake_skill() -> FakeSkill:
    return FakeSkill()


@pytest.fixture
def playback_skill() -> FakeSkill:
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
        result=ToolResult(output='{"playing": true}', action=ToolAction.START_PLAYBACK),
    )

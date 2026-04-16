"""Test utilities for Huxley SDK consumers.

`FakeSkill` is the minimal Skill-protocol implementation used across the
test suite (registry tests, coordinator tests, integration tests). Importing
from `huxley_sdk.testing` is intentional — these helpers are not part of
the runtime SDK surface.
"""

from __future__ import annotations

from typing import Any

from huxley_sdk.types import SkillContext, ToolDefinition, ToolResult


class FakeSkill:
    """A minimal Skill-protocol implementation for testing.

    Tracks `setup_called`, `teardown_called`, and `handle_calls` for
    assertions. Defaults to a single tool `fake_tool` and a successful
    `ToolResult`. Override via constructor args for custom shapes.
    """

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
        self.setup_context: SkillContext | None = None
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

    async def setup(self, ctx: SkillContext) -> None:
        self.setup_called = True
        self.setup_context = ctx

    async def teardown(self) -> None:
        self.teardown_called = True

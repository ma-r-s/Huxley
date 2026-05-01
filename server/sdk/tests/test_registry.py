"""Tests for the skill registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

from huxley_sdk import (
    SkillContext,
    SkillNotFoundError,
    SkillRegistry,
    ToolDefinition,
    ToolResult,
)
from huxley_sdk.testing import FakeSkill


def _ctx(name: str) -> SkillContext:
    """Build a no-op SkillContext for testing setup_all."""
    return SkillContext(
        logger=MagicMock(),
        storage=MagicMock(),
        secrets=MagicMock(),
        persona_data_dir=Path("/tmp"),
        config={},
    )


class TestSkillRegistration:
    def test_register_skill(self) -> None:
        registry = SkillRegistry()
        skill = FakeSkill()
        registry.register(skill)
        assert "fake" in registry.skill_names
        assert "fake_tool" in registry.tool_names

    def test_duplicate_tool_name_raises(self) -> None:
        registry = SkillRegistry()
        skill1 = FakeSkill(name="skill_a")
        skill2 = FakeSkill(name="skill_b")  # same tool name "fake_tool"
        registry.register(skill1)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(skill2)

    def test_multiple_skills_with_different_tools(self) -> None:
        registry = SkillRegistry()
        skill_a = FakeSkill(
            name="a",
            tools=[ToolDefinition(name="tool_a", description="A")],
        )
        skill_b = FakeSkill(
            name="b",
            tools=[ToolDefinition(name="tool_b", description="B")],
        )
        registry.register(skill_a)
        registry.register(skill_b)
        assert set(registry.tool_names) == {"tool_a", "tool_b"}


class TestToolDefinitions:
    def test_get_all_tool_definitions_format(self) -> None:
        registry = SkillRegistry()
        registry.register(FakeSkill())
        defs = registry.get_all_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["type"] == "function"
        assert defs[0]["name"] == "fake_tool"
        assert "description" in defs[0]
        assert "parameters" in defs[0]

    def test_empty_registry_returns_empty(self) -> None:
        registry = SkillRegistry()
        assert registry.get_all_tool_definitions() == []


class TestDispatch:
    async def test_dispatch_to_correct_skill(self) -> None:
        registry = SkillRegistry()
        skill = FakeSkill()
        registry.register(skill)
        result = await registry.dispatch("fake_tool", {"arg": "value"})
        assert result.output == '{"ok": true}'
        assert skill.handle_calls == [("fake_tool", {"arg": "value"})]

    async def test_dispatch_unknown_tool_raises(self) -> None:
        registry = SkillRegistry()
        with pytest.raises(SkillNotFoundError, match="no_such_tool"):
            await registry.dispatch("no_such_tool", {})

    async def test_dispatch_preserves_side_effect(self) -> None:
        from huxley_sdk import AudioStream

        async def stub_stream() -> Any:
            if False:
                yield b""

        registry = SkillRegistry()
        skill = FakeSkill(
            name="player",
            tools=[ToolDefinition(name="play", description="Play")],
            result=ToolResult(output="{}", side_effect=AudioStream(factory=stub_stream)),
        )
        registry.register(skill)
        result = await registry.dispatch("play", {})
        assert isinstance(result.side_effect, AudioStream)
        assert result.side_effect.factory is stub_stream


class TestLifecycle:
    async def test_setup_all_passes_context(self) -> None:
        registry = SkillRegistry()
        skill = FakeSkill()
        registry.register(skill)
        await registry.setup_all(_ctx)
        assert skill.setup_called
        assert skill.setup_context is not None

    async def test_teardown_all(self) -> None:
        registry = SkillRegistry()
        skill = FakeSkill()
        registry.register(skill)
        await registry.teardown_all()
        assert skill.teardown_called


class TestPromptContext:
    """T3 #96 — prompt_context() is in the Skill Protocol with empty default."""

    def test_skill_without_prompt_context_contributes_nothing(self) -> None:
        # FakeSkill doesn't define prompt_context; registry returns "".
        registry = SkillRegistry()
        registry.register(FakeSkill(name="quiet"))
        assert registry.get_prompt_context() == ""

    def test_skill_with_prompt_context_contributes_text(self) -> None:
        class TalkativeSkill:
            name = "loud"
            tools: ClassVar[list[ToolDefinition]] = []

            async def setup(self, _ctx: Any) -> None: ...
            async def teardown(self) -> None: ...
            async def handle(self, _name: str, _args: dict[str, Any]) -> ToolResult:
                return ToolResult(output="{}")

            def prompt_context(self) -> str:
                return "I have things to say"

        registry = SkillRegistry()
        registry.register(TalkativeSkill())
        assert registry.get_prompt_context() == "I have things to say"

    def test_multiple_contributions_joined_with_blank_line(self) -> None:
        class A:
            name = "a"
            tools: ClassVar[list[ToolDefinition]] = []

            async def setup(self, _ctx: Any) -> None: ...
            async def teardown(self) -> None: ...
            async def handle(self, _name: str, _args: dict[str, Any]) -> ToolResult:
                return ToolResult(output="{}")

            def prompt_context(self) -> str:
                return "first"

        class B:
            name = "b"
            tools: ClassVar[list[ToolDefinition]] = []

            async def setup(self, _ctx: Any) -> None: ...
            async def teardown(self) -> None: ...
            async def handle(self, _name: str, _args: dict[str, Any]) -> ToolResult:
                return ToolResult(output="{}")

            def prompt_context(self) -> str:
                return "second"

        registry = SkillRegistry()
        registry.register(A())
        registry.register(B())
        assert registry.get_prompt_context() == "first\n\nsecond"

    def test_empty_string_contribution_is_filtered(self) -> None:
        class Silent:
            name = "silent"
            tools: ClassVar[list[ToolDefinition]] = []

            async def setup(self, _ctx: Any) -> None: ...
            async def teardown(self) -> None: ...
            async def handle(self, _name: str, _args: dict[str, Any]) -> ToolResult:
                return ToolResult(output="{}")

            def prompt_context(self) -> str:
                return ""

        registry = SkillRegistry()
        registry.register(Silent())
        assert registry.get_prompt_context() == ""

"""Skill registry — collects tool definitions and routes tool calls."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from abuel_os.types import Skill, ToolResult

logger = structlog.get_logger()


class SkillNotFoundError(Exception):
    """Raised when a tool call cannot be routed to any registered skill."""


class SkillRegistry:
    """Collects tools from registered skills and dispatches tool calls.

    Usage:
        registry = SkillRegistry()
        registry.register(audiobooks_skill)
        registry.register(system_skill)

        # Get all tool schemas for session.update
        tools = registry.get_all_tool_definitions()

        # Route an incoming tool call
        result = await registry.dispatch("search_audiobooks", {"query": "García Márquez"})
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._tool_to_skill: dict[str, str] = {}

    def register(self, skill: Skill) -> None:
        """Register a skill. Its tools become available for dispatch."""
        self._skills[skill.name] = skill
        for tool in skill.tools:
            if tool.name in self._tool_to_skill:
                existing = self._tool_to_skill[tool.name]
                msg = (
                    f"Tool '{tool.name}' already registered by skill '{existing}', "
                    f"cannot register again from skill '{skill.name}'"
                )
                raise ValueError(msg)
            self._tool_to_skill[tool.name] = skill.name

    def get_all_tool_definitions(self) -> list[dict[str, Any]]:
        """Return all tool schemas in OpenAI API format for session.update."""
        definitions: list[dict[str, Any]] = []
        for skill in self._skills.values():
            definitions.extend(tool.to_api_format() for tool in skill.tools)
        return definitions

    async def dispatch(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        """Route a tool call to the correct skill handler."""
        skill_name = self._tool_to_skill.get(tool_name)
        if skill_name is None:
            msg = f"No skill registered for tool '{tool_name}'"
            raise SkillNotFoundError(msg)

        skill = self._skills[skill_name]
        await logger.ainfo(
            "tool_dispatch",
            tool=tool_name,
            skill=skill_name,
            args=args,
        )
        return await skill.handle(tool_name, args)

    async def setup_all(self) -> None:
        """Call setup() on all registered skills."""
        for skill in self._skills.values():
            await skill.setup()

    async def teardown_all(self) -> None:
        """Call teardown() on all registered skills."""
        for skill in self._skills.values():
            await skill.teardown()

    @property
    def skill_names(self) -> list[str]:
        return list(self._skills.keys())

    @property
    def tool_names(self) -> list[str]:
        return list(self._tool_to_skill.keys())

"""Skill registry — collects tool definitions and routes tool calls."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from huxley_sdk.types import Skill, SkillContext, ToolResult


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
        """Route a tool call to the correct skill handler.

        No `tool_dispatch` log emitted here — `coord.tool_dispatch` covers
        the same event with richer context (turn, state, has_factory).
        Adding one here would duplicate the line in every turn's log.
        """
        skill_name = self._tool_to_skill.get(tool_name)
        if skill_name is None:
            msg = f"No skill registered for tool '{tool_name}'"
            raise SkillNotFoundError(msg)
        return await self._skills[skill_name].handle(tool_name, args)

    def get_prompt_context(self) -> str:
        """Collect optional prompt context contributed by registered skills.

        `prompt_context()` is part of the `Skill` Protocol with a
        default empty implementation (T3 #96). Skills override it to
        inject baseline awareness into the system prompt at
        session-connect time — e.g. the audiobooks skill lists its
        catalog so the model can resolve title fuzzy-matches in one
        shot. Empty contributions are filtered, so skills that have
        nothing to add inherit the default and pay no cost.

        Falls back to `getattr` for backward compatibility with skills
        from before this method was promoted to the Protocol — they may
        still expose `prompt_context` without inheriting the new
        default. Remove the fallback once all first-party skills are
        confirmed to use the new shape.
        """
        parts: list[str] = []
        for skill in self._skills.values():
            getter = getattr(skill, "prompt_context", None)
            if callable(getter):
                ctx = getter()
                if ctx:
                    parts.append(ctx)
        return "\n\n".join(parts)

    async def setup_all(
        self,
        build_context: Callable[[str], SkillContext],
    ) -> None:
        """Call setup(ctx) on all registered skills.

        `build_context` receives the skill name and returns its `SkillContext`.
        The framework supplies this callable; it knows how to wire each skill's
        logger / storage namespace / config.
        """
        for name, skill in self._skills.items():
            await skill.setup(build_context(name))

    async def reconfigure_all(
        self,
        build_context: Callable[[str], SkillContext],
    ) -> None:
        """Call reconfigure(ctx) on every skill with a fresh context.

        The framework fires this on every session connect with a
        `build_context` that yields language-resolved configs (per-skill
        `i18n.<language>` overrides merged in, `language` set to the
        active code). Skills refresh language-dependent state so the
        next `tools` property access reflects the active language.

        Uses `getattr` so skills from before `reconfigure` was added to
        the Protocol continue to work — if absent, it's a silent no-op.
        Remove the fallback once all first-party skills confirm they
        inherit the default.
        """
        for name, skill in self._skills.items():
            reconf = getattr(skill, "reconfigure", None)
            if callable(reconf):
                await reconf(build_context(name))

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

    @property
    def skills(self) -> list[Skill]:
        """All registered skills in declaration order. Used by the
        framework for cross-skill discovery (e.g., finding the call-
        hooks provider in `Application._wire_call_hooks_if_any`)."""
        return list(self._skills.values())

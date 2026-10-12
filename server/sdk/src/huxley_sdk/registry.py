"""Skill registry — collects tool definitions and routes tool calls."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from huxley_sdk.types import Skill, SkillContext, ToolDefinition, ToolResult


class SkillNotFoundError(Exception):
    """Raised when a tool call cannot be routed to any registered skill."""


class SkillUnavailableError(Exception):
    """Raised when a tool call routes to a skill that was disabled because
    its `setup()` raised. The framework turns it into an error tool output
    the model speaks, like any other failing tool call."""


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
        # Tool list each skill reported at registration. Used for disabled
        # skills so a half-set-up skill's `tools` property is never called.
        self._registered_tools: dict[str, list[ToolDefinition]] = {}
        # Skills whose setup() raised under
        # `setup_all(on_skill_disabled=...)`. Already torn down; they stay
        # registered (tools still offered) but are skipped everywhere
        # else; see `setup_all`.
        self._disabled: set[str] = set()

    def register(self, skill: Skill) -> None:
        """Register a skill. Its tools become available for dispatch."""
        self._skills[skill.name] = skill
        tools = list(skill.tools)
        self._registered_tools[skill.name] = tools
        for tool in tools:
            if tool.name in self._tool_to_skill:
                existing = self._tool_to_skill[tool.name]
                msg = (
                    f"Tool '{tool.name}' already registered by skill '{existing}', "
                    f"cannot register again from skill '{skill.name}'"
                )
                raise ValueError(msg)
            self._tool_to_skill[tool.name] = skill.name

    def get_all_tool_definitions(self) -> list[dict[str, Any]]:
        """Return all tool schemas in OpenAI API format for session.update.

        Includes the tools of disabled skills on purpose: the persona's
        prompt may name them, and a model told about a capability with no
        tool behind it can claim it acted when nothing happened. Calling
        one raises `SkillUnavailableError` in `dispatch`, which the
        framework turns into an error the model says out loud. A disabled
        skill's tools come from the list it reported at registration.
        """
        definitions: list[dict[str, Any]] = []
        for name, skill in self._skills.items():
            tools = self._registered_tools[name] if name in self._disabled else skill.tools
            definitions.extend(tool.to_api_format() for tool in tools)
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
        if skill_name in self._disabled:
            msg = f"Skill '{skill_name}' is disabled (its setup failed); tool '{tool_name}'"
            raise SkillUnavailableError(msg)
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
        for skill in self.skills:  # disabled skills contribute nothing
            getter = getattr(skill, "prompt_context", None)
            if callable(getter):
                ctx = getter()
                if ctx:
                    parts.append(ctx)
        return "\n\n".join(parts)

    async def setup_all(
        self,
        build_context: Callable[[str], SkillContext],
        *,
        on_skill_disabled: Callable[[str, BaseException], Awaitable[None]] | None = None,
        on_teardown_error: Callable[[str, BaseException], Awaitable[None]] | None = None,
    ) -> None:
        """Call setup(ctx) on all registered skills.

        `build_context` receives the skill name and returns its `SkillContext`.
        The framework supplies this callable; it knows how to wire each skill's
        logger / storage namespace / config.

        With `on_skill_disabled`, one skill's failing setup never stops
        the rest. The skill is disabled, `on_skill_disabled(name, exc)` is
        awaited, the skill's `teardown()` runs right away (so tasks or
        handlers its partial setup started stop now; a raising teardown
        goes to `on_teardown_error`), and the loop moves on. A disabled
        skill drops out of `skills`, `skill_names`, `get_prompt_context`,
        `reconfigure_all` and `teardown_all`; its tools stay offered but
        `dispatch` raises `SkillUnavailableError` for them. Without
        `on_skill_disabled` the first failure propagates and nothing is
        disabled, so a caller that can't report a failure never loses it.
        2026-10-12: one skill's failed setup took a whole persona down
        for 55 s before this.
        """
        for name, skill in self._skills.items():
            try:
                await skill.setup(build_context(name))
            except Exception as exc:
                if on_skill_disabled is None:
                    raise
                self._disabled.add(name)
                try:
                    await on_skill_disabled(name, exc)
                finally:
                    await self._teardown_one(name, skill, on_teardown_error)

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
        inherit the default. Disabled skills are skipped: their setup
        never completed, so there is nothing consistent to refresh.
        """
        for name, skill in self._skills.items():
            if name in self._disabled:
                continue
            reconf = getattr(skill, "reconfigure", None)
            if callable(reconf):
                await reconf(build_context(name))

    async def teardown_all(
        self,
        on_error: Callable[[str, BaseException], Awaitable[None]] | None = None,
    ) -> None:
        """Call `teardown()` on every registered skill, resilient to
        individual failures.

        One skill's teardown raising must NEVER block the rest — that's
        the difference between a single buggy skill and a half-torn-
        down framework where some background tasks survive shutdown
        and some don't. The optional `on_error` callback receives
        `(skill_name, exception)` for each failure so the framework can
        log via its preferred channel (the SDK can't depend on
        structlog).

        If `on_error` is None, failures are silently swallowed — the
        framework should always pass a handler. Made optional only so
        a test fixture without a logger doesn't have to wire one.
        Round-3 review (2026-04-29) noted the previous unguarded loop
        could leave skills B+C up while A's teardown threw, with their
        background tasks still running until `task_supervisor.stop()`
        cancelled them later in the shutdown — silent inconsistency.

        Disabled skills are skipped: `setup_all` tore them down when it
        disabled them.
        """
        for name, skill in self._skills.items():
            if name in self._disabled:
                continue
            await self._teardown_one(name, skill, on_error)

    async def _teardown_one(
        self,
        name: str,
        skill: Skill,
        on_error: Callable[[str, BaseException], Awaitable[None]] | None,
    ) -> None:
        # T1.18 — `teardown` is optional per the Skill Protocol's
        # documented contract ("Optional methods (`prompt_context`,
        # `setup`, `teardown`) have empty defaults"). The Protocol
        # method body provides a default at type-check time but does
        # NOT inject a runtime method on classes that don't inherit
        # from Skill (most skills are structurally typed — e.g.
        # `class StocksSkill:` with no base class). Use the same
        # `getattr`-then-callable pattern as `reconfigure_all` above
        # so a skill without a teardown silently no-ops instead of
        # raising AttributeError mid-shutdown — which previously
        # aborted the loop and left later skills' background tasks /
        # clients dangling until the supervisor cancelled them.
        teardown = getattr(skill, "teardown", None)
        if not callable(teardown):
            return
        try:
            await teardown()
        except Exception as exc:
            if on_error is not None:
                await on_error(name, exc)

    @property
    def skill_names(self) -> list[str]:
        """Names of the working skills (registered and not disabled)."""
        return [name for name in self._skills if name not in self._disabled]

    @property
    def disabled_skill_names(self) -> list[str]:
        """Names of skills disabled because their setup raised."""
        return [name for name in self._skills if name in self._disabled]

    @property
    def tool_names(self) -> list[str]:
        """Every registered tool, including those of disabled skills
        (they are still offered; see `get_all_tool_definitions`)."""
        return list(self._tool_to_skill.keys())

    @property
    def skills(self) -> list[Skill]:
        """Working skills (not disabled) in declaration order. Used by the
        framework for cross-skill discovery and for persisting each
        skill's data_schema_version after a successful setup."""
        return [skill for name, skill in self._skills.items() if name not in self._disabled]

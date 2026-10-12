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
    SkillUnavailableError,
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

    async def test_teardown_all_skips_skills_without_teardown(self) -> None:
        """T1.18 regression: a structurally-typed skill (no base
        class, no `teardown` method defined) must NOT abort
        `teardown_all` mid-loop with AttributeError.

        Pre-T1.18 the registry called `await skill.teardown()`
        unconditionally, raising AttributeError on classes that
        relied on the Skill Protocol's documented "optional with
        default" semantics — Python Protocol method bodies are
        type-check defaults, not runtime injections. A real-world
        offender (StocksSkill) had no teardown-able state but
        still crashed every persona swap.

        This test pins the fix: a skill without `teardown`
        registered alongside one that DOES define teardown — the
        teardown-having skill's cleanup must still run, the
        teardown-less one is silently skipped."""

        class _NoTeardownSkill:
            name = "no_teardown"
            tools: ClassVar[list[ToolDefinition]] = []

            async def setup(self, _ctx: Any) -> None: ...
            async def handle(self, _name: str, _args: dict[str, Any]) -> ToolResult:
                return ToolResult(output="{}")

        registry = SkillRegistry()
        no_teardown = _NoTeardownSkill()
        with_teardown = FakeSkill(name="cleans_up")
        registry.register(no_teardown)
        registry.register(with_teardown)

        # Must NOT raise AttributeError.
        errors: list[tuple[str, BaseException]] = []

        async def _on_error(name: str, exc: BaseException) -> None:
            errors.append((name, exc))

        await registry.teardown_all(on_error=_on_error)

        # The teardown-having skill still got cleaned up, even
        # though it was registered AFTER the teardown-less one.
        assert with_teardown.teardown_called
        # No error path fired — the missing-teardown case is a
        # clean skip, not a swallowed exception.
        assert errors == []

    async def test_teardown_all_continues_past_a_raising_teardown(self) -> None:
        """Companion to the above: a teardown that raises must
        also not abort the loop (existing behavior, regression-
        pinned here so the T1.18 getattr-guard refactor doesn't
        accidentally drop it)."""

        class _RaisingSkill:
            name = "raises"
            tools: ClassVar[list[ToolDefinition]] = []

            async def setup(self, _ctx: Any) -> None: ...
            async def teardown(self) -> None:
                msg = "boom"
                raise RuntimeError(msg)

            async def handle(self, _name: str, _args: dict[str, Any]) -> ToolResult:
                return ToolResult(output="{}")

        registry = SkillRegistry()
        raising = _RaisingSkill()
        with_teardown = FakeSkill(name="cleans_up")
        registry.register(raising)
        registry.register(with_teardown)

        errors: list[tuple[str, BaseException]] = []

        async def _on_error(name: str, exc: BaseException) -> None:
            errors.append((name, exc))

        await registry.teardown_all(on_error=_on_error)

        assert with_teardown.teardown_called
        assert len(errors) == 1
        assert errors[0][0] == "raises"
        assert isinstance(errors[0][1], RuntimeError)


class _BrokenSetupSkill(FakeSkill):
    """A skill whose setup raises, like Telegram's on 2026-10-12 when
    its session DB was still locked by the previous server process.
    Records lifecycle calls so the tests can show a disabled skill is
    torn down once and otherwise left alone. Its `tools` property
    raises once setup has failed, like a skill whose tool list depends
    on state setup never finished building."""

    def __init__(self, *, teardown_error: Exception | None = None) -> None:
        super().__init__(
            name="broken",
            tools=[ToolDefinition(name="broken_tool", description="Broken")],
        )
        self.reconfigure_called = False
        self.teardown_calls = 0
        self.events: list[str] | None = None
        self._teardown_error = teardown_error

    @property
    def tools(self) -> list[ToolDefinition]:
        if self.setup_called:
            msg = "tools need setup state"
            raise AttributeError(msg)
        return self._tools

    async def setup(self, ctx: SkillContext) -> None:
        self.setup_called = True
        msg = "database is locked"
        raise RuntimeError(msg)

    async def reconfigure(self, ctx: SkillContext) -> None:
        self.reconfigure_called = True

    def prompt_context(self) -> str:
        return "broken skill context"

    async def teardown(self) -> None:
        self.teardown_calls += 1
        if self.events is not None:
            self.events.append("broken.teardown")
        if self._teardown_error is not None:
            raise self._teardown_error


class _OrderedSkill(FakeSkill):
    """FakeSkill that records its setup into a shared event list."""

    def __init__(self, name: str, events: list[str]) -> None:
        super().__init__(name=name, tools=[ToolDefinition(name=f"t_{name}", description="")])
        self._events = events

    async def setup(self, ctx: SkillContext) -> None:
        self._events.append(f"{self.name}.setup")
        await super().setup(ctx)


async def _ignore(_name: str, _exc: BaseException) -> None:
    return None


class TestSetupIsolation:
    """One skill's failing setup must not take the persona down: with
    `on_skill_disabled`, the registry disables that skill, tears it
    down, and sets up the rest."""

    @staticmethod
    def _registry(
        broken: _BrokenSetupSkill | None = None,
    ) -> tuple[SkillRegistry, FakeSkill, _BrokenSetupSkill, FakeSkill]:
        registry = SkillRegistry()
        before = FakeSkill(name="before", tools=[ToolDefinition(name="t_before", description="")])
        broken = broken or _BrokenSetupSkill()
        after = FakeSkill(name="after", tools=[ToolDefinition(name="t_after", description="")])
        for skill in (before, broken, after):
            registry.register(skill)
        return registry, before, broken, after

    async def test_failing_setup_disables_only_that_skill(self) -> None:
        registry, before, broken, after = self._registry()
        disabled: list[tuple[str, BaseException]] = []

        async def _on_disabled(name: str, exc: BaseException) -> None:
            disabled.append((name, exc))

        await registry.setup_all(_ctx, on_skill_disabled=_on_disabled)

        # Skills on both sides of the failure were set up.
        assert before.setup_called
        assert after.setup_called
        assert broken.setup_called
        assert [(name, str(exc)) for name, exc in disabled] == [("broken", "database is locked")]
        assert registry.skill_names == ["before", "after"]
        assert registry.disabled_skill_names == ["broken"]
        assert registry.skills == [before, after]

    async def test_disabled_skill_is_torn_down_at_once_and_only_once(self) -> None:
        """Tasks or handlers a partial setup started stop right away, not
        at shutdown; `teardown_all` then leaves the skill alone."""
        events: list[str] = []
        registry = SkillRegistry()
        broken = _BrokenSetupSkill()
        broken.events = events
        registry.register(_OrderedSkill("first", events))
        registry.register(broken)
        registry.register(_OrderedSkill("last", events))

        await registry.setup_all(_ctx, on_skill_disabled=_ignore)
        assert events == ["first.setup", "broken.teardown", "last.setup"]

        await registry.teardown_all()
        assert broken.teardown_calls == 1

    async def test_raising_teardown_of_disabled_skill_is_reported(self) -> None:
        registry, _, _, after = self._registry(
            _BrokenSetupSkill(teardown_error=RuntimeError("teardown boom"))
        )
        teardown_errors: list[tuple[str, BaseException]] = []

        async def _on_teardown_error(name: str, exc: BaseException) -> None:
            teardown_errors.append((name, exc))

        await registry.setup_all(
            _ctx, on_skill_disabled=_ignore, on_teardown_error=_on_teardown_error
        )

        assert [(name, str(exc)) for name, exc in teardown_errors] == [("broken", "teardown boom")]
        assert after.setup_called

    async def test_teardown_runs_even_if_the_disabled_handler_raises(self) -> None:
        registry, _, broken, _ = self._registry()

        async def _raising(_name: str, _exc: BaseException) -> None:
            msg = "handler boom"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="handler boom"):
            await registry.setup_all(_ctx, on_skill_disabled=_raising)
        assert broken.teardown_calls == 1
        assert registry.disabled_skill_names == ["broken"]

    async def test_disabled_skill_tools_stay_offered_but_raise_unavailable(self) -> None:
        """The tools stay in session.update (the persona prompt may name
        them) and a call answers with SkillUnavailableError, which the
        coordinator turns into a spoken apology. The definitions come
        from registration, so the broken skill's `tools` is not called."""
        registry, _, broken, _ = self._registry()

        await registry.setup_all(_ctx, on_skill_disabled=_ignore)

        offered = [d["name"] for d in registry.get_all_tool_definitions()]
        assert offered == ["t_before", "broken_tool", "t_after"]
        assert "broken_tool" in registry.tool_names
        with pytest.raises(SkillUnavailableError, match="broken"):
            await registry.dispatch("broken_tool", {})
        assert broken.handle_calls == []
        # Working skills still dispatch.
        result = await registry.dispatch("t_after", {})
        assert result.output == '{"ok": true}'

    async def test_disabled_skill_skipped_by_prompt_and_reconfigure(self) -> None:
        registry, _, broken, _ = self._registry()

        await registry.setup_all(_ctx, on_skill_disabled=_ignore)
        await registry.reconfigure_all(_ctx)

        assert registry.get_prompt_context() == ""
        assert not broken.reconfigure_called

    async def test_without_handler_the_failure_propagates(self) -> None:
        """No handler, no isolation: a caller that can't report the
        failure must not have it swallowed."""
        registry, _, broken, after = self._registry()
        with pytest.raises(RuntimeError, match="database is locked"):
            await registry.setup_all(_ctx)
        assert not after.setup_called
        assert registry.disabled_skill_names == []
        assert broken.teardown_calls == 0


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

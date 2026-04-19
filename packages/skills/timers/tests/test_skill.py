"""Unit tests for `huxley-skill-timers`."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from huxley_sdk.testing import make_test_context
from huxley_skill_timers.skill import TimersSkill


async def _setup_skill(
    inject_turn: AsyncMock | None = None,
) -> tuple[TimersSkill, AsyncMock]:
    """Build a TimersSkill wired to a recording `inject_turn` mock."""
    skill = TimersSkill()
    inject_mock = inject_turn or AsyncMock()
    ctx = make_test_context()
    # `make_test_context` populates a no-op inject_turn; override it with
    # the recording mock so tests can assert on what the timer fired.
    object.__setattr__(ctx, "inject_turn", inject_mock)
    await skill.setup(ctx)
    return skill, inject_mock


class TestSetTimer:
    async def test_schedules_and_fires_inject_turn(self) -> None:
        skill, inject_mock = await _setup_skill()

        # Use a very short sleep so the test runs fast.
        result = await skill.handle(
            "set_timer", {"seconds": 1, "message": "sacar la ropa de la lavadora"}
        )

        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["timer_id"] == 1
        assert payload["seconds"] == 1

        # Wait long enough for the timer to fire.
        await asyncio.sleep(1.1)
        inject_mock.assert_awaited_once()
        prompt = inject_mock.await_args.args[0]
        # Prompt shape (imperative, LLM-friendly) contains both the message
        # and an instruction to the model to narrate warmly. Asserting on
        # specific wording would be brittle; these invariants are what
        # matter: the user's message is there, and it's framed as an
        # instruction so the LLM doesn't minimally echo it.
        assert "sacar la ropa de la lavadora" in prompt
        assert "temporizador" in prompt.lower() or "recuerd" in prompt.lower()

    async def test_clamps_below_minimum(self) -> None:
        skill, _ = await _setup_skill()
        result = await skill.handle("set_timer", {"seconds": 0, "message": "ya"})
        payload = json.loads(result.output)
        # Should clamp to the minimum (1s).
        assert payload["seconds"] == 1
        await skill.teardown()

    async def test_clamps_above_maximum(self) -> None:
        skill, _ = await _setup_skill()
        result = await skill.handle("set_timer", {"seconds": 99999, "message": "nunca"})
        payload = json.loads(result.output)
        assert payload["seconds"] == 3600
        await skill.teardown()

    async def test_rejects_empty_message(self) -> None:
        skill, inject_mock = await _setup_skill()
        result = await skill.handle("set_timer", {"seconds": 60, "message": ""})
        payload = json.loads(result.output)
        assert "error" in payload
        # No task should have been scheduled.
        assert skill._handles == {}
        inject_mock.assert_not_awaited()

    async def test_rejects_non_int_seconds(self) -> None:
        skill, _ = await _setup_skill()
        result = await skill.handle("set_timer", {"seconds": "a lot", "message": "x"})
        payload = json.loads(result.output)
        assert "error" in payload

    async def test_multiple_timers_get_unique_ids(self) -> None:
        skill, _ = await _setup_skill()
        r1 = await skill.handle("set_timer", {"seconds": 60, "message": "uno"})
        r2 = await skill.handle("set_timer", {"seconds": 60, "message": "dos"})
        assert json.loads(r1.output)["timer_id"] == 1
        assert json.loads(r2.output)["timer_id"] == 2
        await skill.teardown()


class TestTeardown:
    async def test_teardown_cancels_pending_timers(self) -> None:
        skill, inject_mock = await _setup_skill()

        # Schedule a timer that would fire in an hour (won't in this test).
        await skill.handle("set_timer", {"seconds": 3600, "message": "nunca"})
        assert len(skill._handles) == 1

        await skill.teardown()

        assert skill._handles == {}
        inject_mock.assert_not_awaited()

    async def test_teardown_idempotent_when_no_timers(self) -> None:
        skill, _ = await _setup_skill()
        await skill.teardown()
        await skill.teardown()  # second call must not raise


class TestPromptContext:
    async def test_empty_when_no_timers(self) -> None:
        skill, _ = await _setup_skill()
        assert skill.prompt_context() == ""

    async def test_singular_when_one_timer(self) -> None:
        skill, _ = await _setup_skill()
        await skill.handle("set_timer", {"seconds": 3600, "message": "x"})
        ctx = skill.prompt_context()
        assert "1 temporizador activo" in ctx
        await skill.teardown()

    async def test_plural_when_multiple_timers(self) -> None:
        skill, _ = await _setup_skill()
        await skill.handle("set_timer", {"seconds": 3600, "message": "a"})
        await skill.handle("set_timer", {"seconds": 3600, "message": "b"})
        ctx = skill.prompt_context()
        assert "2 temporizadores activos" in ctx
        await skill.teardown()


class TestUnknownTool:
    async def test_returns_error_payload(self) -> None:
        skill, _ = await _setup_skill()
        result = await skill.handle("fake_tool", {})
        payload = json.loads(result.output)
        assert "error" in payload
        assert "fake_tool" in payload["error"]


class TestTimerFiresDuringNormalFlow:
    async def test_fire_clears_task_from_tracking(self) -> None:
        """After a timer fires, it's removed from `_handles` so teardown
        doesn't try to cancel an already-completed task."""
        skill, _ = await _setup_skill()
        await skill.handle("set_timer", {"seconds": 1, "message": "bien"})
        assert len(skill._handles) == 1

        await asyncio.sleep(1.2)
        # The fire path's `finally` clears the entry.
        assert skill._handles == {}

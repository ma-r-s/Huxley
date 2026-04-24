"""Unit tests for `huxley-skill-timers`."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from huxley_sdk.testing import _NoopSkillStorage, make_test_context
from huxley_skill_timers.skill import TimersSkill

if TYPE_CHECKING:
    from huxley_sdk import SkillStorage


async def _instant_sleep(_seconds: float) -> None:
    """Zero-wall-clock stand-in for `asyncio.sleep` used in `_fire_after`.

    One `asyncio.sleep(0)` yields the event loop so the scheduled
    background task can interleave with the test's own await points.
    Tests that want to observe the fire path completing can follow
    up with `await _drain()` (a few more zero-sleeps).
    """
    await asyncio.sleep(0)


async def _drain(ticks: int = 5) -> None:
    """Yield the event loop `ticks` times so any pending background
    task (spawned by `ctx.background_task`) reaches completion. More
    deterministic than `asyncio.sleep(0.05)` and much faster than
    the original `asyncio.sleep(1.1)` waits the persistence tests
    started with."""
    for _ in range(ticks):
        await asyncio.sleep(0)


async def _setup_skill(
    inject_turn: AsyncMock | None = None,
    config: dict[str, object] | None = None,
    storage: SkillStorage | None = None,
    *,
    real_sleep: bool = False,
    language: str = "es",
) -> tuple[TimersSkill, AsyncMock]:
    """Build a TimersSkill wired to a recording `inject_turn` mock.

    Tests default to an instant `_sleep` stub so the suite doesn't
    burn wall-clock time. Pass `real_sleep=True` for tests that
    specifically need the sleep duration to matter (there's only one
    today: the original happy-path end-to-end test). `language`
    defaults to Spanish because the historic assertions in this suite
    all target the Spanish fire prompt and prompt_context.
    """
    skill = TimersSkill(sleep=None if real_sleep else _instant_sleep)
    inject_mock = inject_turn or AsyncMock()
    ctx = make_test_context(
        config=dict(config) if config else None,
        storage=storage,
        language=language,
    )
    # `make_test_context` populates a no-op inject_turn; override it with
    # the recording mock so tests can assert on what the timer fired.
    object.__setattr__(ctx, "inject_turn", inject_mock)
    await skill.setup(ctx)
    return skill, inject_mock


class TestSetTimer:
    async def test_schedules_and_fires_inject_turn(self) -> None:
        """One end-to-end test with `real_sleep=True` keeps wall-clock
        coverage of the happy path — every other test uses the fast
        stub to keep the suite under a second."""
        skill, inject_mock = await _setup_skill(real_sleep=True)

        # Use a very short sleep so the test runs fast.
        result = await skill.handle(
            "set_timer", {"seconds": 1, "message": "sacar la ropa de la lavadora"}
        )

        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["timer_id"] == 1
        assert payload["seconds"] == 1

        # Wait long enough for the timer to fire — real sleep test so a
        # plain event-loop yield isn't enough.
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


class TestFirePromptPersonaConfig:
    """Persona config override for the fire-prompt template (PQ-2).

    The default is Spanish / AbuelOS-toned. Any persona can override
    via `persona.yaml`'s `timers.fire_prompt`; the skill interpolates
    `{message}` at fire time.
    """

    async def test_default_template_used_when_config_absent(self) -> None:
        skill, inject_mock = await _setup_skill()
        await skill.handle("set_timer", {"seconds": 1, "message": "agua"})
        await _drain()
        prompt = inject_mock.await_args.args[0]
        # Default is the Spanish/AbuelOS-toned template.
        assert "temporizador" in prompt.lower()
        assert "agua" in prompt

    async def test_persona_override_replaces_default(self) -> None:
        skill, inject_mock = await _setup_skill(
            config={
                "fire_prompt": "Timer. Tell user: {message}. Terse.",
            }
        )
        await skill.handle("set_timer", {"seconds": 1, "message": "drink water"})
        await _drain()
        prompt = inject_mock.await_args.args[0]
        assert prompt == "Timer. Tell user: drink water. Terse."

    async def test_missing_placeholder_falls_back_to_default(self) -> None:
        """If persona config sets `fire_prompt` without `{message}`,
        skill logs a warning and keeps the default — better than
        silently dropping the message."""
        skill, inject_mock = await _setup_skill(
            config={"fire_prompt": "broken template with no placeholder"}
        )
        await skill.handle("set_timer", {"seconds": 1, "message": "pills"})
        await _drain()
        prompt = inject_mock.await_args.args[0]
        # Default template used; user's message is still surfaced.
        assert "pills" in prompt
        assert "temporizador" in prompt.lower()

    async def test_empty_string_override_ignored(self) -> None:
        skill, inject_mock = await _setup_skill(config={"fire_prompt": ""})
        await skill.handle("set_timer", {"seconds": 1, "message": "x"})
        await _drain()
        prompt = inject_mock.await_args.args[0]
        # Empty string shouldn't silently disable the prompt.
        assert "temporizador" in prompt.lower()


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

        await _drain()
        # The fire path's `finally` clears the entry.
        assert skill._handles == {}


class TestPersistence:
    """Stage 3b — timers survive a server restart.

    Tests exercise the write-on-schedule / delete-on-fire invariant
    plus the `setup()` restore path for the four restore outcomes:
    reschedule, fire-immediately, stale-drop, fired-dedup-drop.
    """

    async def test_set_timer_writes_entry_to_storage(self) -> None:
        storage = _NoopSkillStorage()
        skill, _ = await _setup_skill(storage=storage)
        await skill.handle("set_timer", {"seconds": 300, "message": "pastilla"})

        entries = await storage.list_settings("timer:")
        assert len(entries) == 1
        key, value = entries[0]
        assert key == "timer:1"
        payload = json.loads(value)
        assert payload["v"] == 1
        assert payload["message"] == "pastilla"
        assert payload["fired_at"] is None
        # fire_at is ~now + 300s; just check it parses and is in the future.
        fire_at = datetime.fromisoformat(payload["fire_at"])
        assert fire_at > datetime.now(UTC)
        await skill.teardown()

    async def test_fire_deletes_entry_from_storage(self) -> None:
        storage = _NoopSkillStorage()
        skill, _ = await _setup_skill(storage=storage)
        await skill.handle("set_timer", {"seconds": 1, "message": "agua"})
        assert len(await storage.list_settings("timer:")) == 1

        await _drain()
        # After natural fire, the entry is gone so it won't replay on restart.
        assert await storage.list_settings("timer:") == []

    async def test_teardown_preserves_entries_for_restore(self) -> None:
        storage = _NoopSkillStorage()
        skill, _ = await _setup_skill(storage=storage)
        await skill.handle("set_timer", {"seconds": 3600, "message": "nunca"})
        await skill.teardown()
        # Teardown cancels the in-memory task but MUST keep the persisted
        # entry — that's what makes restore-across-restart work.
        entries = await storage.list_settings("timer:")
        assert len(entries) == 1

    async def test_restore_reschedules_pending_timer(self) -> None:
        """A fresh skill sees a pending entry, reschedules it, fires it."""
        storage = _NoopSkillStorage()
        inject_mock = AsyncMock()
        fire_at = datetime.now(UTC) + timedelta(seconds=1)
        await storage.set_setting(
            "timer:42",
            json.dumps(
                {
                    "v": 1,
                    "fire_at": fire_at.isoformat(),
                    "message": "leche",
                    "fired_at": None,
                }
            ),
        )
        skill, _ = await _setup_skill(inject_turn=inject_mock, storage=storage)
        assert 42 in skill._handles

        await _drain()
        inject_mock.assert_awaited_once()
        assert "leche" in inject_mock.await_args.args[0]
        # Entry deleted after natural fire.
        assert await storage.list_settings("timer:") == []

    async def test_restore_fires_immediately_when_slightly_stale(self) -> None:
        """`fire_at` already past but within the stale threshold → fire
        on next tick, don't drop. Clamped to _MIN_SECONDS (1s) so the
        user at least hears a reminder."""
        storage = _NoopSkillStorage()
        inject_mock = AsyncMock()
        fire_at = datetime.now(UTC) - timedelta(seconds=30)
        await storage.set_setting(
            "timer:7",
            json.dumps(
                {
                    "v": 1,
                    "fire_at": fire_at.isoformat(),
                    "message": "pill",
                    "fired_at": None,
                }
            ),
        )
        skill, _ = await _setup_skill(inject_turn=inject_mock, storage=storage)

        await _drain()
        inject_mock.assert_awaited_once()

    async def test_restore_drops_entry_older_than_stale_threshold(self) -> None:
        storage = _NoopSkillStorage()
        inject_mock = AsyncMock()
        fire_at = datetime.now(UTC) - timedelta(hours=2)
        await storage.set_setting(
            "timer:99",
            json.dumps(
                {
                    "v": 1,
                    "fire_at": fire_at.isoformat(),
                    "message": "ancient",
                    "fired_at": None,
                }
            ),
        )
        skill, _ = await _setup_skill(inject_turn=inject_mock, storage=storage)
        # Too old — dropped without scheduling a task and without firing.
        assert 99 not in skill._handles
        await _drain()
        inject_mock.assert_not_awaited()
        assert await storage.list_settings("timer:") == []

    async def test_restore_drops_entry_with_fired_at_set(self) -> None:
        """Critical: a crash between `inject_turn` and the storage delete
        leaves `fired_at` set. Restore MUST skip (no second dose)."""
        storage = _NoopSkillStorage()
        inject_mock = AsyncMock()
        now = datetime.now(UTC)
        await storage.set_setting(
            "timer:50",
            json.dumps(
                {
                    "v": 1,
                    "fire_at": now.isoformat(),
                    "message": "dose",
                    "fired_at": now.isoformat(),
                }
            ),
        )
        skill, _ = await _setup_skill(inject_turn=inject_mock, storage=storage)
        assert 50 not in skill._handles
        await _drain()
        inject_mock.assert_not_awaited()
        assert await storage.list_settings("timer:") == []

    async def test_restore_primes_next_id_past_existing(self) -> None:
        """After restore, `set_timer` must not overwrite a restored entry."""
        storage = _NoopSkillStorage()
        fire_at = datetime.now(UTC) + timedelta(seconds=3600)
        await storage.set_setting(
            "timer:5",
            json.dumps(
                {
                    "v": 1,
                    "fire_at": fire_at.isoformat(),
                    "message": "existing",
                    "fired_at": None,
                }
            ),
        )
        skill, _ = await _setup_skill(storage=storage)
        result = await skill.handle("set_timer", {"seconds": 60, "message": "new"})
        assert json.loads(result.output)["timer_id"] == 6
        await skill.teardown()

    async def test_restore_skips_malformed_entries(self) -> None:
        """Garbage in storage must not crash setup()."""
        storage = _NoopSkillStorage()
        await storage.set_setting("timer:bogus", "not json")
        await storage.set_setting("timer:1", "{}")  # valid JSON, missing fields
        await storage.set_setting("timer:abc", '{"v":1}')  # non-numeric id suffix
        # Should complete without raising.
        skill, _ = await _setup_skill(storage=storage)
        assert skill._handles == {}
        await skill.teardown()

    async def test_restore_empty_storage_is_noop(self) -> None:
        storage = _NoopSkillStorage()
        skill, _ = await _setup_skill(storage=storage)
        assert skill._handles == {}
        # Fresh skill should start assigning ids from 1.
        result = await skill.handle("set_timer", {"seconds": 60, "message": "first"})
        assert json.loads(result.output)["timer_id"] == 1
        await skill.teardown()


class TestStaleThresholdConfig:
    """Persona config override for the stale-restore threshold.

    Default is 1h. Personas that extend the skill's effective max
    duration (e.g., a scheduling skill that reuses this code path)
    must extend the threshold in lockstep or restored entries longer
    than 1h drop.
    """

    async def test_custom_threshold_keeps_entry_within_window(self) -> None:
        storage = _NoopSkillStorage()
        inject_mock = AsyncMock()
        # 2h ago — would drop under the 1h default, but threshold is 4h.
        fire_at = datetime.now(UTC) - timedelta(hours=2)
        await storage.set_setting(
            "timer:1",
            json.dumps(
                {
                    "v": 1,
                    "fire_at": fire_at.isoformat(),
                    "message": "late",
                    "fired_at": None,
                }
            ),
        )
        skill, _ = await _setup_skill(
            inject_turn=inject_mock,
            storage=storage,
            config={"stale_restore_threshold_s": 4 * 3600},
        )
        # Not dropped — rescheduled with _MIN_SECONDS remaining.
        assert 1 in skill._handles
        await _drain()
        inject_mock.assert_awaited_once()

    async def test_invalid_threshold_falls_back_to_default(self) -> None:
        storage = _NoopSkillStorage()
        skill, _ = await _setup_skill(
            storage=storage,
            config={"stale_restore_threshold_s": "not a number"},
        )
        # Default kept (1h); no crash on bad config.
        assert skill._stale_threshold == timedelta(hours=1)
        await skill.teardown()

    async def test_zero_or_negative_threshold_falls_back_to_default(self) -> None:
        storage = _NoopSkillStorage()
        skill, _ = await _setup_skill(
            storage=storage,
            config={"stale_restore_threshold_s": -5},
        )
        assert skill._stale_threshold == timedelta(hours=1)
        await skill.teardown()

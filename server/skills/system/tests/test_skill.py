"""Tests for SystemSkill — time query and volume control."""

from __future__ import annotations

import json

import pytest

from huxley_sdk import SetVolume
from huxley_sdk.testing import make_test_context
from huxley_skill_system.skill import SystemSkill


@pytest.fixture
async def skill() -> SystemSkill:
    s = SystemSkill()
    ctx = make_test_context(config={"timezone": "America/Bogota"})
    await s.setup(ctx)
    return s


class TestGetCurrentTime:
    async def test_returns_required_keys(self, skill: SystemSkill) -> None:
        result = await skill.handle("get_current_time", {})
        data = json.loads(result.output)
        assert "time" in data
        assert "date" in data
        assert "timezone" in data

    async def test_timezone_from_config(self, skill: SystemSkill) -> None:
        result = await skill.handle("get_current_time", {})
        data = json.loads(result.output)
        assert data["timezone"] == "America/Bogota"

    async def test_default_timezone_without_config(self) -> None:
        s = SystemSkill()
        await s.setup(make_test_context(config={}))
        result = await s.handle("get_current_time", {})
        data = json.loads(result.output)
        assert data["timezone"] == "America/Bogota"

    async def test_custom_timezone(self) -> None:
        s = SystemSkill()
        await s.setup(make_test_context(config={"timezone": "Europe/Madrid"}))
        result = await s.handle("get_current_time", {})
        data = json.loads(result.output)
        assert data["timezone"] == "Europe/Madrid"

    async def test_no_side_effect(self, skill: SystemSkill) -> None:
        result = await skill.handle("get_current_time", {})
        assert result.side_effect is None


class TestSetVolume:
    async def test_clamps_above_100(self, skill: SystemSkill) -> None:
        result = await skill.handle("set_volume", {"level": 150})
        data = json.loads(result.output)
        assert data["volume"] == 100
        assert data["ok"] is True
        assert isinstance(result.side_effect, SetVolume)
        assert result.side_effect.level == 100

    async def test_clamps_below_0(self, skill: SystemSkill) -> None:
        result = await skill.handle("set_volume", {"level": -10})
        data = json.loads(result.output)
        assert data["volume"] == 0
        assert isinstance(result.side_effect, SetVolume)
        assert result.side_effect.level == 0

    async def test_valid_level_passes_through(self, skill: SystemSkill) -> None:
        result = await skill.handle("set_volume", {"level": 42})
        data = json.loads(result.output)
        assert data["volume"] == 42
        assert isinstance(result.side_effect, SetVolume)
        assert result.side_effect.level == 42

    async def test_returns_set_volume_side_effect(self, skill: SystemSkill) -> None:
        result = await skill.handle("set_volume", {"level": 75})
        assert isinstance(result.side_effect, SetVolume)
        assert result.side_effect.level == 75


class TestUnknownTool:
    async def test_unknown_tool_returns_error_json(self, skill: SystemSkill) -> None:
        result = await skill.handle("nonexistent_tool", {})
        data = json.loads(result.output)
        assert "error" in data

    async def test_no_side_effect_on_unknown(self, skill: SystemSkill) -> None:
        result = await skill.handle("nonexistent_tool", {})
        assert result.side_effect is None


class TestSetupGuard:
    async def test_handle_before_setup_raises(self) -> None:
        s = SystemSkill()
        with pytest.raises(RuntimeError, match="setup"):
            await s.handle("get_current_time", {})


class TestBattery:
    """The device's level (F-0025): kept from `client_event system.battery`,
    answered by `get_battery`, and spoken once at 20 % and once at 10 %."""

    async def _skill(self) -> tuple[SystemSkill, list[str], list[tuple[str, object]]]:
        import dataclasses

        injected: list[str] = []
        subs: list[tuple[str, object]] = []

        async def inject(prompt: str, /, **_: object) -> None:
            injected.append(prompt)

        def subscribe(key: str, handler: object, /) -> None:
            subs.append((key, handler))

        ctx = dataclasses.replace(
            make_test_context(config={}, language="es"),
            inject_turn=inject,
            subscribe_client_event=subscribe,
        )
        s = SystemSkill()
        await s.setup(ctx)
        return s, injected, subs

    async def _report(self, s: SystemSkill, pct: int, mv: int = 3800) -> None:
        import asyncio

        await s._on_battery({"pct": pct, "mv": mv})
        await asyncio.sleep(0)  # the warning runs as its own task

    async def test_subscribes_to_the_device_event(self) -> None:
        _, _, subs = await self._skill()
        assert [k for k, _ in subs] == ["system.battery"]

    async def test_no_reading_yet(self) -> None:
        s, _, _ = await self._skill()
        data = json.loads((await s.handle("get_battery", {})).output)
        assert data["known"] is False

    async def test_reports_the_last_reading(self) -> None:
        s, _, _ = await self._skill()
        await self._report(s, 63)
        data = json.loads((await s.handle("get_battery", {})).output)
        assert data == {"known": True, "percent": 63}

    async def test_warns_once_at_20_then_once_at_10(self) -> None:
        s, injected, _ = await self._skill()
        for pct in (25, 21, 20, 19, 18, 12, 10, 9, 5):
            await self._report(s, pct)
        assert len(injected) == 2
        assert "20 por ciento" in injected[0] and "Díselo al usuario" in injected[0]
        assert "10 por ciento" in injected[1]

    async def test_a_jump_past_both_warns_once_at_the_lower(self) -> None:
        s, injected, _ = await self._skill()
        await self._report(s, 40)
        await self._report(s, 8)
        assert len(injected) == 1 and "8 por ciento" in injected[0]

    async def test_charging_rearms_the_warnings(self) -> None:
        s, injected, _ = await self._skill()
        await self._report(s, 19)
        await self._report(s, 35)  # charged
        await self._report(s, 20)
        assert len(injected) == 2

    async def test_a_bad_reading_is_ignored(self) -> None:
        s, injected, _ = await self._skill()
        await s._on_battery({"pct": "lots"})
        await s._on_battery({"pct": 150, "mv": 3800})
        data = json.loads((await s.handle("get_battery", {})).output)
        assert data["known"] is False and injected == []

    async def test_stale_reading_says_how_old(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import huxley_skill_system.skill as mod

        s, _, _ = await self._skill()
        await self._report(s, 50)
        real = mod.time.monotonic
        monkeypatch.setattr(mod.time, "monotonic", lambda: real() + 20 * 60)
        data = json.loads((await s.handle("get_battery", {})).output)
        assert data["percent"] == 50 and data["minutes_since_reading"] == 20


class TestVolumeSteps:
    """ "Más volumen" twice set 80 twice (2026-10-17): the step is the
    skill's, from the last level set, kept across a restart."""

    async def test_up_without_a_known_level_is_80_then_15_more(self) -> None:
        s = SystemSkill()
        await s.setup(make_test_context(config={}))
        first = json.loads((await s.handle("set_volume", {"change": "up"})).output)
        second = json.loads((await s.handle("set_volume", {"change": "up"})).output)
        assert (first["volume"], second["volume"], second["was"]) == (80, 95, 80)
        third = json.loads((await s.handle("set_volume", {"change": "up"})).output)
        assert third["volume"] == 100

    async def test_down_steps_and_stops_at_20(self) -> None:
        s = SystemSkill()
        await s.setup(make_test_context(config={}))
        await s.handle("set_volume", {"level": 30})
        down = await s.handle("set_volume", {"change": "down"})
        assert json.loads(down.output)["volume"] == 20
        assert isinstance(down.side_effect, SetVolume) and down.side_effect.level == 20

    async def test_a_level_said_wins_over_a_change(self) -> None:
        s = SystemSkill()
        await s.setup(make_test_context(config={}))
        out = json.loads((await s.handle("set_volume", {"level": 60, "change": "up"})).output)
        assert out["volume"] == 60

    async def test_the_last_level_survives_a_restart(self) -> None:
        ctx = make_test_context(config={})
        s = SystemSkill()
        await s.setup(ctx)
        await s.handle("set_volume", {"level": 50})
        again = SystemSkill()
        await again.setup(ctx)
        out = json.loads((await again.handle("set_volume", {"change": "up"})).output)
        assert (out["was"], out["volume"]) == (50, 65)

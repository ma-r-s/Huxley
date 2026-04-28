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

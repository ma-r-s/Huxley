"""A skill whose setup() raises must not take the persona down.

2026-10-12: the Telegram skill's setup raised (its session DB was still
locked by a server process that was shutting down), `setup_all`
propagated it, and the whole persona failed to load: the walkie had no
assistant for 55 s. Now the registry disables the failing skill, tears
it down, and the persona starts with the rest. The disabled skill's
tools stay offered and answer with an error the model speaks, so the
failure has an audio surface (the Abuelo user is blind).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
import structlog

from huxley.config import Settings
from huxley.focus.manager import FocusManager
from huxley.runtime import Runtime
from huxley.turn.coordinator import TurnCoordinator
from huxley.voice.stub import StubVoiceProvider
from huxley_sdk import SkillContext, SkillRegistry, ToolDefinition, ToolResult
from huxley_sdk.testing import FakeSkill, make_test_context

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


_PERSONA_YAML = """\
version: 1
name: p
voice: coral
language_code: es
transcription_language: es
timezone: America/Bogota
system_prompt: |
  Test persona with one broken skill.
constraints: []
skills:
  good: {}
  broken: {}
"""


class _GoodSkill:
    name = "good"
    data_schema_version = 1

    @property
    def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(name="good_tool", description="Works")]

    async def setup(self, ctx: SkillContext) -> None:
        return None

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult(output='{"ok": true}')


class _BrokenSkill:
    """Fails setup the way Telegram did, after subscribing to a client
    event and starting a supervised task (so the tests can show a
    disabled skill's subscriptions and tasks are dropped)."""

    name = "broken"
    data_schema_version = 1
    teardown_calls = 0

    @property
    def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(name="call_contact", description="Call someone")]

    async def setup(self, ctx: SkillContext) -> None:
        ctx.subscribe_client_event("broken.button", self._on_button)
        ctx.background_task("broken.loop", self._loop)
        msg = "database is locked"
        raise RuntimeError(msg)

    async def _loop(self) -> None:
        await asyncio.Event().wait()

    async def _on_button(self, data: dict[str, Any]) -> None:
        return None

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        raise AssertionError("a disabled skill must never be dispatched to")

    async def teardown(self) -> None:
        type(self).teardown_calls += 1


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Runtime:
    persona_dir = tmp_path / "personas" / "p"
    persona_dir.mkdir(parents=True)
    (persona_dir / "persona.yaml").write_text(_PERSONA_YAML, encoding="utf-8")
    (persona_dir / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "huxley.app.discover_skills",
        lambda _enabled: {"good": _GoodSkill, "broken": _BrokenSkill},
    )
    _BrokenSkill.teardown_calls = 0
    return Runtime(Settings(openai_api_key="test-key"))


class TestPersonaStartsWithoutTheBrokenSkill:
    async def test_start_succeeds_and_disables_only_the_failing_skill(
        self, runtime: Runtime
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            await runtime._switch_to_persona("p", auto_connect=False)

        app = runtime.current_app
        assert app is not None
        assert app.skill_registry.skill_names == ["good"]
        assert app.skill_registry.disabled_skill_names == ["broken"]

        failed = [e for e in logs if e["event"] == "skill.setup_failed"]
        assert len(failed) == 1
        assert failed[0]["skill"] == "broken"
        assert failed[0]["exception_class"] == "RuntimeError"
        assert failed[0]["error"] == "database is locked"
        ready = [e for e in logs if e["event"] == "huxley_app_ready"]
        assert ready[0]["skills"] == ["good"]
        assert ready[0]["disabled_skills"] == ["broken"]
        # Tools stay offered so the persona prompt and the tool list agree.
        assert "call_contact" in ready[0]["tools"]
        assert not [e for e in logs if e["event"] == "runtime.persona_start_failed"]

        await app.shutdown()

    async def test_disabled_skill_is_torn_down_once_and_loses_its_subscriptions(
        self, runtime: Runtime
    ) -> None:
        await runtime._switch_to_persona("p", auto_connect=False)
        app = runtime.current_app
        assert app is not None

        assert _BrokenSkill.teardown_calls == 1
        assert "broken.button" not in runtime.audio_server._client_event_subs

        await app.shutdown()
        assert _BrokenSkill.teardown_calls == 1  # not torn down twice

    async def test_tasks_started_by_the_failed_setup_are_cancelled(self, runtime: Runtime) -> None:
        """A disabled skill must not keep a loop running behind the user
        (e.g. a client that keeps reconnecting, or a scheduler that
        speaks)."""
        with structlog.testing.capture_logs() as logs:
            await runtime._switch_to_persona("p", auto_connect=False)
        app = runtime.current_app
        assert app is not None
        await asyncio.sleep(0)

        task = app.task_supervisor._tasks["broken.loop"]
        assert task.cancelled()
        failed = next(e for e in logs if e["event"] == "skill.setup_failed")
        assert failed["cancelled_tasks"] == ["broken.loop"]
        # Tasks started after setup are not tracked (nothing to cancel).
        assert app._setup_task_handles is None

        await app.shutdown()

    async def test_disabled_skill_schema_version_is_not_persisted(self, runtime: Runtime) -> None:
        """Same torn-setup rule as before isolation: the next boot must
        re-check the skill that failed."""
        await runtime._switch_to_persona("p", auto_connect=False)
        app = runtime.current_app
        assert app is not None

        assert await app.storage.get_skill_schema_version("good") == 1
        assert await app.storage.get_skill_schema_version("broken") is None

        await app.shutdown()


class _BrokenFake(FakeSkill):
    async def setup(self, ctx: SkillContext) -> None:
        msg = "database is locked"
        raise RuntimeError(msg)


@pytest.fixture
async def focus_manager() -> AsyncIterator[FocusManager]:
    fm = FocusManager.with_default_channels()
    fm.start()
    yield fm
    await fm.stop()


class TestDisabledSkillToolCallIsSpoken:
    """The audio surface: calling a disabled skill's tool goes through
    the coordinator's tool-error path, which sends the model an error to
    apologize for and asks it for a follow-up response."""

    async def test_tool_call_gets_error_output_and_follow_up(
        self, focus_manager: FocusManager
    ) -> None:
        registry = SkillRegistry()
        registry.register(
            _BrokenFake(
                name="broken",
                tools=[ToolDefinition(name="call_contact", description="Call someone")],
            )
        )

        async def _ignore(_name: str, _exc: BaseException) -> None:
            return None

        await registry.setup_all(
            lambda name: make_test_context(name=name), on_skill_disabled=_ignore
        )

        provider = StubVoiceProvider()
        provider._connected = True
        send_dev_event = AsyncMock()
        coordinator = TurnCoordinator(
            send_audio=AsyncMock(),
            send_audio_clear=AsyncMock(),
            send_status=AsyncMock(),
            send_model_speaking=AsyncMock(),
            send_dev_event=send_dev_event,
            provider=provider,
            dispatch_tool=registry.dispatch,
            focus_manager=focus_manager,
        )

        await coordinator.on_ptt_start()
        assert coordinator.current_turn is not None
        coordinator.current_turn.user_audio_bytes = 300 * 48
        await coordinator.on_ptt_stop()
        await coordinator.on_tool_call("call_1", "call_contact", {"name": "hija"})

        outputs = [s for s in provider.sent if s[0] == "send_tool_output"]
        assert len(outputs) == 1
        assert '"error": "tool_failed"' in outputs[0][2]
        assert '"tool": "call_contact"' in outputs[0][2]
        kinds = [call.args[0] for call in send_dev_event.await_args_list]
        assert "tool_error" in kinds

        # The model is asked to speak (the apology) on the next round.
        await coordinator.on_response_done()
        assert ("request_response",) in provider.sent

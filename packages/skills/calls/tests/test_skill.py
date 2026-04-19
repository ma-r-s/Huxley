"""Tests for `huxley-skill-calls`.

Skill is tested in isolation: no real WebSocket, no real coordinator.
A fake `ServerConnection` is fed via `on_caller_connected`; assertions
hit `inject_turn` / tool returns / cleanup state. The end-to-end
integration (claim through coordinator + AudioServer routes) is
covered separately via the route tests in
`packages/core/tests/unit/test_audio_server_calls.py` and the
coordinator's claim tests in
`packages/core/tests/unit/test_coordinator_input_claim.py`.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

from huxley_sdk import (
    ClaimEndReason,
    InjectPriority,
    InputClaim,
)
from huxley_sdk.testing import make_test_context
from huxley_skill_calls.skill import CallsSkill

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest


class FakeWS:
    """Minimal stand-in for `websockets.asyncio.server.ServerConnection`.

    The skill uses two methods on the WS: `__aiter__` to read incoming
    PCM frames and `send` to forward grandpa's mic frames. `close` is
    called from cleanup.
    """

    def __init__(self, incoming: list[bytes] | None = None) -> None:
        # Frames the test wants the caller to "send" to grandpa.
        self.incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
        for f in incoming or []:
            self.incoming.put_nowait(f)
        # Frames the skill `send`s to the caller (grandpa's mic).
        self.sent: list[bytes] = []
        self.closed = False
        self.close_code: int | None = None

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[bytes]:
        while True:
            frame = await self.incoming.get()
            if frame is None:
                return
            yield frame

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        _ = reason
        self.closed = True
        self.close_code = code
        # Unblock any pending iter() so the read loop exits cleanly.
        await self.incoming.put(None)


async def _setup_skill(
    config: dict[str, Any] | None = None,
    inject_turn: AsyncMock | None = None,
) -> tuple[CallsSkill, AsyncMock]:
    """Build a CallsSkill with a recording inject_turn mock."""
    skill = CallsSkill()
    inject_mock = inject_turn or AsyncMock()
    ctx = make_test_context(config=dict(config) if config else None)
    object.__setattr__(ctx, "inject_turn", inject_mock)
    await skill.setup(ctx)
    return skill, inject_mock


# --- Setup + secret loading ---


class TestSetup:
    async def test_secret_from_persona_config(self) -> None:
        skill, _ = await _setup_skill(config={"secret": "from-yaml"})
        assert skill.secret == "from-yaml"

    async def test_env_var_wins_over_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HUXLEY_CALLS_SECRET", "from-env")
        skill, _ = await _setup_skill(config={"secret": "from-yaml"})
        assert skill.secret == "from-env"

    async def test_no_secret_warns_but_doesnt_fail(self) -> None:
        # No env var, no config → secret is None, skill still loads.
        skill, _ = await _setup_skill()
        assert skill.secret is None


# --- on_ring ---


class TestOnRing:
    async def test_accepted_when_idle(self) -> None:
        skill, inject = await _setup_skill(config={"secret": "x"})
        accepted = await skill.on_ring({"from": "Mario"})
        assert accepted is True
        # Inject fired with PREEMPT priority + the from_name in the prompt.
        inject.assert_awaited_once()
        prompt = inject.await_args.args[0]
        assert "Mario" in prompt
        assert inject.await_args.kwargs["priority"] is InjectPriority.PREEMPT

    async def test_rejected_when_already_pending(self) -> None:
        skill, inject = await _setup_skill(config={"secret": "x"})
        await skill.on_ring({"from": "Mario"})
        inject.reset_mock()
        # Second ring before answer — busy.
        accepted = await skill.on_ring({"from": "Otra Persona"})
        assert accepted is False
        inject.assert_not_awaited()

    async def test_rejected_when_caller_already_connected(self) -> None:
        skill, _ = await _setup_skill(config={"secret": "x"})
        ws = FakeWS()
        # Simulate the caller WS landing first (skill sees it as busy).
        skill._caller_ws = ws  # type: ignore[assignment]
        accepted = await skill.on_ring({"from": "M"})
        assert accepted is False

    async def test_default_from_name_when_missing(self) -> None:
        skill, inject = await _setup_skill()
        await skill.on_ring({})
        prompt = inject.await_args.args[0]
        assert "alguien" in prompt


# --- on_caller_connected + read loop ---


class TestCallerConnection:
    async def test_drops_frames_before_claim_starts(self) -> None:
        skill, _ = await _setup_skill()
        ws = FakeWS(incoming=[b"early", b"frames"])
        # Run the connection handler in a task so we can let it process
        # then close.
        task = asyncio.create_task(skill.on_caller_connected(ws))  # type: ignore[arg-type]
        # Give the loop a few ticks to process the queued frames.
        for _ in range(5):
            await asyncio.sleep(0)
        await ws.close()
        await task
        # No claim active → frames dropped, skill state cleaned.
        assert skill._caller_ws is None

    async def test_second_caller_rejected(self) -> None:
        skill, _ = await _setup_skill()
        ws1 = FakeWS()
        ws2 = FakeWS()
        skill._caller_ws = ws1  # type: ignore[assignment]
        await skill.on_caller_connected(ws2)  # type: ignore[arg-type]
        assert ws2.closed is True
        assert ws2.close_code == 1008

    async def test_caller_close_drives_cancel_active_claim(self) -> None:
        """Stage 2.1: when the caller WS closes during an active call,
        the skill invokes `ctx.cancel_active_claim(NATURAL)` so the
        observer's on_claim_end fires and "Mario colgó" narrates. Before
        2.1 this was a TODO / workaround; now verified end-to-end."""
        skill, _ = await _setup_skill()
        ctx = skill._ctx
        assert ctx is not None
        # Replace the default no-op cancel_active_claim with a recorder.
        cancel_mock = AsyncMock(return_value=True)
        object.__setattr__(ctx, "cancel_active_claim", cancel_mock)

        ws = FakeWS()
        # Simulate "answer_call already ran": claim active, queue set.
        skill._claim_active = True
        skill._caller_pcm_queue = asyncio.Queue()
        task = asyncio.create_task(skill.on_caller_connected(ws))  # type: ignore[arg-type]
        for _ in range(5):
            await asyncio.sleep(0)
        # Caller disconnects.
        await ws.close()
        await task

        cancel_mock.assert_awaited_once_with(reason=ClaimEndReason.NATURAL)

    async def test_caller_close_without_active_claim_no_cancel(self) -> None:
        """If the caller WS closes before `answer_call` has fired (e.g.
        caller gave up during the countdown), there's no claim to cancel
        — skill must not call `cancel_active_claim`."""
        skill, _ = await _setup_skill()
        ctx = skill._ctx
        assert ctx is not None
        cancel_mock = AsyncMock(return_value=False)
        object.__setattr__(ctx, "cancel_active_claim", cancel_mock)

        ws = FakeWS()
        assert skill._claim_active is False
        task = asyncio.create_task(skill.on_caller_connected(ws))  # type: ignore[arg-type]
        for _ in range(5):
            await asyncio.sleep(0)
        await ws.close()
        await task

        cancel_mock.assert_not_awaited()


# --- answer_call ---


class TestAnswerCall:
    async def test_returns_input_claim_with_relay(self) -> None:
        skill, _ = await _setup_skill()
        ws = FakeWS()
        skill._caller_ws = ws  # type: ignore[assignment]
        skill._pending_from = "Mario"

        result = await skill.handle("answer_call", {})
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["from"] == "Mario"
        assert isinstance(result.side_effect, InputClaim)
        assert result.side_effect.speaker_source is not None
        assert skill._claim_active is True
        assert skill._caller_pcm_queue is not None

    async def test_no_caller_returns_error(self) -> None:
        skill, _ = await _setup_skill()
        result = await skill.handle("answer_call", {})
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "no caller" in payload["error"]

    async def test_already_active_returns_error(self) -> None:
        skill, _ = await _setup_skill()
        skill._caller_ws = FakeWS()  # type: ignore[assignment]
        skill._claim_active = True
        result = await skill.handle("answer_call", {})
        payload = json.loads(result.output)
        assert payload["ok"] is False

    async def test_mic_frame_forwards_to_caller(self) -> None:
        skill, _ = await _setup_skill()
        ws = FakeWS()
        skill._caller_ws = ws  # type: ignore[assignment]
        skill._pending_from = "M"

        result = await skill.handle("answer_call", {})
        assert isinstance(result.side_effect, InputClaim)
        # Simulate the framework calling on_mic_frame with grandpa's PCM.
        await result.side_effect.on_mic_frame(b"\xaa\xbb\xcc")
        assert ws.sent == [b"\xaa\xbb\xcc"]

    async def test_speaker_source_yields_caller_frames(self) -> None:
        skill, _ = await _setup_skill()
        ws = FakeWS()
        skill._caller_ws = ws  # type: ignore[assignment]
        skill._pending_from = "M"

        result = await skill.handle("answer_call", {})
        assert isinstance(result.side_effect, InputClaim)
        # Push frames into the queue (simulates caller WS read loop).
        assert skill._caller_pcm_queue is not None
        skill._caller_pcm_queue.put_nowait(b"\x01\x02")
        skill._caller_pcm_queue.put_nowait(b"\x03\x04")
        # Pull two from speaker_source, then cancel iteration.
        speaker = result.side_effect.speaker_source
        assert speaker is not None
        first = await asyncio.wait_for(speaker.__anext__(), timeout=0.5)
        second = await asyncio.wait_for(speaker.__anext__(), timeout=0.5)
        assert first == b"\x01\x02"
        assert second == b"\x03\x04"


# --- on_claim_end narration ---


class TestEndNarration:
    async def test_natural_end_says_caller_hung_up(self) -> None:
        skill, inject = await _setup_skill()
        skill._pending_from = "Mario"
        await skill._on_call_ended(ClaimEndReason.NATURAL)
        inject.assert_awaited_once()
        prompt = inject.await_args.args[0]
        # Default "Mario colgó" template uses {from_name}.
        assert "Mario" in prompt

    async def test_user_ptt_end_says_finished(self) -> None:
        skill, inject = await _setup_skill()
        await skill._on_call_ended(ClaimEndReason.USER_PTT)
        inject.assert_awaited_once()
        prompt = inject.await_args.args[0]
        assert "finalizada" in prompt.lower() or "finaliz" in prompt.lower()

    async def test_error_end_says_dropped(self) -> None:
        skill, inject = await _setup_skill()
        await skill._on_call_ended(ClaimEndReason.ERROR)
        inject.assert_awaited_once()
        prompt = inject.await_args.args[0]
        assert "cort" in prompt.lower() or "problema" in prompt.lower()

    async def test_preempted_does_not_narrate(self) -> None:
        """PREEMPT means another inject is already taking over; we
        must not double-narrate or queue a second inject."""
        skill, inject = await _setup_skill()
        await skill._on_call_ended(ClaimEndReason.PREEMPTED)
        inject.assert_not_awaited()

    async def test_end_clears_state(self) -> None:
        skill, _ = await _setup_skill()
        skill._caller_ws = FakeWS()  # type: ignore[assignment]
        skill._pending_from = "Mario"
        skill._claim_active = True
        skill._caller_pcm_queue = asyncio.Queue()
        await skill._on_call_ended(ClaimEndReason.NATURAL)
        assert skill._caller_ws is None
        assert skill._pending_from is None
        assert skill._claim_active is False
        assert skill._caller_pcm_queue is None


# --- Persona prompt overrides ---


class TestPromptOverrides:
    async def test_ring_prompt_override(self) -> None:
        skill, inject = await _setup_skill(
            config={
                "ring_prompt": "Phone! From {from_name}. Pick up via answer_call.",
            }
        )
        await skill.on_ring({"from": "Bob"})
        prompt = inject.await_args.args[0]
        assert prompt == "Phone! From Bob. Pick up via answer_call."

    async def test_invalid_override_falls_back_to_default(self) -> None:
        skill, inject = await _setup_skill(config={"ring_prompt": 42})
        await skill.on_ring({"from": "Mario"})
        prompt = inject.await_args.args[0]
        # Default Spanish template kicks in.
        assert "Llamada" in prompt or "llamada" in prompt or "teléfono" in prompt


# --- reject_call + end_call ---


class TestRejectAndEnd:
    async def test_reject_clears_pending(self) -> None:
        skill, _ = await _setup_skill()
        skill._pending_from = "Mario"
        result = await skill.handle("reject_call", {})
        assert json.loads(result.output)["ok"] is True
        assert skill._pending_from is None

    async def test_reject_no_pending_errors(self) -> None:
        skill, _ = await _setup_skill()
        result = await skill.handle("reject_call", {})
        payload = json.loads(result.output)
        assert payload["ok"] is False

    async def test_end_call_no_active_errors(self) -> None:
        skill, _ = await _setup_skill()
        result = await skill.handle("end_call", {})
        payload = json.loads(result.output)
        assert payload["ok"] is False

    async def test_end_call_active_closes_caller(self) -> None:
        skill, _ = await _setup_skill()
        ws = FakeWS()
        skill._caller_ws = ws  # type: ignore[assignment]
        skill._claim_active = True
        result = await skill.handle("end_call", {})
        assert json.loads(result.output)["ok"] is True
        assert ws.closed is True


class TestUnknownTool:
    async def test_returns_error(self) -> None:
        skill, _ = await _setup_skill()
        result = await skill.handle("nope", {})
        assert "error" in json.loads(result.output)

"""Proactive turns wait for a listener (T1.44).

Regression tests for the 2026-10-12 finding (docs/triage.md T1.44): an
injected turn went ahead with no client connected, so it was spoken to
nobody (a reminder at boot with `HUXLEY_PERSONA_AT_BOOT`, or whenever the
walkie dropped off Wi-Fi). Now it waits, in order, until a client can hear
it, unless its expiry passes first; a turn cut off by a disconnect is said
again, whole.

Each test drives a real `TurnCoordinator` + `FocusManager` with a stub
provider and `_Server`, a stand-in for the AudioServer's side: the
listener epoch (a number per connection, None with no client) and the
confirming ping (the pong proves the client received everything sent
before it).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from huxley.focus.manager import FocusManager
from huxley.turn.coordinator import (
    TurnCoordinator,
    TurnSource,
    TurnState,
    turn_waits_forbidden,
)
from huxley.voice.stub import StubVoiceProvider
from huxley_sdk import (
    DEFAULT_INJECT_EXPIRY,
    AudioStream,
    ClaimEndReason,
    InjectOutcome,
    InjectPriority,
    InputClaim,
    ToolResult,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


async def _until(cond: Callable[[], bool], timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


def _said(provider: StubVoiceProvider) -> list[str]:
    """Every prompt the coordinator sent the model, in order."""
    return [s[1] for s in provider.sent if s[0] == "send_conversation_message"]


def _waiting(coordinator: TurnCoordinator) -> list[str]:
    return [r.prompt for r in coordinator._injected_queue]


async def _speak(coordinator: TurnCoordinator, nbytes: int = 480) -> None:
    """The model says something short (480 bytes = 10 ms) and ends its
    response: the injected turn in flight ends normally."""
    await coordinator.on_audio_delta(b"\x00" * nbytes)
    await coordinator.on_audio_done()
    await coordinator.on_response_done()


class _Server:
    """The AudioServer as the coordinator sees it: who is listening (an
    epoch per connection, once its hello is out) and the confirming ping."""

    def __init__(self) -> None:
        self.epoch: int | None = None
        self._next = 0
        self.pong = True
        self.pings: list[int] = []
        self.gate: asyncio.Event | None = None

    def listener(self) -> int | None:
        return self.epoch

    async def received(self, epoch: int, _timeout_s: float) -> bool:
        self.pings.append(epoch)
        if self.gate is not None:
            await self.gate.wait()
        return self.pong and epoch == self.epoch

    async def connect(self, coordinator: TurnCoordinator) -> None:
        self._next += 1
        self.epoch = self._next
        await coordinator.on_client_presence(True)

    async def leave(self, coordinator: TurnCoordinator) -> None:
        self.epoch = None
        await coordinator.on_client_presence(False)


@pytest.fixture
def provider() -> StubVoiceProvider:
    p = StubVoiceProvider()
    p._connected = True
    return p


@pytest.fixture
def server() -> _Server:
    return _Server()


@pytest.fixture
def mocks(provider: StubVoiceProvider, server: _Server) -> dict[str, Any]:
    return {
        "send_audio": AsyncMock(),
        "send_audio_clear": AsyncMock(),
        "send_status": AsyncMock(),
        "send_model_speaking": AsyncMock(),
        "send_dev_event": AsyncMock(),
        "provider": provider,
        "dispatch_tool": AsyncMock(return_value=ToolResult(output="{}")),
        "listener_epoch": server.listener,
        "confirm_client_received": server.received,
    }


@pytest.fixture
async def focus_manager() -> AsyncIterator[FocusManager]:
    fm = FocusManager.with_default_channels()
    fm.start()
    yield fm
    await fm.stop()


@pytest.fixture
async def coordinator(
    mocks: dict[str, Any], focus_manager: FocusManager
) -> AsyncIterator[TurnCoordinator]:
    """No client connected yet, as at boot with `HUXLEY_PERSONA_AT_BOOT`
    or while the walkie is off Wi-Fi."""
    coord = TurnCoordinator(**mocks, focus_manager=focus_manager)
    yield coord
    await coord.close()


async def _press(coordinator: TurnCoordinator) -> None:
    """A committed press, as the walkie sends it."""
    await coordinator.on_ptt_start()
    assert coordinator.current_turn is not None
    coordinator.current_turn.user_audio_bytes = 300 * 48
    await coordinator.on_ptt_stop()


class TestWaitsForAListener:
    async def test_not_spoken_without_a_client_then_spoken_when_one_connects(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        """The T1.44 regression: before, this fired at once into no client."""
        await coordinator.inject_turn("Es hora de la pastilla.")

        assert coordinator.current_turn is None
        assert _said(provider) == []
        assert _waiting(coordinator) == ["Es hora de la pastilla."]

        await server.connect(coordinator)

        assert _said(provider) == ["Es hora de la pastilla."]
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.source is TurnSource.INJECTED

    async def test_inject_and_wait_reports_delivered_after_playback_and_a_pong(
        self, coordinator: TurnCoordinator, server: _Server
    ) -> None:
        task = asyncio.create_task(coordinator.inject_turn_and_wait("Llamada de Ana."))
        await asyncio.sleep(0.02)
        assert not task.done()

        await server.connect(coordinator)
        await _speak(coordinator)

        assert await asyncio.wait_for(task, timeout=1.0) is InjectOutcome.DELIVERED
        assert server.pings == [1]

    async def test_presence_is_read_live_not_cached(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        """An app being started isn't current and misses presence calls
        (critic B2): the gate reads the server, so a client that left
        without telling this coordinator gets nothing."""
        server.epoch = 1
        await coordinator.on_client_presence(True)
        server.epoch = None  # left; the call went to another app

        await coordinator.inject_turn("Aviso.")

        assert _said(provider) == []
        assert _waiting(coordinator) == ["Aviso."]

    async def test_a_client_leaving_holds_new_turns_at_once(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        """No grace for turns (content gets 5 s): nothing is lost by waiting."""
        await server.connect(coordinator)
        await server.leave(coordinator)

        await coordinator.inject_turn("Aviso.")

        assert _said(provider) == []
        assert len(coordinator._injected_queue) == 1


class TestExpiry:
    async def test_expires_while_waiting_and_is_never_said(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        task = asyncio.create_task(
            coordinator.inject_turn_and_wait(
                "Es hora de la pastilla.", expires_after=timedelta(seconds=0.05)
            )
        )
        with capture_logs() as logs:
            assert await asyncio.wait_for(task, timeout=1.0) is InjectOutcome.EXPIRED
        assert coordinator._injected_queue == []
        assert any(e["event"] == "coord.inject_turn_expired" for e in logs)

        await server.connect(coordinator)
        assert _said(provider) == []

    async def test_the_default_expiry_is_ten_minutes_and_none_never_expires(
        self, coordinator: TurnCoordinator
    ) -> None:
        loop = asyncio.get_running_loop()
        await coordinator.inject_turn("aviso")
        await coordinator.inject_turn("mensaje", expires_after=None)

        default, forever = coordinator._injected_queue
        assert timedelta(minutes=10) == DEFAULT_INJECT_EXPIRY
        assert default.expires_at is not None
        assert abs(default.expires_at - (loop.time() + 600)) < 1.0
        assert forever.expires_at is None
        assert forever.expiry is None

    async def test_a_live_turn_is_never_cut_for_its_expiry(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        await server.connect(coordinator)
        task = asyncio.create_task(
            coordinator.inject_turn_and_wait("Aviso.", expires_after=timedelta(seconds=0.02))
        )
        await _until(lambda: _said(provider) == ["Aviso."])
        await asyncio.sleep(0.05)  # past the deadline while being said
        await _speak(coordinator)

        assert await asyncio.wait_for(task, timeout=1.0) is InjectOutcome.DELIVERED


class TestOrder:
    async def test_waiting_turns_are_said_in_order_one_at_a_time(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        for prompt in ("uno", "dos", "tres"):
            await coordinator.inject_turn(prompt)

        await server.connect(coordinator)
        assert _said(provider) == ["uno"]

        await _speak(coordinator)
        # "dos" waits until the client has played "uno" (and answered a ping).
        await _until(lambda: _said(provider) == ["uno", "dos"])
        await _speak(coordinator)
        await _until(lambda: _said(provider) == ["uno", "dos", "tres"])
        await _speak(coordinator)
        await _until(lambda: coordinator._unconfirmed is None)
        assert server.pings == [1, 1, 1]

    async def test_the_next_turn_waits_for_the_previous_to_be_heard(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        server.gate = asyncio.Event()
        await server.connect(coordinator)
        await coordinator.inject_turn("uno")
        await _speak(coordinator)

        await coordinator.inject_turn("dos")
        await asyncio.sleep(0.05)
        assert _said(provider) == ["uno"], "the ping for 'uno' is not answered yet"

        server.gate.set()
        await _until(lambda: _said(provider) == ["uno", "dos"])


class TestDedup:
    async def test_a_same_key_turn_replaces_the_waiting_one_in_place(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        first = asyncio.create_task(
            coordinator.inject_turn_and_wait("pastilla v1", dedup_key="r1")
        )
        await asyncio.sleep(0)
        await coordinator.inject_turn("mensaje de Ana")
        await coordinator.inject_turn("pastilla v2", dedup_key="r1")

        assert await asyncio.wait_for(first, timeout=1.0) is InjectOutcome.DROPPED
        assert _waiting(coordinator) == ["pastilla v2", "mensaje de Ana"]

        await server.connect(coordinator)
        assert _said(provider) == ["pastilla v2"]

    async def test_a_same_key_turn_while_one_is_being_heard_is_dropped(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        server.gate = asyncio.Event()
        await server.connect(coordinator)
        await coordinator.inject_turn("pastilla", dedup_key="r1")
        await _speak(coordinator)  # said, awaiting confirmation

        outcome = await coordinator.inject_turn_and_wait("pastilla otra vez", dedup_key="r1")

        assert outcome is InjectOutcome.DROPPED
        server.gate.set()
        await _until(lambda: coordinator._unconfirmed is None)
        assert _said(provider) == ["pastilla"]


class TestCutOff:
    async def test_a_client_leaving_mid_turn_cuts_it_and_it_is_said_whole_later(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        await server.connect(coordinator)
        task = asyncio.create_task(coordinator.inject_turn_and_wait("Es hora de la pastilla."))
        await _until(lambda: coordinator.current_turn is not None)
        await coordinator.on_audio_delta(b"\x00" * 480)  # half said

        await server.leave(coordinator)

        assert coordinator.current_turn is None
        assert ("cancel_current_response",) in provider.sent
        assert _waiting(coordinator) == ["Es hora de la pastilla."]
        assert not task.done()

        await server.connect(coordinator)
        first, again = _said(provider)
        assert first == "Es hora de la pastilla."
        assert again.endswith("Es hora de la pastilla.")
        assert "did not hear" in again, "the model is told it wasn't heard"
        await _speak(coordinator)
        assert await asyncio.wait_for(task, timeout=1.0) is InjectOutcome.DELIVERED

    async def test_a_client_leaving_before_playback_ends_means_not_delivered(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        """The model streams faster than real time: the turn ends on the
        server while the walkie still has seconds of it to play."""
        await server.connect(coordinator)
        task = asyncio.create_task(coordinator.inject_turn_and_wait("Aviso largo."))
        await _until(lambda: coordinator.current_turn is not None)
        await _speak(coordinator, nbytes=48_000)  # 1 s still to play

        await server.leave(coordinator)

        assert _waiting(coordinator) == ["Aviso largo."]
        await server.connect(coordinator)
        assert len(_said(provider)) == 2
        await _speak(coordinator)
        assert await asyncio.wait_for(task, timeout=2.0) is InjectOutcome.DELIVERED

    async def test_evicted_after_the_turn_before_the_ping_is_not_delivered(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        """The critic's test: the walkie reconnects (evicting its dead socket)
        after the turn ended and before the ping. A pong from the new
        connection proves nothing about audio sent to the old one: not
        delivered, said exactly once more, to the new connection."""
        await server.connect(coordinator)
        task = asyncio.create_task(coordinator.inject_turn_and_wait("Aviso."))
        await _until(lambda: coordinator.current_turn is not None)
        await _speak(coordinator, nbytes=4_800)  # 100 ms still to play
        server.epoch = 2  # replaced, no presence call yet

        await _until(lambda: _waiting(coordinator) == ["Aviso."] or len(_said(provider)) == 2)
        assert not task.done()
        server._next = 2
        await coordinator.on_client_presence(True)  # the new client's handshake

        await _until(lambda: len(_said(provider)) == 2)
        await _speak(coordinator)
        assert await asyncio.wait_for(task, timeout=1.0) is InjectOutcome.DELIVERED
        assert len(_said(provider)) == 2
        assert server.pings[-1] == 2

    async def test_the_coordinator_itself_checks_who_it_said_it_to(
        self,
        coordinator: TurnCoordinator,
        provider: StubVoiceProvider,
        server: _Server,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even a ping answered by whoever is current (the first draft's
        server) must not deliver a turn said to an earlier connection."""

        async def any_pong(epoch: int, _timeout_s: float) -> bool:
            server.pings.append(epoch)
            return True

        monkeypatch.setattr(coordinator, "_confirm_client_received", any_pong)
        await server.connect(coordinator)
        task = asyncio.create_task(coordinator.inject_turn_and_wait("Aviso."))
        await _until(lambda: coordinator.current_turn is not None)
        await _speak(coordinator, nbytes=4_800)
        server.epoch = 2  # replaced before the confirmation

        await _until(lambda: len(_said(provider)) == 2)
        assert not task.done()
        assert server.pings == [], "no ping for a connection that is gone"

    async def test_an_unanswered_ping_is_not_delivered_and_waits_for_a_new_connection(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        """A dead socket the server hasn't noticed: no pong (the server
        then aborts it). Nothing fires again into the same connection."""
        await server.connect(coordinator)
        server.pong = False
        task = asyncio.create_task(coordinator.inject_turn_and_wait("Aviso."))
        await _until(lambda: coordinator.current_turn is not None)
        await _speak(coordinator)

        await _until(lambda: _waiting(coordinator) == ["Aviso."])
        await asyncio.sleep(0.05)
        assert len(_said(provider)) == 1, "no second try into the same socket"

        server.pong = True
        await server.leave(coordinator)  # the abort
        await server.connect(coordinator)  # the walkie is back
        await _until(lambda: len(_said(provider)) == 2)
        await _speak(coordinator)
        assert await asyncio.wait_for(task, timeout=1.0) is InjectOutcome.DELIVERED

    async def test_a_ping_that_never_returns_is_not_delivered(
        self,
        coordinator: TurnCoordinator,
        server: _Server,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(TurnCoordinator, "_CONFIRM_PING_TIMEOUT_S", -4.95)  # backstop 50 ms
        server.gate = asyncio.Event()  # never set
        await server.connect(coordinator)
        task = asyncio.create_task(coordinator.inject_turn_and_wait("Aviso."))
        await _until(lambda: coordinator.current_turn is not None)
        await _speak(coordinator)

        await _until(lambda: _waiting(coordinator) == ["Aviso."])
        assert not task.done()

    async def test_a_session_drop_mid_turn_says_it_again(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        await server.connect(coordinator)
        await coordinator.inject_turn("Aviso.")
        await coordinator.on_audio_delta(b"\x00" * 480)

        await coordinator.on_session_disconnected()
        assert _waiting(coordinator) == ["Aviso."]

        await coordinator.on_session_connected()
        assert len(_said(provider)) == 2

    async def test_a_press_into_a_playing_turn_counts_as_heard(
        self, coordinator: TurnCoordinator, server: _Server
    ) -> None:
        await server.connect(coordinator)
        task = asyncio.create_task(coordinator.inject_turn_and_wait("Aviso."))
        await _until(lambda: coordinator.current_turn is not None)
        await coordinator.on_audio_delta(b"\x00" * 4_800)  # playing now

        await coordinator.on_ptt_start()

        assert await asyncio.wait_for(task, timeout=1.0) is InjectOutcome.DELIVERED
        assert coordinator._injected_queue == []

    async def test_a_press_before_the_turn_started_playing_says_it_after(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        """Critic B3: a drained turn queues behind the previous answer on the
        client; the press empties the buffer before a sample of it played.
        It follows the user's turn instead of counting as heard."""
        await server.connect(coordinator)
        await _press(coordinator)
        await coordinator.inject_turn("Aviso.")  # waits behind the answer
        await coordinator.on_audio_delta(b"\x00" * 96_000)  # a 2 s answer
        await coordinator.on_audio_done()
        await coordinator.on_response_done()  # turn ends: "Aviso." fires
        assert _said(provider) == ["Aviso."]
        await coordinator.on_audio_delta(b"\x00" * 480)  # queued behind the answer

        await coordinator.on_ptt_start()  # 2 s of answer still unplayed

        assert _waiting(coordinator) == ["Aviso."]
        assert coordinator.current_turn is not None
        coordinator.current_turn.user_audio_bytes = 300 * 48
        await coordinator.on_ptt_stop()
        await _speak(coordinator)  # the user's turn ends
        assert len(_said(provider)) == 2

    async def test_a_cut_off_turn_past_its_expiry_is_not_said_again(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        await server.connect(coordinator)
        task = asyncio.create_task(
            coordinator.inject_turn_and_wait("Aviso.", expires_after=timedelta(seconds=0.02))
        )
        await _until(lambda: coordinator.current_turn is not None)
        await asyncio.sleep(0.05)

        await server.leave(coordinator)

        assert await asyncio.wait_for(task, timeout=1.0) is InjectOutcome.EXPIRED
        await server.connect(coordinator)
        assert _said(provider) == ["Aviso."]


class TestRaces:
    async def test_a_press_during_a_drain_is_not_overwritten(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        """Critic: an await between the drain's check and the turn let a
        press in, and the injected turn then replaced the user's."""
        await coordinator.inject_turn("Aviso.")
        server.epoch = 1
        original = coordinator._log.ainfo

        async def press_while_logging(event: str, **kw: Any) -> None:
            if event == "coord.inject_turn_dequeued":
                await coordinator.on_ptt_start()
            await original(event, **kw)

        coordinator._log.ainfo = press_while_logging  # type: ignore[method-assign]
        await coordinator.on_client_presence(True)

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.source is TurnSource.USER
        assert _said(provider) == []
        assert _waiting(coordinator) == ["Aviso."]


class TestWaitCue:
    async def test_injected_turns_never_arm_the_waiting_cue(
        self, coordinator: TurnCoordinator, server: _Server
    ) -> None:
        await coordinator.inject_turn("uno")
        await server.connect(coordinator)  # fired from the queue
        assert not coordinator._wait_cue.armed
        await coordinator.on_tool_call("c1", "some_tool", {})
        assert not coordinator._wait_cue.armed
        await server.leave(coordinator)  # cut off
        assert not coordinator._wait_cue.armed
        await server.connect(coordinator)  # said again
        assert not coordinator._wait_cue.armed


class TestClaims:
    async def _start_call(self, coordinator: TurnCoordinator) -> None:
        async def _peer() -> AsyncIterator[bytes]:
            while True:
                yield b"\x00" * 480
                await asyncio.sleep(0.01)

        await coordinator.start_input_claim(
            InputClaim(on_mic_frame=AsyncMock(), speaker_source=_peer())
        )

    async def test_a_call_live_when_the_client_returns_holds_normal_turns(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        await server.connect(coordinator)
        await self._start_call(coordinator)
        await server.leave(coordinator)
        await coordinator.inject_turn("pastilla", priority=InjectPriority.BLOCK_BEHIND_COMMS)

        await server.connect(coordinator)
        assert _said(provider) == [], "the call is still live"

        await coordinator.cancel_active_claim(reason=ClaimEndReason.NATURAL)
        await _until(lambda: _said(provider) == ["pastilla"])

    async def test_preempt_goes_through_a_live_call_when_the_client_returns(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        await server.connect(coordinator)
        await self._start_call(coordinator)
        await server.leave(coordinator)
        await coordinator.inject_turn("mensaje")
        await coordinator.inject_turn("alarma", priority=InjectPriority.PREEMPT)

        await server.connect(coordinator)

        assert _said(provider) == ["alarma"]
        assert _waiting(coordinator) == ["mensaje"]


class TestContent:
    async def test_a_held_stream_stays_parked_until_the_narration_ends(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
        server: _Server,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The client is back: the waiting turn's DIALOG parks the content
        first, so the book doesn't play for a tick before the narration."""
        monkeypatch.setattr(TurnCoordinator, "_CLIENT_ABSENT_GRACE_S", 0.01)
        runs = 0

        async def _book() -> AsyncIterator[bytes]:
            while True:
                yield b"\x00" * 480
                await asyncio.sleep(0.005)

        def factory() -> AsyncIterator[bytes]:
            nonlocal runs
            runs += 1
            return _book()

        await server.connect(coordinator)
        mocks["dispatch_tool"].return_value = ToolResult(
            output="{}",
            side_effect=AudioStream(factory=factory, patience=timedelta(minutes=30)),
        )
        await _press(coordinator)
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()
        await _until(lambda: runs == 1)

        await server.leave(coordinator)
        await _until(lambda: coordinator._client_absent)
        await coordinator.inject_turn("pastilla")

        await server.connect(coordinator)

        assert _said(provider) == ["pastilla"]
        await asyncio.sleep(0.05)
        assert runs == 1, "the book waits for the narration"
        await _speak(coordinator)
        await _until(lambda: runs == 2)


class TestWithdrawAndShutdown:
    async def test_cancelling_the_wait_withdraws_a_waiting_turn(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, server: _Server
    ) -> None:
        """Telegram: the caller hung up while the announcement waited."""
        task = asyncio.create_task(coordinator.inject_turn_and_wait("Te llama Ana."))
        await _until(lambda: _waiting(coordinator) == ["Te llama Ana."])

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert coordinator._injected_queue == []
        await server.connect(coordinator)
        assert _said(provider) == []

    async def test_close_drops_what_still_waits(self, coordinator: TurnCoordinator) -> None:
        task = asyncio.create_task(coordinator.inject_turn_and_wait("Aviso."))
        await asyncio.sleep(0)

        await coordinator.close()

        assert await asyncio.wait_for(task, timeout=1.0) is InjectOutcome.DROPPED
        assert await coordinator.inject_turn_and_wait("Tarde.") is InjectOutcome.DROPPED

    async def test_waiting_summary_for_health(self, coordinator: TurnCoordinator) -> None:
        assert coordinator.waiting_summary() == (0, None)
        await coordinator.inject_turn("uno")
        await coordinator.inject_turn("dos")
        count, oldest = coordinator.waiting_summary()
        assert count == 2
        assert oldest is not None
        assert oldest >= 0


class TestCannotWait:
    async def test_from_a_tool_handler_it_queues_instead_of_deadlocking(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        server: _Server,
    ) -> None:
        await server.connect(coordinator)
        outcomes: list[InjectOutcome] = []
        spawned: list[asyncio.Task[InjectOutcome]] = []

        async def _tool(_name: str, _args: dict[str, Any]) -> ToolResult:
            outcomes.append(await coordinator.inject_turn_and_wait("después"))
            # A task the handler spawns copies its context, yet may wait.
            spawned.append(asyncio.create_task(coordinator.inject_turn_and_wait("luego")))
            return ToolResult(output="{}")

        mocks["dispatch_tool"].side_effect = _tool
        await _press(coordinator)

        await asyncio.wait_for(coordinator.on_tool_call("c1", "t", {}), timeout=1.0)

        assert outcomes == [InjectOutcome.QUEUED]
        await asyncio.sleep(0.02)
        assert not spawned[0].done(), "the spawned task waits for its turn"
        assert _waiting(coordinator) == ["después", "luego"]
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state is not TurnState.IDLE

    async def test_from_skill_setup_it_queues_instead_of_deadlocking(
        self, coordinator: TurnCoordinator
    ) -> None:
        """At boot the turn would wait for the walkie, whose connection
        waits for the persona's start."""
        with turn_waits_forbidden():
            outcome = await asyncio.wait_for(coordinator.inject_turn_and_wait("hola"), timeout=1.0)
        assert outcome is InjectOutcome.QUEUED
        assert _waiting(coordinator) == ["hola"]


class TestWithoutAServer:
    async def test_presence_calls_drive_the_gate(
        self, mocks: dict[str, Any], focus_manager: FocusManager, provider: StubVoiceProvider
    ) -> None:
        """Tests and tools that build a coordinator without a server:
        `on_client_presence` alone says who listens. Written against the
        API the old coordinator had too: it fails there (the turn was
        spoken to nobody), checked 2026-10-12 against main's sources."""
        del mocks["listener_epoch"], mocks["confirm_client_received"]
        coord = TurnCoordinator(**mocks, focus_manager=focus_manager)
        try:
            await coord.on_client_presence(False)  # the walkie dropped off Wi-Fi
            await coord.inject_turn("Aviso.")
            assert _said(provider) == [], "spoken to nobody"
            await coord.on_client_presence(True)
            assert _said(provider) == ["Aviso."]
        finally:
            await coord.close()

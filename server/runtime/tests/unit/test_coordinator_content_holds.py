"""Content holds: a press pauses what plays, the turn's end resumes it.

Regression tests for the 2026-10-12 field log (see docs/decisions.md,
"A press pauses content; the turn's end resumes it"):

- A. 35 presses stopped the music; 12 were not media requests (volume x6,
  "¿Me escuchas?", a noise press, "¿En qué capítulo vamos?"). Nothing
  restarted it, and the model kept saying it was playing.
- B. With the walkie off Wi-Fi, a Spotify stream ran 33 minutes into
  `_send_raw`'s silent drop; an audiobook skips what nobody heard.
- C. A provider reconnect (planned, or after session_expired) stopped
  whatever played.

Each test drives a real `TurnCoordinator` + `FocusManager` with a stub
provider and a counting media factory: `runs` is how many times the
framework called `factory()` (2 = resumed), `chunks` how much it pumped.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from huxley.focus.manager import FocusManager
from huxley.turn.coordinator import TurnCoordinator
from huxley.voice.stub import StubVoiceProvider
from huxley_sdk import (
    AudioStream,
    CancelMedia,
    InjectPriority,
    InputClaim,
    SetVolume,
    SideEffect,
    ToolResult,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class _Media:
    """A resumable stream: every `factory()` call is one run."""

    def __init__(self) -> None:
        self.runs = 0
        self.chunks = 0

    def factory(self) -> AsyncIterator[bytes]:
        self.runs += 1
        return self._gen()

    async def _gen(self) -> AsyncIterator[bytes]:
        while True:
            self.chunks += 1
            yield b"\x00\x00" * 240  # 10 ms
            await asyncio.sleep(0.005)


async def _until(cond: Callable[[], bool], timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


async def _stays(cond: Callable[[], bool], for_s: float = 0.25) -> None:
    deadline = asyncio.get_running_loop().time() + for_s
    while asyncio.get_running_loop().time() < deadline:
        assert cond()
        await asyncio.sleep(0.005)


@pytest.fixture(autouse=True)
def _short_transcript_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests deliver no transcript; don't wait 1.5 s for one."""
    monkeypatch.setattr(TurnCoordinator, "_TRANSCRIPT_WAIT_S", 0.05)


@pytest.fixture
def provider() -> StubVoiceProvider:
    p = StubVoiceProvider()
    p._connected = True
    return p


@pytest.fixture
def mocks(provider: StubVoiceProvider) -> dict[str, Any]:
    return {
        "send_audio": AsyncMock(),
        "send_audio_clear": AsyncMock(),
        "send_status": AsyncMock(),
        "send_model_speaking": AsyncMock(),
        "send_dev_event": AsyncMock(),
        "send_stream_started": AsyncMock(),
        "send_stream_ended": AsyncMock(),
        "provider": provider,
        "dispatch_tool": AsyncMock(return_value=ToolResult(output="{}")),
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
    coord = TurnCoordinator(**mocks, focus_manager=focus_manager)
    yield coord
    await coord.close()


async def _press(coord: TurnCoordinator, audio_ms: int = 300) -> None:
    """Press and release: `audio_ms` of speech (the floor is 133 ms)."""
    await coord.on_ptt_start()
    assert coord.current_turn is not None
    coord.current_turn.user_audio_bytes = audio_ms * 48
    await coord.on_ptt_stop()


async def _answer(coord: TurnCoordinator, nbytes: int = 480) -> None:
    """One model response: `nbytes` of speech (480 = 10 ms), then done."""
    await coord.on_audio_delta(b"\x00" * nbytes)
    await coord.on_audio_done()
    await coord.on_response_done()


async def _turn(
    coord: TurnCoordinator,
    mocks: dict[str, Any],
    side_effect: SideEffect | None = None,
    *,
    tool: bool = True,
    answer_bytes: int = 480,
) -> None:
    """A full user turn: press, optional tool call, spoken answer."""
    await _press(coord)
    if tool:
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=side_effect)
        await coord.on_tool_call("c1", "tool", {})
        await coord.on_response_done()  # follow-up round, or the barrier
        if coord.current_turn is None:
            return  # an AudioStream: no follow-up round
    await _answer(coord, answer_bytes)


async def _start(coord: TurnCoordinator, mocks: dict[str, Any], **stream_kwargs: Any) -> _Media:
    media = _Media()
    stream = AudioStream(factory=media.factory, label="Radio Uno", **stream_kwargs)
    await _turn(coord, mocks, stream)
    await _until(lambda: media.chunks > 0)
    return media


def _down_for(coord: TurnCoordinator, seconds: float) -> None:
    """Pretend the voice session dropped `seconds` ago."""
    assert coord._session_down_since is not None
    coord._session_down_since -= seconds


async def _flowing(media: _Media) -> None:
    """The stream pumps audio again."""
    before = media.chunks
    await _until(lambda: media.chunks > before + 2)


# ---------------------------------------------------------------------------


class TestPressPausesAndResumes:
    """Failure A."""

    @pytest.mark.parametrize(
        ("side_effect", "tool"),
        [
            (SetVolume(level=30), True),  # "bájale el volumen" x6 in the log
            (None, True),  # an info tool: "¿en qué capítulo vamos?"
            (None, False),  # no tool at all: "¿me escuchas?"
        ],
        ids=["volume", "info_tool", "question"],
    )
    async def test_turn_that_does_not_touch_media_resumes_it(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        side_effect: SideEffect | None,
        tool: bool,
    ) -> None:
        media = await _start(coordinator, mocks)

        await coordinator.on_ptt_start()
        assert coordinator.current_media_task is None, "the press pauses the stream"
        assert coordinator.current_turn is not None
        coordinator.current_turn.user_audio_bytes = 300 * 48
        await coordinator.on_ptt_stop()
        if tool:
            mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=side_effect)
            await coordinator.on_tool_call("c1", "tool", {})
            await coordinator.on_response_done()
        assert media.runs == 1, "nothing plays during the turn"
        await _answer(coordinator)

        await _until(lambda: media.runs == 2)
        await _flowing(media)

    async def test_pause_tool_does_not_resume(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        media = await _start(coordinator, mocks)

        await _turn(coordinator, mocks, CancelMedia())

        assert coordinator._content_obs is None
        await _stays(lambda: media.runs == 1)

    async def test_new_stream_replaces_without_resuming_the_old_one(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        old = await _start(coordinator, mocks)
        new = _Media()

        await _turn(coordinator, mocks, AudioStream(factory=new.factory))

        await _until(lambda: new.chunks > 0)
        await _stays(lambda: old.runs == 1)

    async def test_new_stream_dropped_by_a_preempt_reminder_still_ends_the_old(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """The user asked for something else; a PREEMPT reminder drained at
        turn end drops the new stream. The old one must not come back."""
        old = await _start(coordinator, mocks, patience=None)
        await _press(coordinator)
        await coordinator.inject_turn("reminder", priority=InjectPriority.PREEMPT)
        mocks["dispatch_tool"].return_value = ToolResult(
            output="{}", side_effect=AudioStream(factory=_Media().factory)
        )
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()  # fires the reminder instead

        assert coordinator._content_obs is None
        await _answer(coordinator)  # the reminder's narration ends
        await _stays(lambda: old.runs == 1)

    async def test_too_short_press_resumes_at_once(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A noise press (one in the log) must not leave silence. Nothing was
        committed, so no transcript is coming: no wait for one."""
        monkeypatch.setattr(TurnCoordinator, "_TRANSCRIPT_WAIT_S", 1.0)
        media = await _start(coordinator, mocks)

        await _press(coordinator, audio_ms=50)

        assert ("commit_and_request_response",) not in provider.sent[-2:]
        await _until(lambda: media.runs == 2, timeout_s=0.5)

    @pytest.mark.parametrize(
        "heard",
        ["Para.", "¡Ya basta!", "Apaga la música, por favor", "silencio", "ya"],
    )
    async def test_a_stop_request_keeps_it_stopped_even_without_the_tool(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], heard: str
    ) -> None:
        """T1.30's first risk: the model answers "listo" to "para" without
        calling the stop tool, and the music comes back against what a
        blind user said. The press's own words decide: err toward silence."""
        coordinator.set_ui_strings(
            {
                "content_stop_words": "para pausa silencio apaga basta ya",
                "content_filler_words": "la el musica radio por favor",
            }
        )
        media = await _start(coordinator, mocks)

        await _press(coordinator)
        coordinator.on_user_transcript(heard)
        await _answer(coordinator)  # "Listo." and no tool call

        await _until(lambda: coordinator._content_obs is None)
        await _stays(lambda: media.runs == 1)

    async def test_a_late_transcript_still_counts(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The transcript can land after the turn ended (08:38 in the log)."""
        monkeypatch.setattr(TurnCoordinator, "_TRANSCRIPT_WAIT_S", 1.0)
        coordinator.set_ui_strings({"content_stop_words": "para", "content_filler_words": ""})
        media = await _start(coordinator, mocks)
        await _press(coordinator)
        await _answer(coordinator)

        await asyncio.sleep(0.2)
        assert media.runs == 1, "the resume waits for what the user said"
        coordinator.on_user_transcript("para")

        await _until(lambda: coordinator._content_obs is None)
        assert media.runs == 1

    @pytest.mark.parametrize(
        "heard", ["Sube el volumen", "¿Para qué sirve esto?", "¿Me escuchas?"]
    )
    async def test_other_words_resume(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], heard: str
    ) -> None:
        coordinator.set_ui_strings(
            {"content_stop_words": "para pausa ya", "content_filler_words": "la el esto"}
        )
        media = await _start(coordinator, mocks)

        await _press(coordinator)
        coordinator.on_user_transcript(heard)
        await _answer(coordinator)

        await _until(lambda: media.runs == 2)

    async def test_held_too_long_is_stopped_not_resumed(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T1.30's second risk: a bound on how long media stays parked
        (a turn that never ends, a very long back-and-forth)."""
        monkeypatch.setattr(TurnCoordinator, "_PARKED_STOP_AFTER_S", 0.05)
        media = await _start(coordinator, mocks)
        await coordinator.on_ptt_start()
        await asyncio.sleep(0.1)
        assert coordinator.current_turn is not None
        coordinator.current_turn.user_audio_bytes = 300 * 48
        await coordinator.on_ptt_stop()
        await _answer(coordinator)

        await _until(lambda: coordinator._content_obs is None)
        assert media.runs == 1

    async def test_commit_failure_resumes(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        media = await _start(coordinator, mocks)

        await _press(coordinator)
        await coordinator.on_commit_failed()

        await _until(lambda: media.runs == 2)

    async def test_resume_waits_for_the_answer_to_play(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """Content started at once would queue behind the answer in the
        client's buffer, and the next press would flush it unheard."""
        media = await _start(coordinator, mocks)

        await _turn(coordinator, mocks, tool=False, answer_bytes=48_000 * 3 // 10)  # 300 ms

        await _stays(lambda: media.runs == 1, for_s=0.2)
        await _until(lambda: media.runs == 2, timeout_s=1.0)

    async def test_press_during_the_answer_cancels_the_pending_resume(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        media = await _start(coordinator, mocks)
        await _turn(coordinator, mocks, tool=False, answer_bytes=48_000 * 2)  # 2 s answer

        await coordinator.on_ptt_start()  # a follow-up question mid-answer
        await asyncio.sleep(0.1)
        assert media.runs == 1
        assert coordinator.current_turn is not None
        coordinator.current_turn.user_audio_bytes = 300 * 48
        await coordinator.on_ptt_stop()
        await _answer(coordinator)

        await _until(lambda: media.runs == 2)

    async def test_model_is_told_before_the_commit(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], provider: StubVoiceProvider
    ) -> None:
        """The model said "y ahora suena The Beatles" 3 s after a press
        stopped them. It must know the press paused the stream."""
        await _start(coordinator, mocks)
        provider.sent.clear()

        await _press(coordinator)

        assert provider.sent[-1] == ("commit_and_request_response",)
        kind, note = provider.sent[-2]
        assert kind == "send_conversation_message"
        assert "(Radio Uno)" in note
        assert "{label}" not in note

    async def test_model_note_uses_the_persona_text(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], provider: StubVoiceProvider
    ) -> None:
        coordinator.set_ui_strings({"content_held": "[en pausa{label}]"})
        await _start(coordinator, mocks)

        await _press(coordinator)

        assert ("send_conversation_message", "[en pausa (Radio Uno)]") in provider.sent

    async def test_no_note_without_content_or_for_a_dropped_press(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], provider: StubVoiceProvider
    ) -> None:
        await _press(coordinator)  # nothing playing
        await _answer(coordinator)
        await _start(coordinator, mocks)
        await _press(coordinator, audio_ms=50)  # dropped: no stale note

        assert not [s for s in provider.sent if s[0] == "send_conversation_message"]

    async def test_audio_clear_goes_out_before_model_speaking_false(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """The walkie drops in-flight audio from its press until
        model_speaking=false; the flush must already be out by then, or
        music in flight plays over the user's question."""
        await _start(coordinator, mocks)
        order: list[str] = []
        mocks["send_audio_clear"].side_effect = lambda: order.append("audio_clear")

        async def speaking(value: bool) -> None:
            order.append(f"speaking={value}")

        coordinator._speaking_state._notify = speaking

        await coordinator.on_ptt_start()

        assert order.index("audio_clear") < order.index("speaking=False"), order

    async def test_pause_and_resume_are_announced_to_the_client(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        media = await _start(coordinator, mocks)
        assert mocks["send_stream_started"].await_count == 1

        await coordinator.on_ptt_start()
        mocks["send_stream_ended"].assert_awaited_once()
        assert coordinator.current_turn is not None
        coordinator.current_turn.user_audio_bytes = 300 * 48
        await coordinator.on_ptt_stop()
        await _answer(coordinator)
        await _until(lambda: media.runs == 2)

        await _until(lambda: mocks["send_stream_started"].await_count == 2)
        (stream_id, label, _), _ = mocks["send_stream_started"].await_args
        assert label == "Radio Uno"
        assert stream_id == mocks["send_stream_ended"].await_args.args[0]

    async def test_reminder_pause_and_resume_are_announced_to_the_client(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """An injected turn takes the speaker through focus, not a hold:
        the client must still hear stream_ended, then stream_started again
        when the stream comes back, same stream_id, never two starts in a
        row."""
        from datetime import timedelta

        media = await _start(coordinator, mocks, patience=timedelta(minutes=30))
        assert mocks["send_stream_started"].await_count == 1

        await coordinator.inject_turn("a reminder")
        await _until(lambda: mocks["send_stream_ended"].await_count == 1)
        (ended_id, reason), _ = mocks["send_stream_ended"].await_args
        assert reason == "interrupted"
        await _answer(coordinator)
        await _until(lambda: media.runs == 2)

        await _until(lambda: mocks["send_stream_started"].await_count == 2)
        assert mocks["send_stream_started"].await_args.args[0] == ended_id
        assert mocks["send_stream_ended"].await_count == 1

    async def test_reminder_that_evicts_the_stream_announces_its_end(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """No patience: the reminder stops the stream for good. The client
        must still be told it ended, once."""
        media = await _start(coordinator, mocks)

        await coordinator.inject_turn("a reminder")
        await _until(lambda: mocks["send_stream_ended"].await_count == 1)
        await _answer(coordinator)

        await _stays(lambda: media.runs == 1)
        assert mocks["send_stream_ended"].await_count == 1
        assert mocks["send_stream_started"].await_count == 1

    async def test_call_pause_is_announced_to_the_client(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        from datetime import timedelta

        await _start(coordinator, mocks, patience=timedelta(minutes=30))
        await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        await _until(lambda: coordinator.current_media_task is None)

        await _until(lambda: mocks["send_stream_ended"].await_count == 1)
        assert mocks["send_stream_ended"].await_args.args[1] == "interrupted"

    async def test_press_during_a_call_resumes_what_the_call_parked(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """A press during a call only hangs up; no turn follows, so the
        book the call parked comes back right away."""
        from datetime import timedelta

        media = await _start(coordinator, mocks, patience=timedelta(minutes=30))
        await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        await _until(lambda: coordinator.current_media_task is None)
        coordinator._claim_started_at = 0.0  # past the 300 ms debounce

        await coordinator.on_ptt_start()

        assert coordinator.current_turn is None, "hang-up only"
        await _until(lambda: media.runs == 2)

    async def test_finished_stream_is_not_replayed_by_a_later_turn(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        runs = 0

        def short() -> AsyncIterator[bytes]:
            nonlocal runs
            runs += 1

            async def gen() -> AsyncIterator[bytes]:
                yield b"\x00\x00"

            return gen()

        await _turn(coordinator, mocks, AudioStream(factory=short, patience=None))
        await _until(
            lambda: coordinator._content_obs is not None and coordinator._content_obs.finished
        )

        await _turn(coordinator, mocks, tool=False)
        await coordinator.inject_turn("a reminder")
        await _answer(coordinator)

        await _stays(lambda: runs == 1)

    async def test_press_in_an_outage_stops_the_content(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """The session drop no longer stops content (failure C), so in a
        real outage the press is the user's only way to silence the
        device."""
        await _start(coordinator, mocks)
        await coordinator.on_session_disconnected()
        _down_for(coordinator, 6.0)

        await coordinator.on_ptt_rejected()

        assert coordinator._content_obs is None
        mocks["send_audio_clear"].assert_awaited()


class TestNoClient:
    """Failure B."""

    @pytest.fixture(autouse=True)
    def _fast_grace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(TurnCoordinator, "_CLIENT_ABSENT_GRACE_S", 0.05)

    async def test_no_client_pauses_the_stream_and_a_client_back_resumes_it(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        media = await _start(coordinator, mocks)

        await coordinator.on_client_presence(False)
        await _until(lambda: coordinator.current_media_task is None)
        sent = mocks["send_audio"].await_count
        await _stays(lambda: mocks["send_audio"].await_count == sent)

        await coordinator.on_client_presence(True)

        await _until(lambda: media.runs == 2)
        await _flowing(media)

    async def test_a_short_absence_changes_nothing(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """A reload or a quick reconnect: no pause/resume round trip."""
        media = await _start(coordinator, mocks)

        await coordinator.on_client_presence(False)
        await coordinator.on_client_presence(True)

        await _stays(lambda: media.runs == 1 and coordinator.current_media_task is not None)

    async def test_a_long_absence_stops_the_stream(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Walkie off for the night: no music out of the blue next morning."""
        monkeypatch.setattr(TurnCoordinator, "_PARKED_STOP_AFTER_S", 0.1)
        media = await _start(coordinator, mocks)

        await coordinator.on_client_presence(False)
        await _until(lambda: coordinator.current_media_task is None)
        await asyncio.sleep(0.15)
        await coordinator.on_client_presence(True)

        assert coordinator._content_obs is None
        await _stays(lambda: media.runs == 1)

    async def test_both_holds_must_clear(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """The client drops during a turn: the turn ending is not enough."""
        media = await _start(coordinator, mocks)
        await coordinator.on_ptt_start()
        await coordinator.on_client_presence(False)
        await asyncio.sleep(0.1)  # past the grace
        assert coordinator.current_turn is not None
        coordinator.current_turn.user_audio_bytes = 300 * 48
        await coordinator.on_ptt_stop()
        await _answer(coordinator)
        await _stays(lambda: media.runs == 1)

        await coordinator.on_client_presence(True)

        await _until(lambda: media.runs == 2)


class TestProviderReconnect:
    """Failure C."""

    async def test_reconnect_with_nothing_in_progress_keeps_playing(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        media = await _start(coordinator, mocks)
        task = coordinator.current_media_task

        await coordinator.on_session_disconnected()

        assert coordinator.current_media_task is task
        await _flowing(media)

    async def test_reconnect_mid_turn_resumes_what_the_press_held(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        media = await _start(coordinator, mocks)
        await _press(coordinator)  # the question in flight when the session drops

        await coordinator.on_session_disconnected()

        assert coordinator.current_turn is None
        await _until(lambda: media.runs == 2)

    async def test_press_during_the_refresh_pauses_until_the_session_is_back(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """The planned refresh disconnects mid-book every 50 min; a press in
        the ~2-3 s before the reconnect stopped the book for good. It
        pauses it now, and the session coming back resumes it."""
        media = await _start(coordinator, mocks)
        provider._connected = False
        await coordinator.on_session_disconnected()

        await coordinator.on_ptt_rejected()

        assert coordinator.current_media_task is None, "the press pauses it"
        assert coordinator._content_obs is not None, "not stopped"
        mocks["send_audio_clear"].assert_awaited()
        assert not coordinator._speaking_state.is_speaking  # the walkie unholds
        await _stays(lambda: media.runs == 1, for_s=0.1)

        provider._connected = True
        await coordinator.on_session_connected()

        await _until(lambda: media.runs == 2)
        await _flowing(media)

    async def test_a_press_later_in_the_outage_stops_what_was_paused(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        media = await _start(coordinator, mocks)
        provider._connected = False
        await coordinator.on_session_disconnected()
        await coordinator.on_ptt_rejected()
        assert coordinator._content_obs is not None
        assert "session" in coordinator._content_obs.holds, "the first press pauses"
        _down_for(coordinator, 6.0)

        await coordinator.on_ptt_rejected()  # the outage is real

        assert coordinator._content_obs is None
        provider._connected = True
        await coordinator.on_session_connected()
        await _stays(lambda: media.runs == 1)

    async def test_reminder_during_the_refresh_waits_for_the_session(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """A timer firing in the reconnect gap was sent into no session: its
        turn never ended, kept the music parked until the next press, and
        queued every later inject behind it. It waits for the session now."""
        media = await _start(coordinator, mocks, patience=timedelta(minutes=30))
        provider._connected = False
        await coordinator.on_session_disconnected()
        provider.sent.clear()

        await coordinator.inject_turn("Timer: the pasta")

        assert coordinator.current_turn is None
        assert provider.sent == []
        await _flowing(media)  # the music plays on meanwhile

        provider._connected = True
        await coordinator.on_session_connected()

        assert ("send_conversation_message", "Timer: the pasta") in provider.sent
        assert coordinator.current_turn is not None
        await _until(lambda: coordinator.current_media_task is None)  # parked
        await _answer(coordinator)  # the narration ends
        await _until(lambda: media.runs == 2)

    async def test_reminder_and_press_in_the_gap_narrate_before_the_music(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """The session back fires the reminder first: the music the press
        paused must not start for a tick before the narration."""
        media = await _start(coordinator, mocks, patience=timedelta(minutes=30))
        provider._connected = False
        await coordinator.on_session_disconnected()
        await coordinator.on_ptt_rejected()
        await coordinator.inject_turn("Timer: the pasta")

        provider._connected = True
        await coordinator.on_session_connected()

        assert ("send_conversation_message", "Timer: the pasta") in provider.sent
        await _stays(lambda: media.runs == 1, for_s=0.1)
        await _answer(coordinator)
        await _until(lambda: media.runs == 2)

    async def test_inject_and_wait_in_the_gap_announces_once_the_session_is_back(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        """Telegram announces a caller, then bridges the call. Fired into no
        session, the wait never ended and the call was never bridged."""
        provider._connected = False
        await coordinator.on_session_disconnected()
        task = asyncio.create_task(coordinator.inject_turn_and_wait("Ana is calling"))
        await asyncio.sleep(0.05)
        assert coordinator.current_turn is None, "nothing fires into no session"

        provider._connected = True
        await coordinator.on_session_connected()

        await _until(lambda: ("send_conversation_message", "Ana is calling") in provider.sent)
        assert not task.done(), "returns after the announcement, not before"
        await _answer(coordinator)
        await asyncio.wait_for(task, timeout=1.0)

    async def test_inject_and_wait_queues_if_the_session_stays_down(
        self,
        coordinator: TurnCoordinator,
        provider: StubVoiceProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(TurnCoordinator, "_INJECT_SESSION_WAIT_S", 0.05)
        provider._connected = False
        await coordinator.on_session_disconnected()

        await asyncio.wait_for(coordinator.inject_turn_and_wait("Ana is calling"), timeout=1.0)

        assert coordinator.current_turn is None
        assert [r.prompt for r in coordinator._injected_queue] == ["Ana is calling"]

    async def test_reminder_after_a_server_close_waits_too(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        """OpenAI closed the session (expiry, a network drop): the provider
        still holds the dead socket and says it is connected. The
        coordinator goes by the drop it was told about."""
        await coordinator.on_session_disconnected()  # provider still "connected"

        await coordinator.inject_turn("Timer: the pasta")

        assert coordinator.current_turn is None
        assert len(coordinator._injected_queue) == 1
        await coordinator.on_session_connected()
        assert ("send_conversation_message", "Timer: the pasta") in provider.sent

    async def test_press_rejected_as_the_session_comes_back_leaves_the_music(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """The app rejected the press, then awaited a log line and a status
        while the connect completed. Nothing to wait for anymore: the
        music must not stop for good."""
        media = await _start(coordinator, mocks)
        await coordinator.on_session_disconnected()
        await coordinator.on_session_connected()

        await coordinator.on_ptt_rejected()

        assert coordinator.current_media_task is not None
        await _flowing(media)

    async def test_session_hold_released_even_if_the_queued_inject_fails(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A hold left behind would keep the music paused through every
        later press (theirs release, this one would not)."""
        await _start(coordinator, mocks, patience=timedelta(minutes=30))
        provider._connected = False
        await coordinator.on_session_disconnected()
        await coordinator.on_ptt_rejected()
        await coordinator.inject_turn("Timer: the pasta")
        provider._connected = True
        monkeypatch.setattr(
            provider, "send_conversation_message", AsyncMock(side_effect=ConnectionError)
        )

        with pytest.raises(ConnectionError):
            await coordinator.on_session_connected()

        obs = coordinator._content_obs
        assert obs is not None
        assert "session" not in obs.holds

    async def test_stream_ending_in_the_gap_leaves_no_turn_behind(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """A book's end prompt sent into no session left a completion turn
        that never ended: model_speaking stuck on, injects queued behind
        it until the next press."""
        end = asyncio.Event()

        async def gen() -> AsyncIterator[bytes]:
            yield b"\x00\x00" * 240
            await end.wait()

        stream = AudioStream(factory=gen, on_complete_prompt="The book ended.")
        await _turn(coordinator, mocks, stream)
        await _until(lambda: coordinator._speaking_state.is_speaking)
        provider._connected = False
        await coordinator.on_session_disconnected()

        end.set()  # the book ends during the reconnect

        await _until(
            lambda: coordinator._content_obs is not None and coordinator._content_obs.finished
        )
        assert coordinator.current_turn is None
        assert not coordinator._speaking_state.is_speaking
        assert ("send_conversation_message", "The book ended.") not in provider.sent


class TestStopWordsOverruleTheModel:
    """2026-10-12 morning, 08:09 and 08:11 local: "Pausa la música" was
    transcribed right, the model called music_control(resume), the paused
    stream was stopped by the stop words and the resume started the music
    again. What the user said wins over the model's media call."""

    @pytest.fixture(autouse=True)
    def _words(self, coordinator: TurnCoordinator) -> None:
        coordinator.set_ui_strings(
            {"content_stop_words": "para pausa", "content_filler_words": "la musica"}
        )

    async def _resume_turn(
        self, coord: TurnCoordinator, mocks: dict[str, Any], heard: str | None
    ) -> _Media:
        new = _Media()
        await coord.on_ptt_start()
        assert coord.current_turn is not None
        coord.current_turn.user_audio_bytes = 300 * 48
        await coord.on_ptt_stop()
        if heard is not None:
            coord.on_user_transcript(heard)
        mocks["dispatch_tool"].return_value = ToolResult(
            output='{"ok": true, "action": "resume"}', side_effect=AudioStream(factory=new.factory)
        )
        await coord.on_tool_call("c1", "music_control", {"action": "resume"})
        await coord.on_response_done()
        return new

    async def test_stop_words_drop_the_stream_the_model_started(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        old = await _start(coordinator, mocks)

        new = await self._resume_turn(coordinator, mocks, "Pausa la música")

        await _until(lambda: coordinator._content_obs is None)
        await _stays(lambda: new.runs == 0 and old.runs == 1)

    async def test_a_late_transcript_stops_the_stream_the_turn_started(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        old = await _start(coordinator, mocks)

        new = await self._resume_turn(coordinator, mocks, None)
        await _until(lambda: new.chunks > 0)  # it started before the words came
        coordinator.on_user_transcript("pausa la música")

        await _until(lambda: coordinator._content_obs is None)
        await _stays(lambda: old.runs == 1)

    async def test_other_words_keep_the_stream(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _start(coordinator, mocks)

        new = await self._resume_turn(coordinator, mocks, "Pon otra canción")

        await _until(lambda: new.chunks > 0)
        await _flowing(new)

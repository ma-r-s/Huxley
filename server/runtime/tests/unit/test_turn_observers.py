"""Unit tests for `huxley.turn.observers`.

Observers are tested in isolation against mock callbacks — no FocusManager,
no coordinator. The focus-transitions-drive-observer-calls contract is
tested end-to-end in `test_focus_manager.py`; here we test that the
observer's callbacks fire at the right moments and the pump task
lifecycle is clean.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from huxley.focus.vocabulary import FocusState, MixingBehavior
from huxley.turn.observers import ContentStreamObserver, DialogObserver
from huxley_sdk import AudioStream

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# --- DialogObserver ---


class TestDialogObserver:
    async def test_none_fires_on_stop_once(self) -> None:
        on_stop = AsyncMock()
        obs = DialogObserver(interface_name="turn.user.abc", on_stop=on_stop)
        await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)
        on_stop.assert_awaited_once()

    async def test_duplicate_none_is_idempotent(self) -> None:
        on_stop = AsyncMock()
        obs = DialogObserver(interface_name="turn.user.abc", on_stop=on_stop)
        await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)
        await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)
        on_stop.assert_awaited_once()

    async def test_foreground_is_noop(self) -> None:
        on_stop = AsyncMock()
        obs = DialogObserver(interface_name="turn.user.abc", on_stop=on_stop)
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        on_stop.assert_not_awaited()

    async def test_background_is_noop(self) -> None:
        on_stop = AsyncMock()
        obs = DialogObserver(interface_name="turn.user.abc", on_stop=on_stop)
        await obs.on_focus_changed(FocusState.BACKGROUND, MixingBehavior.MUST_PAUSE)
        on_stop.assert_not_awaited()

    async def test_foreground_then_none_fires_once(self) -> None:
        on_stop = AsyncMock()
        obs = DialogObserver(interface_name="turn.user.abc", on_stop=on_stop)
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)
        on_stop.assert_awaited_once()


# --- ContentStreamObserver ---


async def _finite_stream(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


async def _infinite_stream() -> AsyncIterator[bytes]:
    while True:
        yield b"chunk"
        await asyncio.sleep(0.01)


def _make_finite_audio_stream(chunks: list[bytes]) -> AudioStream:
    """Fresh AudioStream closing over `chunks` so factory() is reusable."""

    def factory() -> AsyncIterator[bytes]:
        return _finite_stream(list(chunks))

    return AudioStream(factory=factory)


def _make_infinite_audio_stream() -> AudioStream:
    return AudioStream(factory=_infinite_stream)


async def _wait_task_done(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        async with asyncio.timeout(1.0):
            await task


class TestContentStreamObserverPump:
    async def test_foreground_spawns_pump_task(self) -> None:
        send_audio = AsyncMock()
        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_finite_audio_stream([b"a", b"b"]),
            send_audio=send_audio,
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        assert obs.task is not None
        await _wait_task_done(obs.task)
        # Both chunks were forwarded.
        assert send_audio.await_count == 2

    async def test_duplicate_foreground_does_not_spawn_new_task(self) -> None:
        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_infinite_audio_stream(),
            send_audio=AsyncMock(),
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        first_task = obs.task
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        assert obs.task is first_task
        # Clean up.
        await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)


class TestContentStreamObserverCancellation:
    async def test_none_cancels_pump_task(self) -> None:
        send_audio = AsyncMock()
        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_infinite_audio_stream(),
            send_audio=send_audio,
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        await asyncio.sleep(0.02)
        await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)
        assert obs.task is None

    async def test_background_must_pause_cancels_pump(self) -> None:
        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_infinite_audio_stream(),
            send_audio=AsyncMock(),
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        await asyncio.sleep(0.02)
        await obs.on_focus_changed(FocusState.BACKGROUND, MixingBehavior.MUST_PAUSE)
        assert obs.task is None

    async def test_background_may_duck_keeps_pump_running(self) -> None:
        """Stage 1b: MAY_DUCK is true AVS ducking — pump stays alive,
        gain ramps down to `_DUCK_GAIN` over ~100ms. Contrast with
        MUST_PAUSE (above), which cancels the pump."""
        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_infinite_audio_stream(),
            send_audio=AsyncMock(),
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        await asyncio.sleep(0.02)
        await obs.on_focus_changed(FocusState.BACKGROUND, MixingBehavior.MAY_DUCK)

        # Pump task is still alive — duck is NOT a pause.
        assert obs.task is not None
        assert not obs.task.done()
        # A ramp is in flight toward the duck target.
        assert obs._ramp_target == pytest.approx(0.3)

        # Clean up.
        await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)


class TestContentStreamObserverEof:
    async def test_natural_eof_fires_on_eof_callback(self) -> None:
        on_eof = AsyncMock()
        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_finite_audio_stream([b"x"]),
            send_audio=AsyncMock(),
            on_eof=on_eof,
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        await _wait_task_done(obs.task)
        on_eof.assert_awaited_once()

    async def test_cancellation_does_not_fire_on_eof(self) -> None:
        on_eof = AsyncMock()
        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_infinite_audio_stream(),
            send_audio=AsyncMock(),
            on_eof=on_eof,
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        await asyncio.sleep(0.02)
        await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)
        on_eof.assert_not_awaited()

    async def test_natural_completion_fires_after_eof_then_none(self) -> None:
        on_completion = AsyncMock()
        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_finite_audio_stream([b"x"]),
            send_audio=AsyncMock(),
            on_natural_completion=on_completion,
        )
        # FG → pump runs to natural EOF (sets _natural_eof=True).
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        await _wait_task_done(obs.task)
        # NONE after EOF → completion callback fires.
        await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)
        on_completion.assert_awaited_once()

    async def test_natural_completion_not_fired_when_cancelled(self) -> None:
        on_completion = AsyncMock()
        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_infinite_audio_stream(),
            send_audio=AsyncMock(),
            on_natural_completion=on_completion,
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        await asyncio.sleep(0.02)
        # NONE arrives while pump is still mid-stream — cancellation path.
        await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)
        on_completion.assert_not_awaited()


class TestContentStreamObserverDuckEnvelope:
    """Stage 1b: linear gain ramp applied to outgoing PCM on focus
    transitions. MAY_DUCK ramps to 0.3; return to PRIMARY ramps back to
    1.0. MUST_PAUSE still cancels (no fade-on-pause in this stage).
    """

    @staticmethod
    def _loud_chunk(n_samples: int = 480, amplitude: int = 20000) -> bytes:
        """Build a PCM16-LE chunk of `n_samples` int16s at `amplitude`.
        480 samples = 20ms at 24kHz — realistic chunk size."""
        return struct.pack(f"<{n_samples}h", *([amplitude] * n_samples))

    @staticmethod
    def _unpack_max(chunk: bytes) -> int:
        """Max absolute sample value in a PCM16-LE chunk."""
        n = len(chunk) // 2
        samples = struct.unpack(f"<{n}h", chunk)
        return max(abs(s) for s in samples)

    async def test_unducked_chunk_passes_through_unchanged(self) -> None:
        """Fast path: gain=1.0 + no ramp → chunk bytes identical to input."""
        sent: list[bytes] = []

        async def _send(chunk: bytes) -> None:
            sent.append(chunk)

        chunks = [self._loud_chunk()]
        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_finite_audio_stream(chunks),
            send_audio=_send,
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        await _wait_task_done(obs.task)

        assert sent == chunks  # byte-identical — no allocation, no math

    @staticmethod
    def _pcm_infinite_stream(amplitude: int = 20000) -> AudioStream:
        """An infinite PCM16-LE stream that yields valid chunks.

        Needed for duck tests because the generic
        `_make_infinite_audio_stream` yields `b"chunk"` (5 bytes, not
        PCM-aligned) which crashes the gain-scaling path. In production
        the pump never sees non-PCM.
        """

        async def factory() -> AsyncIterator[bytes]:
            chunk = struct.pack("<480h", *([amplitude] * 480))  # 20ms @ 24kHz
            while True:
                yield chunk
                await asyncio.sleep(0.01)

        return AudioStream(factory=factory)

    async def test_duck_gain_attenuates_samples_roughly_to_target(self) -> None:
        """After MAY_DUCK + ramp settles, chunks are attenuated to ≈0.3x."""
        sent: list[bytes] = []

        async def _send(chunk: bytes) -> None:
            sent.append(chunk)

        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=self._pcm_infinite_stream(amplitude=20000),
            send_audio=_send,
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        await asyncio.sleep(0.02)  # few unducked chunks flow
        await obs.on_focus_changed(FocusState.BACKGROUND, MixingBehavior.MAY_DUCK)
        await asyncio.sleep(0.15)  # past ramp duration — gain settled at 0.3
        sent_count_at_settled = len(sent)
        await asyncio.sleep(0.05)  # let a few settled-gain chunks flow
        await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)

        assert len(sent) > sent_count_at_settled
        settled_chunk = sent[sent_count_at_settled]
        # Max sample in the scaled chunk should be close to 20000 * 0.3 = 6000.
        # Tolerance accounts for int truncation in the scaling loop.
        assert self._unpack_max(settled_chunk) == pytest.approx(int(20000 * 0.3), abs=50)
        assert obs._gain == pytest.approx(0.3)
        assert obs._ramp_target is None  # ramp settled

    async def test_duck_then_resume_rearms_gain_back_to_one(self) -> None:
        """MAY_DUCK → PRIMARY ramps gain back up to 1.0."""
        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=self._pcm_infinite_stream(),
            send_audio=AsyncMock(),
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        await asyncio.sleep(0.02)
        await obs.on_focus_changed(FocusState.BACKGROUND, MixingBehavior.MAY_DUCK)
        await asyncio.sleep(0.15)  # past ramp — settled at 0.3
        assert obs._gain == pytest.approx(0.3)

        # Return to FOREGROUND — ramp target flips to 1.0.
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        assert obs._ramp_target == pytest.approx(1.0)
        await asyncio.sleep(0.15)  # past ramp
        # Let a chunk flow through `_apply_gain` — triggers the
        # ramp-completion path that commits `_gain = target`.
        await asyncio.sleep(0.05)
        assert obs._gain == pytest.approx(1.0)

        await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)

    async def test_must_pause_still_cancels_pump_unchanged(self) -> None:
        """MUST_PAUSE (NONMIXABLE content path) is unchanged — hard cancel,
        no fade. Duck envelope is for MAY_DUCK only."""
        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_infinite_audio_stream(),
            send_audio=AsyncMock(),
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        await asyncio.sleep(0.02)
        await obs.on_focus_changed(FocusState.BACKGROUND, MixingBehavior.MUST_PAUSE)
        assert obs.task is None  # cancelled, not ducked


class TestContentStreamObserverSelfCancel:
    """Reentrance safety: NONE delivered back to this observer from within
    its own `on_eof` callback must be handled cleanly.

    Scenario: an observer callback chain delivers NONE to the same
    observer while still executing inside `_pump` (e.g. `on_eof` fires
    and the handler, directly or indirectly, re-enters
    `on_focus_changed(NONE)`). The `_cancel_pump` self-cancel guard
    detects `task is current_task()` and skips the cancel-await dance.

    This isn't a deadlock scenario — Python would raise CancelledError
    at `await task` (the cancel flag is consumed at the first await,
    not RuntimeError as one might assume) and `contextlib.suppress`
    catches it. But without the guard, `task.cancel()` is called on a
    task finishing naturally, leaving it in a transient "cancelling"
    state that could interact oddly with `asyncio.shield` or other
    cancel-aware code. The guard keeps the path clean.
    """

    async def test_reentrant_none_from_on_eof_completes_cleanly(self) -> None:
        """NONE delivered back to the observer from inside `on_eof`
        must complete in bounded time with `on_natural_completion` still
        firing — i.e., the reentrant path behaves identically to the
        normal natural-end path.
        """
        on_completion = AsyncMock()
        obs: ContentStreamObserver | None = None

        async def reentrant_on_eof() -> None:
            # Simulate the Stage-2 path: `on_eof` routes back through NONE.
            # Without the self-cancel guard this deadlocks.
            assert obs is not None
            await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)

        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_finite_audio_stream([b"x"]),
            send_audio=AsyncMock(),
            on_eof=reentrant_on_eof,
            on_natural_completion=on_completion,
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        # Bounded-time guarantee: if the guard is missing, this hangs forever.
        async with asyncio.timeout(1.0):
            await _wait_task_done(obs.task)
        on_completion.assert_awaited_once()
        assert obs.task is None

    async def test_reentrant_none_clears_task_slot(self) -> None:
        """Even without on_natural_completion, the reentrant path must
        clear `_task` so a subsequent spawn can proceed."""
        obs: ContentStreamObserver | None = None

        async def reentrant_on_eof() -> None:
            assert obs is not None
            await obs.on_focus_changed(FocusState.NONE, MixingBehavior.MUST_STOP)

        obs = ContentStreamObserver(
            interface_name="skill.audiobooks",
            stream=_make_finite_audio_stream([b"x"]),
            send_audio=AsyncMock(),
            on_eof=reentrant_on_eof,
        )
        await obs.on_focus_changed(FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        async with asyncio.timeout(1.0):
            await _wait_task_done(obs.task)
        assert obs.task is None

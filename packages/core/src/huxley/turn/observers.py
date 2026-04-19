"""Turn observers — bridge between FocusManager transitions and coord behavior.

Observers are thin adapters. The FocusManager tells them "your focus
changed"; they translate that into coordinator-side actions via
callbacks passed at construction time. Keeping observers in their own
module (rather than inline in `coordinator.py`) lets them be unit-tested
in isolation against mock callbacks, and it keeps `coordinator.py`
focused on orchestration.

See `docs/architecture.md#focus-management` for how the observers fit
into the wider system.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
import time
from typing import TYPE_CHECKING

import structlog

from huxley.focus.vocabulary import FocusState, MixingBehavior

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from huxley_sdk import AudioStream

logger = structlog.get_logger()

# --- Duck envelope constants (T1.4 Stage 1b) ---

# Target gain when an Activity is MAY_DUCK'd. -10 dB ≈ 0.316, rounded for
# human-recognizable "much quieter but still there." Future skill authors
# that want stream-specific duck depths can grow AudioStream with a
# `duck_gain` field; today one value for every MIXABLE stream is fine.
_DUCK_GAIN: float = 0.3

# Duration of a gain transition (duck-down, duck-up). 100ms is the sweet
# spot: long enough to avoid audible clicks at chunk boundaries, short
# enough that the transition doesn't feel sluggish. Shorter ramps (<30ms)
# click; longer ramps (>200ms) make the overlay voice start before the
# content has attenuated enough, which feels muddy.
_RAMP_DURATION_S: float = 0.1

# Clamp bounds for PCM16 scaled samples.
_PCM16_MAX: int = 32767
_PCM16_MIN: int = -32768


class DialogObserver:
    """Observer for a DIALOG-channel Activity (user / completion / injected turn).

    Fires `on_stop` once, when the Activity receives `NONE` focus
    (interrupted, stopped by the coordinator, or replaced via same-
    interface acquire). FOREGROUND and BACKGROUND are no-ops —
    `send_model_speaking` is driven by actual LLM audio events in the
    coordinator (`on_audio_delta` / `on_audio_done`), not by focus.

    Idempotent: a second `NONE` delivery (shouldn't happen by construction
    but defensive) is ignored.
    """

    def __init__(
        self,
        *,
        interface_name: str,
        on_stop: Callable[[], Awaitable[None]],
    ) -> None:
        self._interface_name = interface_name
        self._on_stop = on_stop
        self._stopped = False

    async def on_focus_changed(self, new_focus: FocusState, behavior: MixingBehavior) -> None:
        _ = behavior  # FOREGROUND/BACKGROUND no-op; see class docstring
        if new_focus is FocusState.NONE and not self._stopped:
            self._stopped = True
            await self._on_stop()


class ContentStreamObserver:
    """Observer for a CONTENT-channel `AudioStream` side effect.

    Owns the pump task that iterates the `AudioStream.factory()` async
    iterator and forwards PCM chunks to `send_audio`. Applies a linear
    gain envelope to outgoing audio so focus transitions produce smooth
    volume changes instead of clicks.

    Lifecycle:

    - `FOREGROUND / PRIMARY` → spawn the pump task (if idle). If the
      pump was already running with a lowered gain (from a prior
      `MAY_DUCK`), ramp back up to 1.0 over `_RAMP_DURATION_S`.
      Duplicate FOREGROUND is a no-op (still the same owner).
    - `BACKGROUND / MAY_DUCK` → **keep the pump running** but start a
      gain ramp down to `_DUCK_GAIN` (≈0.3) over `_RAMP_DURATION_S`.
      This is the AVS duck semantics: MIXABLE content dips under an
      overlaying voice without stopping.
    - `BACKGROUND / MUST_PAUSE` → cancel the pump task. NONMIXABLE
      content (spoken word) takes this path because overlaying two
      voices is worse than a pause. A future "fade-out on pause"
      improvement could use the same gain primitive to smooth the
      transition, but today it's a hard cancel.
    - `NONE / MUST_STOP` → cancel the pump task. If the task had
      already reached natural EOF before the NONE delivery, fire
      `on_natural_completion` (used by audiobook completion turns).

    The pump calls `on_eof` when it finishes naturally — the
    coordinator wires this to `focus_manager.release(channel,
    interface_name)` so a NONE delivery follows and the observer can
    distinguish "cancelled" from "completed."
    """

    def __init__(
        self,
        *,
        interface_name: str,
        stream: AudioStream,
        send_audio: Callable[[bytes], Awaitable[None]],
        on_eof: Callable[[], Awaitable[None]] | None = None,
        on_natural_completion: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._interface_name = interface_name
        self._stream = stream
        self._send_audio = send_audio
        self._on_eof = on_eof
        self._on_natural_completion = on_natural_completion
        self._task: asyncio.Task[None] | None = None
        self._natural_eof = False

        # --- Gain envelope state (T1.4 Stage 1b — duck) ---
        # Steady-state gain when no ramp is active. Changes settle here
        # once a ramp completes. Defaults to 1.0 (full volume).
        self._gain: float = 1.0
        # If a ramp is in flight, its target gain. `None` means no ramp
        # active and `_gain` is authoritative.
        self._ramp_target: float | None = None
        # Wall-clock start time of the current ramp (monotonic seconds).
        self._ramp_start_time: float = 0.0
        # Gain at the moment the ramp started — ramp interpolates from
        # here to `_ramp_target` over `_RAMP_DURATION_S`.
        self._ramp_start_gain: float = 1.0

    @property
    def task(self) -> asyncio.Task[None] | None:
        """The pump task. Exposed for tests + for the coord to await shutdown."""
        return self._task

    @property
    def interface_name(self) -> str:
        """The stable `interface_name` this observer was constructed with."""
        return self._interface_name

    async def on_focus_changed(self, new_focus: FocusState, behavior: MixingBehavior) -> None:
        match new_focus:
            case FocusState.FOREGROUND:
                # If we were ducked (MAY_DUCK) and come back to FG, ramp
                # gain back up to 1.0. The spawn-if-idle path handles the
                # first-time FG (pump not yet started).
                self._start_ramp(target=1.0)
                await self._spawn_pump_if_idle()
            case FocusState.BACKGROUND:
                if behavior is MixingBehavior.MAY_DUCK:
                    # True duck: keep the pump running, lower the gain.
                    await logger.ainfo(
                        "focus.duck_started",
                        interface=self._interface_name,
                        target_gain=_DUCK_GAIN,
                        duration_ms=int(_RAMP_DURATION_S * 1000),
                    )
                    self._start_ramp(target=_DUCK_GAIN)
                    # Pump continues — do NOT cancel.
                else:
                    # MUST_PAUSE (NONMIXABLE content) — cancel.
                    await self._cancel_pump()
            case FocusState.NONE:
                await self._cancel_pump()
                if self._natural_eof and self._on_natural_completion is not None:
                    await self._on_natural_completion()

    def _start_ramp(self, *, target: float) -> None:
        """Begin a linear gain ramp from the current effective gain to
        `target` over `_RAMP_DURATION_S`. Idempotent — starting a new
        ramp while one is in flight re-anchors from the current gain.
        No-op when the effective gain already matches `target` (no
        pointless per-sample interpolation on every chunk).
        """
        now = time.monotonic()
        current = self._current_gain(now)
        if self._ramp_target is None and current == target:
            # Already at target with no ramp in flight — nothing to do.
            return
        self._ramp_start_gain = current
        self._ramp_target = target
        self._ramp_start_time = now

    def _current_gain(self, now: float) -> float:
        """Return the effective gain at wall-clock time `now`. Clamps ramp
        state when the duration has elapsed (commits the target to
        steady-state)."""
        if self._ramp_target is None:
            return self._gain
        elapsed = now - self._ramp_start_time
        if elapsed >= _RAMP_DURATION_S:
            self._gain = self._ramp_target
            self._ramp_target = None
            return self._gain
        # Linear interpolation across the ramp window.
        progress = elapsed / _RAMP_DURATION_S
        return self._ramp_start_gain + (self._ramp_target - self._ramp_start_gain) * progress

    def _apply_gain(self, chunk: bytes) -> bytes:
        """Scale PCM16-LE `chunk` by the current gain envelope.

        Fast path: if gain is 1.0 AND no ramp is active, return the
        input untouched (no allocation, no arithmetic). Common case
        for unducked playback.

        Ramping path: per-sample linear interpolation from gain at
        chunk-start to gain at chunk-end. Avoids the click that a
        per-chunk constant gain produces at the ramp's boundaries.
        """
        now = time.monotonic()
        if self._ramp_target is None and self._gain == 1.0:
            return chunk

        # We need gain at chunk-start AND chunk-end. For a chunk of N
        # samples at 24kHz, duration is N / 24000 s. Interpolating
        # across the chunk matters because the ramp is 100ms and chunks
        # can be ~40ms — so two or three chunks straddle the ramp.
        n_samples = len(chunk) // 2
        chunk_duration_s = n_samples / 24_000.0
        gain_start = self._current_gain(now)
        gain_end = self._current_gain(now + chunk_duration_s)

        if gain_start == 1.0 and gain_end == 1.0:
            return chunk

        samples = struct.unpack(f"<{n_samples}h", chunk)
        if gain_start == gain_end:
            # Steady gain across this chunk (ramp already settled or not
            # yet started for this chunk's window) — single multiply.
            g = gain_start
            scaled = [max(_PCM16_MIN, min(_PCM16_MAX, int(s * g))) for s in samples]
        else:
            slope = (gain_end - gain_start) / n_samples
            scaled = [
                max(_PCM16_MIN, min(_PCM16_MAX, int(s * (gain_start + slope * i))))
                for i, s in enumerate(samples)
            ]
        return struct.pack(f"<{n_samples}h", *scaled)

    async def _spawn_pump_if_idle(self) -> None:
        if self._task is not None and not self._task.done():
            return  # already pumping — duplicate FOREGROUND
        self._natural_eof = False
        self._task = asyncio.create_task(
            self._pump(),
            name=f"content_pump:{self._interface_name}",
        )

    async def _pump(self) -> None:
        try:
            async for chunk in self._stream.factory():
                # Apply the gain envelope before forwarding. When no ramp
                # is active and gain is 1.0, this is a no-op (fast path).
                await self._send_audio(self._apply_gain(chunk))
            self._natural_eof = True
            if self._on_eof is not None:
                await self._on_eof()
        except asyncio.CancelledError:
            raise
        except Exception:
            await logger.aexception(
                "content_stream_pump_failed",
                interface=self._interface_name,
            )

    async def _cancel_pump(self) -> None:
        task = self._task
        if task is not None and not task.done():
            # Self-cancel guard — Stage-2 reentrance safety. Today's
            # coordinator never routes `on_eof` back through NONE
            # synchronously (the FocusManager's mailbox serializes,
            # so the NONE arrives on a later tick with the pump
            # already completed). But Stage 2's `InputClaim` adds
            # new reentrance paths: a stream's natural end might
            # trigger a claim-end that cascades into an observer-
            # NONE delivery on the same tick as the pump's finally.
            # Without this guard, `task.cancel()` + `await task` in
            # that scenario would call cancel on a naturally-
            # finishing task, leaving it in a transient cancelling
            # state that interacts oddly with `asyncio.shield` and
            # other cancel-aware constructs. Skip the cancel-await
            # dance; the task is exiting on its own.
            if task is asyncio.current_task():
                self._task = None
                return
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None

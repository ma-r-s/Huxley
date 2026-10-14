"""A soft waiting sound while the user waits on the server (T1.41).

The user let go of the button, or a tool is running, and nothing is
playing. Silence then reads as "it's broken" ("it kept silent for like 10
seconds... it feels like it's not doing anything", Mario 2026-10-12). Once
the client has played everything it was sent and stayed quiet a while, one
soft struck note repeats until real sound comes.

Not the walkie's offline cue (a falling pair at 440 / 330 Hz meaning
"can't reach the server"): waiting must sound like "working", not trouble.

Stopping never cancels a send: the walkie's Opus queue is shared, and a
send cancelled mid-drain clears it, taking the first packets of the answer
with it (critic, 2026-10-12). A blip already on its way finishes; the loop
just doesn't send another.
"""

from __future__ import annotations

import asyncio
import functools
import math
import struct
import time
from typing import TYPE_CHECKING

import structlog

from huxley.loudness import db_to_gain, momentary_max_lufs

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Release to the model's first audio, measured on 233 turns (2026-10-12):
# p50 1.06 s, p90 2.02 s; at 1.5 s one ordinary answer in nine would get a
# blip right before the speech.
WAIT_REPLY_S = 2.5
# A tool's wait, once the client has played what it had (the model's "un
# momento").
WAIT_TOOL_S = 1.5
WAIT_CUE_PERIOD_S = 1.5
# Never "working" forever: a model that never answers isn't a wait.
WAIT_CUE_CAP_S = 30.0
_SAMPLE_RATE = 24_000
# Two 60 ms Opus frames: a partial frame would wait 250 ms for the idle
# flush and reach the walkie split in two (a click and a false underrun).
BLIP_MS = 120


# The blip's loudness, its loudest 400 ms (T1.47): 7 dB under the one-off
# cues (`huxley.loudness.CUE_TARGET_LUFS`, -19), about 12 dB under the
# voice's loudest moments. It repeats while the user waits, so it sits
# under everything else; this keeps the place T1.41 tuned it to (peak -16
# dBFS under a voice then at -27 LUFS) now that the voice plays at -20.
BLIP_TARGET_LUFS = -26.0


@functools.cache
def render_blip(hz: float = 587.33, target_lufs: float = BLIP_TARGET_LUFS) -> bytes:
    """A soft struck note (D5 with two quiet partials, a 4 ms attack and a
    fast decay), PCM16 mono 24 kHz, `BLIP_MS` long, at `target_lufs`
    momentary max. A small speaker plays it; it is clearly not speech and
    not the offline cue's pitch. Cached: every coordinator plays the same
    one."""
    n = _SAMPLE_RATE * BLIP_MS // 1000
    attack = _SAMPLE_RATE * 4 // 1000
    partials = ((1.0, 1.0), (2.0, 0.25), (3.0, 0.08))
    norm = sum(a for _, a in partials)
    raw: list[float] = []
    for i in range(n):
        t = i / _SAMPLE_RATE
        env = (i / attack if i < attack else 1.0) * math.exp(-t / 0.035)
        tone = sum(a * math.sin(2 * math.pi * hz * k * t) for k, a in partials) / norm
        raw.append(env * tone)
    raw[-1] = 0.0
    unit = struct.pack(f"<{n}h", *(int(x * 16384) for x in raw))
    scale = 16384 * db_to_gain(target_lufs - momentary_max_lufs(unit))
    return struct.pack(f"<{n}h", *(int(x * scale) for x in raw))


class WaitCue:
    """Armed while the user waits; plays only once the client is quiet.

    `quiet_in()` says how many seconds of already-sent audio the client
    still has to play (audio goes out faster than real time, so "nothing
    sent" is not "nothing heard"). `disarm` is synchronous and safe from any
    coroutine; real sound must never wait on the cue.
    """

    def __init__(
        self,
        send_audio: Callable[[bytes], Awaitable[None]],
        quiet_in: Callable[[], float],
        *,
        blip: bytes | None = None,
        period_s: float = WAIT_CUE_PERIOD_S,
        cap_s: float = WAIT_CUE_CAP_S,
    ) -> None:
        self._send_audio = send_audio
        self._quiet_in = quiet_in
        self._blip = blip if blip is not None else render_blip()
        self._period_s = period_s
        self._cap_s = cap_s
        self._log: structlog.stdlib.BoundLogger = structlog.get_logger()
        self._task: asyncio.Task[None] | None = None
        self._generation = 0
        self._sending = False
        self._reason = ""
        self._armed_at = 0.0
        self._blips = 0
        self._logs: set[asyncio.Task[None]] = set()

    @property
    def armed(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def blips(self) -> int:
        return self._blips

    def arm(
        self, reason: str, after_s: float, log: structlog.stdlib.BoundLogger | None = None
    ) -> None:
        """Start waiting: the first blip comes once the client is quiet and
        `after_s` more has passed. `log` carries the turn's bindings."""
        self.disarm("rearmed", quiet=True)
        if log is not None:
            self._log = log
        self._reason = reason
        self._armed_at = time.monotonic()
        self._blips = 0
        self._task = asyncio.create_task(self._run(self._generation, after_s), name="wait_cue")

    def disarm(self, reason: str, *, quiet: bool = False) -> None:
        """Stop. No blip is sent after this returns, except one already
        being written, which finishes (cancelling it would clear the
        client's shared audio queue)."""
        self._generation += 1
        task, self._task = self._task, None
        if task is None or task.done():
            return
        if not self._sending:
            task.cancel()
        if self._blips and not quiet:
            self._emit("coord.wait_cue_stop", reason=reason)

    async def _run(self, generation: int, after_s: float) -> None:
        playhead_ms = int(max(0.0, self._quiet_in()) * 1000)
        # New audio meanwhile disarms the cue, so one computed wait does.
        await asyncio.sleep(playhead_ms / 1000 + after_s)
        if generation != self._generation:
            return
        self._emit(
            "coord.wait_cue_start",
            reason=self._reason,
            playhead_remaining_ms=playhead_ms,
            waited_ms=int((time.monotonic() - self._armed_at) * 1000),
        )
        started = time.monotonic()
        while generation == self._generation:
            if time.monotonic() - started >= self._cap_s:
                self._generation += 1
                self._emit("coord.wait_cue_stop", reason="cap")
                return
            self._sending = True
            try:
                await self._send_audio(self._blip)
            finally:
                self._sending = False
            self._blips += 1
            if generation != self._generation:
                return
            await asyncio.sleep(self._period_s)

    def _emit(self, event: str, **fields: object) -> None:
        """Log without awaiting: disarm runs on the audio path."""
        task = asyncio.get_running_loop().create_task(
            self._log.ainfo(
                event,
                blips=self._blips,
                cue_ms=int((time.monotonic() - self._armed_at) * 1000),
                **fields,
            )
        )
        self._logs.add(task)
        task.add_done_callback(self._logs.discard)

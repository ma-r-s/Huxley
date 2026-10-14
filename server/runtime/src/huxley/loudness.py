"""Loudness: measure it, and keep everything the client plays at one level (T1.47).

The standard (docs/sounds.md "Loudness"):

- Program audio (the voice, spoken clips, content streams): -20 LUFS
  integrated (`PROGRAM_TARGET_LUFS`).
- Cues (earcons, tones): -19 LUFS momentary max, their loudest 400 ms
  (`CUE_TARGET_LUFS`). That lands a chime about 3 dB under the voice
  (about -23 LUFS integrated). A chime is heard at its strike, and its
  integrated value depends on how long its reverb tail is; its loudest
  moment does not, and is defined for a clip of any length.
- Nothing on the wire above `WIRE_CEILING_DBFS` (sample peak; streams
  `STREAM_CEILING_DBFS`), which keeps the walkie's decoded Opus under -1.5
  dBFS true peak.

Measurement follows ITU-R BS.1770: K-weighting, 400 ms blocks, a -70 LUFS
absolute gate and a -10 LU relative gate. The filters are computed for the
wire's 24 kHz with libebur128's formulas.

The gain stages (where each lives and why: docs/sounds.md "Loudness"):

- the voice: a fixed gain with a `PeakLimiter`, in the turn coordinator,
  on model audio only;
- content streams: a `StreamLeveler` per stream that opts in
  (`AudioStream.loudness_key`), in the content observer, before the duck;
- the wire: one `PeakLimiter` in `AudioServer.send_audio`, the ceiling for
  everything (call audio, a third-party sound) whatever its source.

Pure Python on purpose: the production box has no numpy. Measured there
(2026-10-13, Python 3.13, one stream): K-weighting 8.4 ms per second of
audio, gain plus limiter about 7 ms; under 1 % of a core each.
"""

from __future__ import annotations

import math
import sys
from array import array
from collections import deque
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

logger = structlog.get_logger()

SAMPLE_RATE = 24_000
PROGRAM_TARGET_LUFS = -20.0
CUE_TARGET_LUFS = -19.0
# Assets (earcons, clips) are checked offline against this true peak.
TRUE_PEAK_CEILING_DBFS = -1.5
# The live limiters work on sample peaks. The margin under the -1.5 dBTP
# target is for the walkie's 32 kbit/s Opus, which adds peak of its own:
# through the server's encoder and decoder (T1.47), speech limited at -3
# dBFS decodes to at most -2.6 dBTP. Dense broadband audio (radio, music)
# gains up to 1.8 dB, even unlimited, hence a lower ceiling for streams:
# limited at -3.5 it decodes to at most -1.7 dBTP.
WIRE_CEILING_DBFS = -3.0
STREAM_CEILING_DBFS = -3.5

_FULL_SCALE_SQ = 32768.0 * 32768.0
_PCM16_MAX = 32767
_SWAP = sys.byteorder != "little"  # the wire is PCM16 little-endian


def lufs(mean_square: float) -> float:
    """Loudness of a K-weighted mean square in PCM16 units squared."""
    if mean_square <= 0.0:
        return -math.inf
    return -0.691 + 10.0 * math.log10(mean_square / _FULL_SCALE_SQ)


def db_to_gain(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def gain_to_db(gain: float) -> float:
    return 20.0 * math.log10(gain) if gain > 0.0 else -math.inf


def pcm_samples(pcm: bytes) -> array[int]:
    """PCM16 little-endian bytes as an array of ints. A trailing odd byte
    is ignored."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if _SWAP:
        samples.byteswap()
    return samples


def pcm_bytes(samples: array[int]) -> bytes:
    if _SWAP:
        samples = array("h", samples)
        samples.byteswap()
    return samples.tobytes()


def _k_coefficients(rate: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """BS.1770's two K-weighting stages (a high shelf, then the RLB high
    pass) for `rate`, as (b0, b1, b2, a1, a2) each. libebur128's formulas:
    at 48 kHz they reproduce the coefficients printed in BS.1770."""
    f0 = 1681.974450955533
    gain_db = 3.999843853973347
    q = 0.7071752369554196
    k = math.tan(math.pi * f0 / rate)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh**0.4996667741545416
    a0 = 1.0 + k / q + k * k
    shelf = (
        (vh + vb * k / q + k * k) / a0,
        2.0 * (k * k - vh) / a0,
        (vh - vb * k / q + k * k) / a0,
        2.0 * (k * k - 1.0) / a0,
        (1.0 - k / q + k * k) / a0,
    )
    f0 = 38.13547087602444
    q = 0.5003270373238773
    k = math.tan(math.pi * f0 / rate)
    a0 = 1.0 + k / q + k * k
    high_pass = (1.0, -2.0, 1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0)
    return shelf, high_pass


class KWeighting:
    """The K-weighting filter with its state, so consecutive calls filter
    one continuous signal."""

    __slots__ = ("_high_pass", "_shelf", "_state")

    def __init__(self, rate: int = SAMPLE_RATE) -> None:
        self._shelf, self._high_pass = _k_coefficients(rate)
        self._state = (0.0, 0.0, 0.0, 0.0)

    def square_sum(self, samples: Iterable[int]) -> float:
        """Filter `samples` and return the sum of the filtered squares."""
        b0, b1, b2, a1, a2 = self._shelf
        _, _, _, c1, c2 = self._high_pass
        z1, z2, w1, w2 = self._state
        acc = 0.0
        # Transposed direct form II, the high pass's (1, -2, 1) inlined.
        for x in samples:
            y = b0 * x + z1
            z1 = b1 * x - a1 * y + z2
            z2 = b2 * x - a2 * y
            u = y + w1
            w1 = -2.0 * y - c1 * u + w2
            w2 = y - c2 * u
            acc += u * u
        self._state = (z1, z2, w1, w2)
        return acc


def _block_mean_squares(samples: array[int], block: int) -> list[float]:
    """K-weighted mean square of each whole `block` of samples."""
    kw = KWeighting()
    return [
        kw.square_sum(samples[i : i + block]) / block
        for i in range(0, len(samples) - block + 1, block)
    ]


def integrated_lufs(pcm: bytes) -> float:
    """Gated integrated loudness (BS.1770) of PCM16 24 kHz mono. -inf for
    a clip shorter than one 400 ms block, or silent."""
    sub = _block_mean_squares(pcm_samples(pcm), SAMPLE_RATE // 10)
    blocks = [sum(sub[i : i + 4]) / 4.0 for i in range(len(sub) - 3)]
    loud = [b for b in blocks if lufs(b) > -70.0]
    if not loud:
        return -math.inf
    relative = lufs(sum(loud) / len(loud)) - 10.0
    kept = [b for b in loud if lufs(b) > relative]
    return lufs(sum(kept) / len(kept))


def momentary_max_lufs(pcm: bytes) -> float:
    """Loudness of the loudest 400 ms (10 ms resolution). A clip shorter
    than 400 ms is measured as if padded with silence to 400 ms."""
    step = SAMPLE_RATE // 100
    pad = array("h", bytes(2 * 4 * SAMPLE_RATE // 10))
    samples = pad + pcm_samples(pcm) + pad
    sub = _block_mean_squares(samples, step)
    window = 40
    best = max(
        (sum(sub[i : i + window]) for i in range(len(sub) - window + 1)),
        default=0.0,
    )
    return lufs(best / window)


def sample_peak_dbfs(pcm: bytes) -> float:
    samples = pcm_samples(pcm)
    if not samples:
        return -math.inf
    return gain_to_db(max(max(samples), -min(samples)) / 32768.0)


def true_peak_dbfs(pcm: bytes, oversample: int = 8, taps: int = 16) -> float:
    """Estimated true peak: Hann-windowed sinc interpolation at
    `oversample` x, evaluated between samples where a neighbour is within
    6 dB of the sample peak (an inter-sample over needs loud neighbours).
    For offline checks of assets; too slow for the live path."""
    samples = pcm_samples(pcm)
    if not samples:
        return -math.inf
    peak = max(max(samples), -min(samples))
    if peak == 0:
        return -math.inf
    kernels: list[list[float]] = []
    for p in range(1, oversample):
        frac = p / oversample
        kernel = []
        for k in range(-taps + 1, taps + 1):
            t = k - frac
            sinc = math.sin(math.pi * t) / (math.pi * t)
            window = 0.5 + 0.5 * math.cos(math.pi * t / taps)
            kernel.append(sinc * window)
        kernels.append(kernel)
    best = float(peak)
    n = len(samples)
    threshold = peak / 2
    for i in range(n - 1):
        if abs(samples[i]) < threshold and abs(samples[i + 1]) < threshold:
            continue
        lo = i - taps + 1
        neighbourhood = [samples[j] if 0 <= j < n else 0 for j in range(lo, lo + 2 * taps)]
        for kernel in kernels:
            value = abs(sum(c * x for c, x in zip(kernel, neighbourhood, strict=True)))
            best = max(best, value)
    return gain_to_db(best / 32768.0)


def scale_pcm(pcm: bytes, gain_db: float) -> bytes:
    """`pcm` times a fixed gain, rounded, clamped to PCM16. For assets."""
    g = db_to_gain(gain_db)
    samples = pcm_samples(pcm)
    out = array("h", [max(-32768, min(_PCM16_MAX, round(x * g))) for x in samples])
    return pcm_bytes(out)


# --- The live gain stages ---------------------------------------------------

_LIMIT_BLOCK = SAMPLE_RATE // 1000  # 1 ms
# A release this close to unity (-0.01 dB) is done: back to the fast path.
_RELEASED = 0.999


class PeakLimiter:
    """A gain (fixed, or ramped across a chunk) and a peak limiter, applied
    to PCM16 chunks with no added latency.

    The chunk in hand is its own lookahead: per 1 ms block, the gain that
    keeps the block's peak at the ceiling; the envelope at each block
    boundary is the lower of that block's and the next one's, so the
    linear ramp across a block never lets a sample over. After a cut the
    envelope holds `hold_ms`, then recovers with `release_ms` (a release
    with no hold ripples at the voice's pitch period, heard as
    distortion). The one thing the chunk can't see is a peak in its own
    first millisecond while the previous chunk ended unlimited: that block
    ramps down instead of stepping (a step clicks), and the few samples it
    lets over are clamped to the ceiling.
    """

    __slots__ = (
        "_ceiling",
        "_env",
        "_hold",
        "_hold_left",
        "_hold_level",
        "_release",
        "limited_blocks",
        "min_gain",
    )

    def __init__(
        self,
        ceiling_dbfs: float = WIRE_CEILING_DBFS,
        *,
        hold_ms: float = 15.0,
        release_ms: float = 150.0,
    ) -> None:
        self._ceiling = int(_PCM16_MAX * db_to_gain(ceiling_dbfs))
        self._hold = round(hold_ms)  # in 1 ms blocks
        self._release = 1.0 - math.exp(-1.0 / release_ms)  # per 1 ms block
        self._env = 1.0
        self._hold_level = 1.0
        self._hold_left = 0
        # Since the last `take_stats`: blocks cut, and the deepest cut.
        self.limited_blocks = 0
        self.min_gain = 1.0

    @property
    def ceiling(self) -> int:
        """The ceiling as a PCM16 sample value."""
        return self._ceiling

    def reset(self) -> None:
        """Forget any cut in progress (a new response starts clean)."""
        self._env = self._hold_level = 1.0
        self._hold_left = 0

    def take_stats(self) -> tuple[int, float]:
        """(ms limited, deepest cut in dB) since the last call; resets them."""
        stats = (self.limited_blocks, round(max(0.0, -gain_to_db(self.min_gain)), 1))
        self.limited_blocks = 0
        self.min_gain = 1.0
        return stats

    def process(self, pcm: bytes, gain_start: float = 1.0, gain_end: float | None = None) -> bytes:
        """`pcm` times a gain ramping from `gain_start` to `gain_end` (the
        same if omitted), limited to the ceiling. Returns `pcm` itself when
        there is nothing to do."""
        g1 = gain_start if gain_end is None else gain_end
        g0 = gain_start
        tail = pcm[len(pcm) - 1 :] if len(pcm) % 2 else b""
        samples = pcm_samples(pcm)
        n = len(samples)
        if n == 0:
            return pcm
        peak = max(max(samples), -min(samples))
        gmax = max(g0, g1)
        if self._env >= 1.0 and peak * gmax <= self._ceiling:
            if g0 == 1.0 and g1 == 1.0:
                return pcm
            return pcm_bytes(_ramp(samples, g0, g1)) + tail
        return pcm_bytes(self._limit(samples, g0, g1)) + tail

    def _limit(self, samples: array[int], g0: float, g1: float) -> array[int]:
        n = len(samples)
        ceiling = self._ceiling
        block = _LIMIT_BLOCK
        starts = range(0, n, block)
        slope = (g1 - g0) / n
        # Required gain per block, from its peak and the highest ramp gain in it.
        need: list[float] = []
        for lo in starts:
            seg = samples[lo : lo + block]
            p = max(max(seg), -min(seg))
            g = max(g0 + slope * lo, g0 + slope * min(n, lo + block))
            need.append(1.0 if p * g <= ceiling else ceiling / (p * g))
        # Envelope at each block boundary: lookahead of one block, hold, release.
        env = [self._env]
        level, left = self._hold_level, self._hold_left
        for b in range(len(need)):
            target = min(need[b], need[b + 1]) if b + 1 < len(need) else need[b]
            if target < level:
                level, left = target, self._hold
            elif left > 0:
                left -= 1
            else:
                level = min(target, level + (1.0 - level) * self._release)
                if level > _RELEASED and target >= 1.0:
                    level = 1.0
            env.append(level)
            if need[b] < 1.0:
                self.limited_blocks += 1
                self.min_gain = min(self.min_gain, need[b])
        self._hold_level, self._hold_left = level, left
        self._env = env[-1]
        out = array("h")
        for b, lo in enumerate(starts):
            hi = min(n, lo + block)
            f0 = (g0 + slope * lo) * env[b]
            f1 = (g0 + slope * hi) * env[b + 1]
            step = (f1 - f0) / (hi - lo)
            seg = samples[lo:hi]
            if b == 0:
                out.extend(
                    [
                        max(-ceiling, min(ceiling, int(x * (f0 + step * j))))
                        for j, x in enumerate(seg)
                    ]
                )
            elif step == 0.0:
                out.extend([int(x * f0) for x in seg])
            else:
                out.extend([int(x * (f0 + step * j)) for j, x in enumerate(seg)])
        return out


def _ramp(samples: array[int], g0: float, g1: float) -> array[int]:
    if g0 == g1:
        return array("h", [int(x * g0) for x in samples])
    step = (g1 - g0) / len(samples)
    return array("h", [int(x * (g0 + step * i)) for i, x in enumerate(samples)])


class LevelMemory:
    """The gain each loudness key last settled at, so a source starts where
    it left off (a station, a book): in memory, and through `save` (the
    persona's settings) across restarts."""

    def __init__(self, save: Callable[[str, float], Awaitable[None]] | None = None) -> None:
        self._gains: dict[str, float] = {}
        self._save = save

    def load(self, gains: Iterable[tuple[str, float]]) -> None:
        self._gains.update(gains)

    def get(self, key: str) -> float | None:
        return self._gains.get(key)

    def set(self, key: str, gain_db: float) -> None:
        self._gains[key] = gain_db

    async def save(self, key: str) -> None:
        """Persist `key`'s gain. Never raises: a lost save only means the
        next start of that source is a little off."""
        if self._save is None or key not in self._gains:
            return
        try:
            await self._save(key, self._gains[key])
        except Exception as exc:
            await logger.awarning("audio.level_save_failed", key=key, error=str(exc))


# StreamLeveler tuning (docs/sounds.md "Loudness", "Streams").
_LEVEL_BLOCK = SAMPLE_RATE // 10  # 100 ms
_LEVEL_BLOCK_S = 0.1
SILENCE_GATE_LUFS = -45.0
RELATIVE_GATE_LU = 10.0
# The relative gate's reference: every window over the silence gate,
# averaged with this time constant. Shorter than the program's, so a
# lasting drop (talk after a loud ad, a quiet album after a loud one)
# gets through it in 5 to 15 s and is followed; a quiet passage shorter
# than that is not ridden up. (Against the program estimate itself the
# gate could never open: a drop of over 10 LU locked the gain quiet.)
GATE_TAU_S = 10.0
PROGRAM_TAU_S = 30.0
MAX_CUT_DB = -20.0
MAX_BOOST_DB = 6.0
DOWN_DB_PER_S = 6.0
UP_DB_PER_S = 2.0
# A key never heard: most sources are louder than -20 (radio -14 to -23,
# music masters -8 to -14), so -6 starts a new one near the target or
# under it; the gain then comes down quickly, or up slowly, to what the
# stream measures.
UNKNOWN_START_GAIN_DB = -6.0
REPORT_EVERY_S = 30.0


class StreamLeveler:
    """Keeps a content stream near `PROGRAM_TARGET_LUFS`, causally, with no
    added latency.

    Per 100 ms block, the K-weighted energy of the input. A 400 ms window
    under `SILENCE_GATE_LUFS` (silence, a pause) changes nothing; one
    `RELATIVE_GATE_LU` under the recent level (every window over the
    silence gate, averaged over `GATE_TAU_S`) is a quiet passage and
    changes nothing either. The others feed the program estimate: the
    first window seeds it, then a running mean for `PROGRAM_TAU_S`, then
    an exponential average with that time constant (long, so a song's
    quiet verse isn't ridden up). The
    gain heads for target minus estimate, within [`MAX_CUT_DB`,
    `MAX_BOOST_DB`], at most `DOWN_DB_PER_S` down and `UP_DB_PER_S` up,
    ramped across each chunk. A `PeakLimiter` comes last.

    One leveler per stream: the estimate survives a resume (a press pauses
    the stream and `factory()` runs again). The gain starts at what the key
    last settled at (`LevelMemory`), else `UNKNOWN_START_GAIN_DB`.
    """

    def __init__(
        self,
        key: str,
        memory: LevelMemory,
        *,
        target_lufs: float = PROGRAM_TARGET_LUFS,
        ceiling_dbfs: float = STREAM_CEILING_DBFS,
    ) -> None:
        self.key = key
        self._memory = memory
        self._target = target_lufs
        learned = memory.get(key)
        self.learned = learned is not None
        self.gain_db = learned if learned is not None else UNKNOWN_START_GAIN_DB
        self._kw = KWeighting()
        self._block_sum = 0.0
        self._block_n = 0
        self._window: deque[float] = deque(maxlen=4)  # 400 ms
        self._short: deque[float] = deque(maxlen=30)  # 3 s, for the log
        self._program: float | None = None
        self._accepted = 0
        self._gate_ref: float | None = None
        self._audible = 0
        self._limiter = PeakLimiter(ceiling_dbfs)
        self.audio_s = 0.0
        self._reported_at = 0.0

    @property
    def program_lufs(self) -> float | None:
        """The program estimate the gain follows (None before the first
        window that passed the gates)."""
        return None if self._program is None else lufs(self._program)

    @property
    def short_lufs(self) -> float:
        """Input loudness over the last 3 s (ungated)."""
        if not self._short:
            return -math.inf
        return lufs(sum(self._short) / len(self._short))

    def process(self, pcm: bytes) -> bytes:
        samples = pcm_samples(pcm)
        n = len(samples)
        if n == 0:
            return pcm
        self._measure(samples)
        g0 = self.gain_db
        dt = n / SAMPLE_RATE
        desired = self._desired_db()
        if desired < g0:
            g1 = max(desired, g0 - DOWN_DB_PER_S * dt)
        else:
            g1 = min(desired, g0 + UP_DB_PER_S * dt)
        self.gain_db = g1
        if self._program is not None:
            # Only a gain that followed a measurement is worth starting from.
            self._memory.set(self.key, g1)
        self.audio_s += dt
        return self._limiter.process(pcm, db_to_gain(g0), db_to_gain(g1))

    def report_due(self) -> bool:
        """True once per `REPORT_EVERY_S` of processed audio."""
        if self.audio_s - self._reported_at >= REPORT_EVERY_S:
            self._reported_at = self.audio_s
            return True
        return False

    def stats(self) -> dict[str, object]:
        """The fields of an `audio.level` line; resets the limiter counters."""
        limited_ms, cut_db = self._limiter.take_stats()
        program = self.program_lufs
        short = self.short_lufs
        return {
            "key": self.key,
            "gain_db": round(self.gain_db, 1),
            "program_lufs": None if program is None else round(program, 1),
            "short_lufs": None if short == -math.inf else round(short, 1),
            "limited_ms": limited_ms,
            "cut_db": cut_db,
            "audio_s": round(self.audio_s, 1),
        }

    def _desired_db(self) -> float:
        if self._program is None:
            return self.gain_db
        wanted = self._target - lufs(self._program)
        return max(MAX_CUT_DB, min(MAX_BOOST_DB, wanted))

    def _measure(self, samples: array[int]) -> None:
        pos, n = 0, len(samples)
        while pos < n:
            take = min(_LEVEL_BLOCK - self._block_n, n - pos)
            self._block_sum += self._kw.square_sum(samples[pos : pos + take])
            self._block_n += take
            pos += take
            if self._block_n == _LEVEL_BLOCK:
                self._block(self._block_sum / _LEVEL_BLOCK)
                self._block_sum = 0.0
                self._block_n = 0

    def _block(self, mean_square: float) -> None:
        self._window.append(mean_square)
        self._short.append(mean_square)
        if len(self._window) < 4:
            return
        window = sum(self._window) / 4.0
        loudness = lufs(window)
        if loudness < SILENCE_GATE_LUFS:
            return
        self._audible += 1
        if self._gate_ref is None:
            self._gate_ref = window
        else:
            beta = max(1.0 / self._audible, _LEVEL_BLOCK_S / GATE_TAU_S)
            self._gate_ref += beta * (window - self._gate_ref)
        if loudness < lufs(self._gate_ref) - RELATIVE_GATE_LU:
            return
        self._accepted += 1
        if self._program is None:
            self._program = window
            return
        alpha = max(1.0 / self._accepted, _LEVEL_BLOCK_S / PROGRAM_TAU_S)
        self._program += alpha * (window - self._program)

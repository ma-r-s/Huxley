"""How loud a push-to-talk press was, measured on its PCM16 frames.

A press with only silence after it is committed anyway gets transcribed
by Whisper as a hallucination ("Subtitulos realizados por la comunidad
de Amara.org") and the model answers that (2026-10-12: 11 presses at
06:44-06:45, 07:30, and a bench test). The coordinator measures each
press as its frames arrive and drops a silent one like a too-short one.
"""

from __future__ import annotations

import math
import sys
from array import array
from dataclasses import dataclass

# PCM16 mono at 24 kHz, the one client audio format.
_BYTES_PER_MS = 48
_FULL_SCALE = 32768.0
# 16-bit floor; also what an all-zero press reports.
_FLOOR_DBFS = -96.0

# The first 100 ms are not measured: the press itself (a mechanical
# click, the mic waking up) can peak at full scale with nothing said.
SKIP_MS = 100
# Nor the last 60 ms: the release click lands there. The walkie stops the
# mic only once the release is debounced (~20-30 ms after the switch
# opens), so the click's onset is inside the press.
TAIL_MS = 60

# A press whose loudest measured sample stays below this is silent.
# Reference levels from the walkie's own per-press stats (2026-10-12):
# presses with nobody talking peak at -56/-57 dBFS (one at -51), speech
# peaks at -7 to 0 dBFS. The iOS app reports room noise as a peak of a
# few hundred (about -44 to -36 dBFS). -40 sits 11 dB above the walkie's
# silence and 33 dB below speech; a soft voice at arm's length still
# peaks far above it. Erring loud on purpose: a missed silent press costs
# a stray answer, a dropped real one costs the user's question.
SILENT_PEAK_DBFS = -40.0


def _dbfs(level: float) -> float:
    if level <= 0:
        return _FLOOR_DBFS
    return round(max(_FLOOR_DBFS, 20 * math.log10(level / _FULL_SCALE)), 1)


@dataclass
class PressLevel:
    """Running peak and RMS of one press, without its first `SKIP_MS`
    and last `TAIL_MS`."""

    skip_bytes: int = SKIP_MS * _BYTES_PER_MS
    tail_bytes: int = TAIL_MS * _BYTES_PER_MS
    seen_bytes: int = 0
    peak: int = 0
    sum_squares: float = 0.0
    samples: int = 0
    # The most recent audio, not measured until more arrives after it:
    # whatever is still here at the release is the tail.
    held: bytes = b""

    def feed(self, pcm: bytes) -> None:
        """Add one frame of little-endian PCM16."""
        start = max(0, self.skip_bytes - self.seen_bytes)
        self.seen_bytes += len(pcm)
        if start >= len(pcm):
            return
        buffered = self.held + pcm[start:]
        cut = max(0, len(buffered) - self.tail_bytes) // 2 * 2  # whole samples
        self.held = buffered[cut:]
        if cut == 0:
            return
        frame = array("h")
        frame.frombytes(buffered[:cut])
        if sys.byteorder == "big":
            frame.byteswap()
        self.peak = max(self.peak, max(frame), -min(frame))
        self.sum_squares += math.sumprod(frame, frame)
        self.samples += len(frame)

    @property
    def measured_ms(self) -> int:
        return self.samples * 2 // _BYTES_PER_MS

    @property
    def peak_dbfs(self) -> float:
        return _dbfs(self.peak)

    @property
    def rms_dbfs(self) -> float:
        if self.samples == 0:
            return _FLOOR_DBFS
        return _dbfs(math.sqrt(self.sum_squares / self.samples))

    @property
    def is_silent(self) -> bool:
        """True only on evidence: a press with nothing measured is not silent."""
        return self.samples > 0 and self.peak_dbfs < SILENT_PEAK_DBFS

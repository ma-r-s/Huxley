"""Audio helpers shared across skills.

The Huxley audio channel is PCM16 / 24 kHz / mono. Skills that ship sound
files (earcons, tones, voiced clips) load them once at `setup()` time and
keep the raw PCM in memory; this module is the canonical loader so
audiobooks, news, and any future sound-using skill share the same WAV
parsing + format validation logic.
"""

from __future__ import annotations

import wave
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

# Channel format the Huxley audio path expects. Files at any other
# sample-rate / channel-count / sample-width get skipped silently — they
# would play as garbage if forwarded as-is.
_EXPECTED_CHANNELS = 1
_EXPECTED_SAMPLE_WIDTH_BYTES = 2  # PCM16
_EXPECTED_SAMPLE_RATE_HZ = 24000


def load_pcm_palette(directory: Path, roles: Iterable[str]) -> dict[str, bytes]:
    """Load PCM16 24 kHz mono WAVs at `<directory>/<role>.wav` for each role.

    Returns a dict mapping role name → raw PCM bytes (no WAV header).
    Missing files, unreadable files, and wrong-format files are silently
    skipped — the caller decides what to do with an empty/partial palette
    (typically: log a warning, run without earcons).

    Uses `wave.open()` so files with non-standard WAV headers (LIST/INFO
    chunks, larger riff chunks from re-encoders / metadata editors) still
    produce correct PCM. Don't strip a fixed 44-byte header by hand — that
    breaks the moment a tool touches the file.
    """
    palette: dict[str, bytes] = {}
    if not directory.exists():
        return palette
    for role in roles:
        wav = directory / f"{role}.wav"
        if not wav.exists():
            continue
        try:
            with wave.open(str(wav), "rb") as wf:
                if (
                    wf.getnchannels() != _EXPECTED_CHANNELS
                    or wf.getsampwidth() != _EXPECTED_SAMPLE_WIDTH_BYTES
                    or wf.getframerate() != _EXPECTED_SAMPLE_RATE_HZ
                ):
                    continue
                palette[role] = wf.readframes(wf.getnframes())
        except (wave.Error, OSError):
            continue
    return palette

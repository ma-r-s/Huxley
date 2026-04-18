#!/usr/bin/env python3
"""Extract individual sounds from the Wii BIOS sounds AIFF compilation.

Pipeline:
1. Run ffmpeg silencedetect to find silence boundaries (gaps between sounds).
2. Compute non-silent segments + add tail padding so reverb decays naturally.
3. Extract each as a PCM16 24kHz mono WAV. No level normalization (keep
   original dynamics; reverb tails were getting clipped + boosted before).

Tunable knobs:
- SILENCE_DB: how quiet counts as silence. Lower = more reverb tail captured.
  -55dB catches the natural decay floor; -40dB cuts decays mid-way.
- MIN_GAP: minimum silence duration to count as a sound boundary. Anything
  shorter is treated as part of the same sound.
- TAIL_PAD: extra audio appended past each detected silence_start so reverb
  trails don't get cut. Capped at half the following gap so it can't bleed
  into the next sound.
- MIN_SOUND_LEN: skip extracted segments shorter than this (artifacts).
"""

import re
import subprocess
import sys
from pathlib import Path

SOURCE = Path(
    "/Users/mario/iCloud Drive (Archive)/GarageBand for iOS/My Song.band/Media/All Wii BIOS Sounds.aiff"
)
OUT_DIR = Path("/Users/mario/Projects/Personal/Code/Huxley/personas/abuelos/sounds/raw")

SILENCE_DB = "-55dB"  # was -40dB; that cut reverb tails mid-decay
MIN_GAP = 0.5  # seconds; silences shorter than this don't split sounds
TAIL_PAD = 0.5  # seconds; how much reverb to append past the silence start
MIN_SOUND_LEN = 0.3  # seconds; skip artifacts


def detect_silences(source: Path) -> list[tuple[float, float]]:
    """Return list of (silence_start, silence_end) tuples."""
    cmd = [
        "ffmpeg",
        "-i",
        str(source),
        "-af",
        f"silencedetect=n={SILENCE_DB}:d={MIN_GAP}",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", result.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", result.stderr)]
    # silence_start without a matching silence_end means the file ended in silence
    return list(zip(starts, ends, strict=False))


def get_duration(source: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source),
    ]
    return float(subprocess.run(cmd, capture_output=True, text=True).stdout.strip())


def compute_segments(
    silences: list[tuple[float, float]], total: float
) -> list[tuple[float, float]]:
    """Convert silence boundaries into (start, end) sound segments with tail pad."""
    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for sil_start, sil_end in silences:
        if sil_start > cursor:
            # Cap the tail pad at half the silence gap so it can't bleed into
            # the next sound's onset.
            gap = sil_end - sil_start
            tail = min(TAIL_PAD, gap / 2)
            segments.append((cursor, min(sil_start + tail, sil_end)))
        cursor = sil_end
    if cursor < total:
        segments.append((cursor, total))
    return [s for s in segments if (s[1] - s[0]) >= MIN_SOUND_LEN]


def extract(start: float, end: float, name: str) -> tuple[float, str]:
    """Run ffmpeg to extract one segment. Returns (actual_duration_seconds, peak_db_str)."""
    duration = end - start
    out = OUT_DIR / f"{name}.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(SOURCE),
        "-ss",
        str(start),
        "-t",
        str(duration),
        "-ar",
        "24000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[-200:]}", file=sys.stderr)
        return (-1, "?")
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    try:
        actual = float(probe.stdout.strip())
    except ValueError:
        actual = -1
    vol = subprocess.run(
        ["ffmpeg", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    peak_match = re.search(r"max_volume: ([-\d.]+)", vol.stderr)
    peak = peak_match.group(1) if peak_match else "?"
    return (actual, peak)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Wipe stale extractions from previous runs (different threshold/pad).
    for stale in OUT_DIR.glob("*.wav"):
        stale.unlink()

    total = get_duration(SOURCE)
    silences = detect_silences(SOURCE)
    segments = compute_segments(silences, total)
    print(
        f"Source: {total:.2f}s, "
        f"silences detected at {SILENCE_DB} (min gap {MIN_GAP}s, tail pad {TAIL_PAD}s)"
    )
    print(f"Extracting {len(segments)} segments to {OUT_DIR}\n")
    for i, (start, end) in enumerate(segments):
        name = f"s{i:02d}"
        dur, peak = extract(start, end, name)
        print(f"  {name}.wav  src=[{start:6.2f}-{end:6.2f}]  out={dur:.2f}s  peak={peak}dB")
    print("\nDone.")


if __name__ == "__main__":
    main()

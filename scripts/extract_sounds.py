#!/usr/bin/env python3
"""Extract individual sounds from the Wii BIOS sounds AIFF compilation.

Parses silence boundaries, identifies non-silent segments, extracts each
as a stripped/normalized PCM16 24kHz mono WAV file.
"""

import subprocess
import sys
from pathlib import Path

SOURCE = Path(
    "/Users/mario/iCloud Drive (Archive)/GarageBand for iOS/My Song.band/Media/All Wii BIOS Sounds.aiff"
)
OUT_DIR = Path("/Users/mario/Projects/Personal/Code/Huxley/personas/abuelos/sounds/raw")
TOTAL_DURATION = 83.50

# Segments derived from silencedetect -n=-40dB:d=0.5
# Format: (start, end, label_hint)
# Tiny segments (<0.3s) excluded; very long ones flagged.
SEGMENTS = [
    (0.000, 0.909, "s00_short_open"),
    (3.015, 5.676, "s01_long_chime"),
    (7.792, 9.731, "s02_chime"),
    (11.075, 14.604, "s03_long_sequence"),
    (15.304, 17.738, "s04_sequence"),
    (18.723, 22.425, "s05_long_sequence2"),
    (23.003, 23.494, "s06_short_click"),
    (24.387, 25.121, "s07_click"),
    (25.957, 27.202, "s08_chime"),
    (28.542, 30.029, "s09_chime"),
    (31.173, 32.013, "s10_short"),
    (32.909, 37.666, "s11_long_music"),
    (40.414, 41.245, "s12_short"),
    (42.629, 43.232, "s13_short"),
    (43.830, 44.550, "s14_short"),
    (45.492, 46.297, "s15_short"),
    (49.323, 50.116, "s16_short"),
    (51.261, 51.943, "s17_short"),
    (53.662, 54.306, "s18_short"),
    (55.615, 55.998, "s19_tiny"),
    (64.479, 65.000, "s20_short"),
    (76.542, 77.575, "s21_chime"),
]


def extract(start: float, end: float, name: str) -> None:
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
        # Convert to PCM16 24kHz mono; silence boundaries already tight from analysis
        "-af",
        "dynaudnorm=p=0.9:r=0.5",
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
        return
    # Report actual duration after processing
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
    raw_dur = probe.stdout.strip()
    try:
        actual = float(raw_dur) if probe.returncode == 0 else -1
    except ValueError:
        actual = -1
    print(f"  {name}.wav  raw={duration:.2f}s  stripped={actual:.2f}s")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Extracting {len(SEGMENTS)} segments to {OUT_DIR}\n")
    for start, end, name in SEGMENTS:
        print(f"[{start:.2f}-{end:.2f}] {name}")
        extract(start, end, name)
    print("\nDone.")


if __name__ == "__main__":
    main()

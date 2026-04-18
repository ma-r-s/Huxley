#!/usr/bin/env python3
"""Extract individual sounds from a compilation audio file (e.g., a Wii BIOS dump).

Pipeline:
1. Run ffmpeg silencedetect to find silence boundaries (gaps between sounds).
2. Compute non-silent segments + add tail padding so reverb decays naturally.
3. Extract each as a PCM16 24kHz mono WAV. No level normalization (keep
   original dynamics; reverb tails were getting clipped + boosted before).

Usage:
    python3 scripts/extract_sounds.py --source path/to/compilation.aiff [options]

Or set HUXLEY_SOUND_SOURCE in the env if you'd rather not pass --source each run.

Tunable knobs (all flags):
- --silence-db: how quiet counts as silence. Lower = more reverb tail captured.
  -55dB catches the natural decay floor; -40dB cuts decays mid-way.
- --min-gap: minimum silence duration to count as a sound boundary. Anything
  shorter is treated as part of the same sound.
- --tail-pad: extra audio appended past each detected silence_start so reverb
  trails don't get cut. Capped at half the following gap so it can't bleed
  into the next sound.
- --min-sound-len: skip extracted segments shorter than this (artifacts).
- --out: output directory (default: personas/abuelos/sounds/raw)
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_OUT_DIR = (
    Path(__file__).resolve().parent.parent / "personas" / "abuelos" / "sounds" / "raw"
)


def detect_silences(source: Path, silence_db: str, min_gap: float) -> list[tuple[float, float]]:
    """Return list of (silence_start, silence_end) tuples."""
    cmd = [
        "ffmpeg",
        "-i",
        str(source),
        "-af",
        f"silencedetect=n={silence_db}:d={min_gap}",
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
    silences: list[tuple[float, float]],
    total: float,
    tail_pad: float,
    min_sound_len: float,
) -> list[tuple[float, float]]:
    """Convert silence boundaries into (start, end) sound segments with tail pad."""
    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for sil_start, sil_end in silences:
        if sil_start > cursor:
            # Cap the tail pad at half the silence gap so it can't bleed into
            # the next sound's onset.
            gap = sil_end - sil_start
            tail = min(tail_pad, gap / 2)
            segments.append((cursor, min(sil_start + tail, sil_end)))
        cursor = sil_end
    if cursor < total:
        segments.append((cursor, total))
    return [s for s in segments if (s[1] - s[0]) >= min_sound_len]


def extract(source: Path, out_dir: Path, start: float, end: float, name: str) -> tuple[float, str]:
    """Run ffmpeg to extract one segment. Returns (actual_duration_seconds, peak_db_str)."""
    duration = end - start
    out = out_dir / f"{name}.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract individual sounds from a compilation audio file.",
    )
    p.add_argument(
        "--source",
        type=Path,
        default=os.environ.get("HUXLEY_SOUND_SOURCE"),
        help="Path to the compilation audio file. Falls back to HUXLEY_SOUND_SOURCE env var.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUT_DIR}).",
    )
    p.add_argument("--silence-db", default="-55dB", help="Silence threshold (default: -55dB).")
    p.add_argument(
        "--min-gap",
        type=float,
        default=0.5,
        help="Min silence duration to count as a boundary (default: 0.5s).",
    )
    p.add_argument(
        "--tail-pad",
        type=float,
        default=0.5,
        help="Reverb tail to keep past silence_start (default: 0.5s).",
    )
    p.add_argument(
        "--min-sound-len",
        type=float,
        default=0.3,
        help="Skip extracted segments shorter than this (default: 0.3s).",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.source is None:
        sys.exit(
            "error: no source file given. Pass --source <path> or set HUXLEY_SOUND_SOURCE in env."
        )
    source: Path = Path(args.source).expanduser()
    if not source.exists():
        sys.exit(f"error: source file not found: {source}")

    out_dir: Path = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    # Wipe stale extractions from previous runs (different threshold/pad).
    for stale in out_dir.glob("*.wav"):
        stale.unlink()

    total = get_duration(source)
    silences = detect_silences(source, args.silence_db, args.min_gap)
    segments = compute_segments(silences, total, args.tail_pad, args.min_sound_len)
    print(
        f"Source: {source} ({total:.2f}s); "
        f"silences at {args.silence_db}, min gap {args.min_gap}s, tail pad {args.tail_pad}s"
    )
    print(f"Extracting {len(segments)} segments to {out_dir}\n")
    for i, (start, end) in enumerate(segments):
        name = f"s{i:02d}"
        dur, peak = extract(source, out_dir, start, end, name)
        print(f"  {name}.wav  src=[{start:6.2f}-{end:6.2f}]  out={dur:.2f}s  peak={peak}dB")
    print("\nDone.")


if __name__ == "__main__":
    main()

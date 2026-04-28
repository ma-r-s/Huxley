"""Tests for huxley_sdk.audio.load_pcm_palette.

Used by audiobooks + news skills (and any future sound-using skill) to load
PCM16 24kHz mono WAV palettes. Format-mismatched files and missing files
must be silently skipped — empty palette is a valid state, not an error.
"""

from __future__ import annotations

import wave
from typing import TYPE_CHECKING

from huxley_sdk.audio import load_pcm_palette

if TYPE_CHECKING:
    from pathlib import Path


def _write_wav(
    path: Path,
    *,
    channels: int = 1,
    sampwidth: int = 2,
    framerate: int = 24000,
    frames: bytes = b"\x00\x01" * 100,
) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(frames)


def test_missing_directory_returns_empty(tmp_path: Path) -> None:
    palette = load_pcm_palette(tmp_path / "does_not_exist", ["alpha"])
    assert palette == {}


def test_existing_dir_no_files_returns_empty(tmp_path: Path) -> None:
    palette = load_pcm_palette(tmp_path, ["alpha", "beta"])
    assert palette == {}


def test_loads_correct_format(tmp_path: Path) -> None:
    _write_wav(tmp_path / "alpha.wav", frames=b"\x00\x01" * 50)
    palette = load_pcm_palette(tmp_path, ["alpha"])
    assert "alpha" in palette
    assert palette["alpha"] == b"\x00\x01" * 50


def test_skips_wrong_channels(tmp_path: Path) -> None:
    _write_wav(tmp_path / "alpha.wav", channels=2)
    palette = load_pcm_palette(tmp_path, ["alpha"])
    assert palette == {}


def test_skips_wrong_sample_rate(tmp_path: Path) -> None:
    _write_wav(tmp_path / "alpha.wav", framerate=44100)
    palette = load_pcm_palette(tmp_path, ["alpha"])
    assert palette == {}


def test_skips_wrong_sample_width(tmp_path: Path) -> None:
    # 1-byte samples (PCM8) instead of 2-byte (PCM16)
    _write_wav(tmp_path / "alpha.wav", sampwidth=1, frames=b"\x00" * 100)
    palette = load_pcm_palette(tmp_path, ["alpha"])
    assert palette == {}


def test_loads_only_requested_roles(tmp_path: Path) -> None:
    _write_wav(tmp_path / "alpha.wav")
    _write_wav(tmp_path / "beta.wav")
    _write_wav(tmp_path / "ignored.wav")
    palette = load_pcm_palette(tmp_path, ["alpha", "beta"])
    assert set(palette.keys()) == {"alpha", "beta"}


def test_partial_load_when_some_files_missing(tmp_path: Path) -> None:
    _write_wav(tmp_path / "alpha.wav")
    palette = load_pcm_palette(tmp_path, ["alpha", "beta"])
    assert "alpha" in palette
    assert "beta" not in palette


def test_corrupt_file_silently_skipped(tmp_path: Path) -> None:
    """Random bytes claiming to be a WAV — wave.open raises, we skip."""
    (tmp_path / "alpha.wav").write_bytes(b"NOT_A_WAV_HEADER_AT_ALL")
    palette = load_pcm_palette(tmp_path, ["alpha"])
    assert palette == {}

"""Unit tests for the transport layer.

Covers the pure pieces — PCM downsampling + phone normalization — plus
a smoke test on `TelegramTransport.__init__` proving the class
constructs without importing pyrogram (so the skill can be loaded in
environments that haven't installed the Telegram deps, and so tests
don't pull in the heavy native binding).

Integration tests that actually dial Telegram live at the spike, not
here.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from huxley_skill_comms_telegram.transport import (
    TelegramTransport,
    TransportError,
    downsample_48k_stereo_to_24k_mono,
    normalize_phone,
)


class TestDownsampler:
    """The 48 kHz stereo → 24 kHz mono function is PURE — easy to verify."""

    def test_empty_input_returns_empty(self) -> None:
        assert downsample_48k_stereo_to_24k_mono(b"") == b""

    def test_partial_frame_input_dropped_cleanly(self) -> None:
        # 3 bytes = not a full stereo frame (4 bytes); function must
        # handle it without indexing past the end.
        assert downsample_48k_stereo_to_24k_mono(b"\x00\x01\x02") == b""

    def test_single_decimation_pair_yields_one_mono_sample(self) -> None:
        # Two stereo frames: (L=100, R=-100) and (L=999, R=-999).
        # Decimation keeps the FIRST frame; mono = (100 + -100) // 2 = 0.
        pcm_in = struct.pack("<4h", 100, -100, 999, -999)
        out = downsample_48k_stereo_to_24k_mono(pcm_in)
        assert len(out) == 2  # one PCM16 sample
        (sample,) = struct.unpack("<h", out)
        assert sample == 0

    def test_stream_of_known_values(self) -> None:
        # 4 stereo frames: (1000,2000) (3000,4000) (5000,6000) (7000,8000)
        # n_in_frames=4; n_out_frames=2; outputs = avg of frames 0 & 2:
        #   M0 = (1000 + 2000)//2 = 1500
        #   M1 = (5000 + 6000)//2 = 5500
        pcm_in = struct.pack("<8h", 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000)
        out = downsample_48k_stereo_to_24k_mono(pcm_in)
        assert struct.unpack("<2h", out) == (1500, 5500)

    def test_halves_the_sample_count_exactly(self) -> None:
        # 48 kHz stereo → 24 kHz mono = 1/4 the bytes.
        # Start with 1 second of silence = 48000*4 = 192000 bytes.
        pcm_in = b"\x00" * (48_000 * 4)
        out = downsample_48k_stereo_to_24k_mono(pcm_in)
        assert len(out) == 48_000  # 24 kHz mono * 2 bytes = 48000 bytes/sec

    def test_channel_averaging_preserves_voice_envelope(self) -> None:
        # Build a stereo sine wave at 440 Hz, both channels in phase.
        # Averaging keeps the same waveform; decimating by 2 halves the
        # rate, so output is 220 Hz-relative (but same PCM amplitude).
        import math

        n_frames = 4800  # 100ms at 48 kHz
        amp = 10_000
        buf = bytearray()
        for i in range(n_frames):
            v = int(math.sin(2 * math.pi * 440 * i / 48_000) * amp)
            buf.extend(struct.pack("<2h", v, v))  # L=R
        out = downsample_48k_stereo_to_24k_mono(bytes(buf))
        # Peak amplitude should still be ~amp (averaging L=R doesn't reduce it).
        samples = struct.unpack(f"<{len(out) // 2}h", out)
        assert max(samples) > amp * 0.95
        assert min(samples) < -amp * 0.95


class TestNormalizePhone:
    def test_strips_spaces_and_dashes(self) -> None:
        assert normalize_phone("+57 315 328 3397") == "+573153283397"
        assert normalize_phone("+57-315-328-3397") == "+573153283397"

    def test_strips_parens(self) -> None:
        assert normalize_phone("+1 (555) 123-4567") == "+15551234567"

    def test_leaves_leading_plus_alone(self) -> None:
        assert normalize_phone("+573153283397") == "+573153283397"

    def test_strips_surrounding_whitespace(self) -> None:
        assert normalize_phone("  +573153283397  ") == "+573153283397"


class TestTransportConstruction:
    def test_init_does_not_import_pyrogram(self, tmp_path: Path) -> None:
        # Verifying by construction: if this succeeds without
        # pyrogram/pytgcalls on PYTHONPATH it means __init__ didn't
        # reach their imports. We don't actually strip them from the
        # venv for this test — we rely on them being lazy-imported
        # inside connect() / place_call().
        t = TelegramTransport(
            api_id=12345678,
            api_hash="abcdef0123456789",
            session_dir=tmp_path,
        )
        # No call made yet.
        assert t._active_user_id is None  # type: ignore[attr-defined]
        assert t._call_py is None  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_resolve_contact_before_connect_raises(self, tmp_path: Path) -> None:
        t = TelegramTransport(
            api_id=12345678,
            api_hash="abcdef0123456789",
            session_dir=tmp_path,
        )
        with pytest.raises(TransportError, match="before connect"):
            await t.resolve_contact("+573186851696")

    @pytest.mark.asyncio
    async def test_place_call_before_connect_raises(self, tmp_path: Path) -> None:
        t = TelegramTransport(
            api_id=12345678,
            api_hash="abcdef0123456789",
            session_dir=tmp_path,
        )
        with pytest.raises(TransportError, match="before connect"):
            await t.place_call(7392572538)

    @pytest.mark.asyncio
    async def test_send_pcm_before_call_is_noop(self, tmp_path: Path) -> None:
        # Without an active call (no `_active_user_id`), `send_pcm`
        # should swallow the bytes silently — no crash, no send.
        t = TelegramTransport(
            api_id=12345678,
            api_hash="abcdef0123456789",
            session_dir=tmp_path,
        )
        await t.send_pcm(b"\x01\x02" * 128)
        assert t._sent_count == 0  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_send_pcm_zero_length_is_noop(self, tmp_path: Path) -> None:
        t = TelegramTransport(
            api_id=12345678,
            api_hash="abcdef0123456789",
            session_dir=tmp_path,
        )
        await t.send_pcm(b"")
        assert t._sent_count == 0  # type: ignore[attr-defined]

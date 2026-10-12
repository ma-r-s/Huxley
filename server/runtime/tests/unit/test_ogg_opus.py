"""The Ogg Opus muxer the recorder writes its files with.

Pages are parsed back by an independent reader (`ogg_reader.py`: RFC
3533 layout, a bitwise CRC), so the writer is not checked against itself. Where
ffmpeg is installed, one test also has it decode a file end to end.
"""

from __future__ import annotations

import io
import math
import shutil
import struct
import subprocess
from typing import TYPE_CHECKING

import pytest

from huxley.server import opus_codec
from huxley.server.ogg_opus import (
    MAX_PACKET_BYTES,
    OggOpusWriter,
    ogg_crc32,
    opus_packet_samples,
)
from tests.unit.ogg_reader import Page, crc_bitwise, parse_pages

if TYPE_CHECKING:
    from pathlib import Path


def _packet(samples_config: int, n: int, size: int = 60) -> bytes:
    """A packet with TOC config `samples_config`, code 0, distinct body."""
    return bytes([samples_config << 3]) + n.to_bytes(2, "big") + bytes(size - 3)


# SILK NB 60 ms (config 3): what a 60 ms voice packet's TOC can say.
_P60 = 3


def _write(packets: list[bytes], **kwargs: int) -> bytes:
    buf = io.BytesIO()
    writer = OggOpusWriter(buf, serial=0x1234ABCD, channels=1, input_sample_rate=24000, **kwargs)
    for p in packets:
        writer.add(p, opus_packet_samples(p))
    writer.finish()
    return buf.getvalue()


# --- CRC ---------------------------------------------------------------------


def test_crc_matches_the_reference_and_the_check_value() -> None:
    # CRC-32 with poly 0x04C11DB7, init 0, no reflection, no xorout:
    # the published check value over "123456789".
    assert ogg_crc32(b"123456789") == 0x89A1897F
    for data in (b"", b"\x00", b"OggS" * 100, bytes(range(256)) * 3):
        assert ogg_crc32(data) == crc_bitwise(data)


# --- TOC durations -----------------------------------------------------------


@pytest.mark.parametrize(
    ("packet", "samples"),
    [
        (bytes([0 << 3]), 480),  # SILK NB 10 ms
        (bytes([3 << 3]), 2880),  # SILK NB 60 ms
        (bytes([11 << 3]), 2880),  # SILK WB 60 ms
        (bytes([13 << 3]), 960),  # hybrid SWB 20 ms
        (bytes([16 << 3]), 120),  # CELT NB 2.5 ms
        (bytes([31 << 3]), 960),  # CELT FB 20 ms
        (bytes([(31 << 3) | 1]), 1920),  # two equal frames
        (bytes([(31 << 3) | 2]), 1920),  # two frames, different sizes
        (bytes([(31 << 3) | 3, 3]), 2880),  # code 3, three 20 ms frames
        (bytes([(3 << 3) | 3, 2]), 5760),  # 2 x 60 ms = 120 ms, the maximum
        (bytes([(3 << 3) | 3, 3]), 0),  # 180 ms: not a legal packet
        (bytes([(31 << 3) | 3, 0]), 0),  # code 3, zero frames
        (bytes([(31 << 3) | 3]), 0),  # code 3 without its count byte
        (b"", 0),
    ],
)
def test_packet_duration_comes_from_the_toc(packet: bytes, samples: int) -> None:
    assert opus_packet_samples(packet) == samples


def test_the_servers_own_packets_are_60_ms() -> None:
    if opus_codec.opus_unavailable_reason() is not None:
        pytest.skip("libopus not loadable")
    enc = opus_codec.OpusEncoderStream()
    packets = enc.push(bytes(opus_codec.FRAME_BYTES * 3))
    assert [opus_packet_samples(p) for p in packets] == [2880] * 3


# --- Pages -------------------------------------------------------------------


def test_header_pages_are_a_valid_opus_head_and_tags() -> None:
    pages = parse_pages(_write([_packet(_P60, 0)]))
    head, tags = pages[0], pages[1]
    assert all(p.crc_ok for p in pages), "CRC must match the bitwise reference"
    assert head.flags == 0x02  # beginning of stream, alone on its page
    assert head.granule == 0 and tags.granule == 0
    assert len(head.packets) == 1 and len(tags.packets) == 1
    magic, version, channels, pre_skip, rate, gain, family = struct.unpack(
        "<8sBBHIhB", head.packets[0]
    )
    assert (magic, version, channels, pre_skip, rate, gain, family) == (
        b"OpusHead",
        1,
        1,
        0,
        24000,
        0,
        0,
    )
    assert tags.packets[0].startswith(b"OpusTags")
    (vendor_len,) = struct.unpack_from("<I", tags.packets[0], 8)
    assert tags.packets[0][12 : 12 + vendor_len] == b"huxley recorder"
    assert tags.flags == 0
    assert {p.serial for p in pages} == {0x1234ABCD}
    assert [p.sequence for p in pages] == list(range(len(pages)))


def test_granule_grows_by_each_packets_own_duration() -> None:
    # Mixed durations: the granule must follow each TOC, not a constant.
    packets = [
        _packet(_P60, 0),  # 2880
        _packet(31, 1),  # 960
        _packet(16, 2),  # 120
        _packet(_P60, 3),  # 2880
        _packet(13, 4),  # 960
    ]
    pages = parse_pages(_write(packets, packets_per_page=2))[2:]
    expected = 0
    seen: list[bytes] = []
    for page in pages:
        assert not page.continued_last
        seen += page.packets
        expected += sum(opus_packet_samples(p) for p in page.packets)
        assert page.granule == expected
    assert seen == packets
    assert pages[-1].granule == 2880 + 960 + 120 + 2880 + 960


def test_n_packets_round_trip_and_only_the_last_page_ends_the_stream() -> None:
    packets = [_packet(_P60, i, size=40 + (i * 37) % 400) for i in range(100)]
    data = _write(packets)
    pages = parse_pages(data)
    assert all(p.crc_ok for p in pages)
    recovered = [pkt for page in pages[2:] for pkt in page.packets]
    assert recovered == packets
    assert [p.flags & 0x04 for p in pages[:-1]] == [0] * (len(pages) - 1)
    assert pages[-1].flags & 0x04
    assert pages[-1].granule == 100 * 2880
    # About a second a page: 17 packets of 60 ms.
    assert max(len(p.packets) for p in pages[2:]) == 17


@pytest.mark.parametrize("size", [254, 255, 256, 510, 1275, MAX_PACKET_BYTES])
def test_lacing_edges_round_trip(size: int) -> None:
    # A multiple of 255 needs a 0 lacing value to end on its page.
    packets = [_packet(_P60, 1, size=size), _packet(_P60, 2, size=size)]
    pages = parse_pages(_write(packets))
    assert [pkt for page in pages[2:] for pkt in page.packets] == packets


def test_a_page_never_overflows_its_lacing_table() -> None:
    # 1275-byte packets need 6 lacing values each: 17 of them would need
    # 102, fine; 60 of 4000 bytes (16 each) would not fit one page.
    packets = [_packet(_P60, i, size=4000) for i in range(60)]
    pages = parse_pages(_write(packets, packets_per_page=60))
    assert [pkt for page in pages[2:] for pkt in page.packets] == packets
    assert all(len(p.packets) <= 15 for p in pages[2:])


def test_an_oversized_packet_is_refused() -> None:
    writer = OggOpusWriter(io.BytesIO(), serial=1, channels=1, input_sample_rate=24000)
    with pytest.raises(ValueError, match="does not fit"):
        writer.add(bytes([_P60 << 3]) + bytes(MAX_PACKET_BYTES), 2880)


def test_a_truncated_file_still_has_whole_pages() -> None:
    """A power cut mid-write: what reached the disk parses up to the last
    whole page (players stop there)."""
    data = _write([_packet(_P60, i) for i in range(40)])
    cut = data[: len(data) - 30]
    pages: list[Page] = []
    pos = 0
    while pos + 27 <= len(cut):
        nseg = cut[pos + 26]
        end = pos + 27 + nseg + sum(cut[pos + 27 : pos + 27 + nseg])
        if end > len(cut):
            break  # the torn last page
        pages.extend(parse_pages(cut[pos:end]))
        pos = end
    assert len(pages) == 4  # both headers and the first two data pages
    assert all(p.crc_ok for p in pages)
    assert [pkt for page in pages[2:] for pkt in page.packets] == [
        _packet(_P60, i) for i in range(34)
    ]


# --- A real decoder ------------------------------------------------------------

_FFPROBE = shutil.which("ffprobe")
_FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(_FFPROBE is None or _FFMPEG is None, reason="ffmpeg/ffprobe not on PATH")
def test_ffmpeg_decodes_a_written_file_to_its_duration(tmp_path: Path) -> None:
    if opus_codec.opus_unavailable_reason() is not None:
        pytest.skip("libopus not loadable")
    assert _FFPROBE is not None and _FFMPEG is not None
    enc = opus_codec.OpusEncoderStream()
    n = 50  # 3 s
    pcm = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 24000)))
        for i in range(opus_codec.FRAME_SAMPLES * n)
    )
    packets = enc.push(pcm)
    assert len(packets) == n
    path = tmp_path / "speech.ogg"
    with path.open("wb") as fh:
        writer = OggOpusWriter(fh, serial=7, channels=1, input_sample_rate=24000)
        for p in packets:
            writer.add(p, opus_packet_samples(p))
        writer.finish()
    probe = subprocess.run(
        [
            _FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_name,channels",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    fields = dict(line.split("=", 1) for line in probe.stdout.split())
    assert fields["codec_name"] == "opus"
    assert fields["channels"] == "1"
    assert abs(float(fields["duration"]) - 3.0) < 0.01
    # Decodes cleanly (a bad CRC or page would be reported on stderr),
    # to the exact sample count at 48 kHz.
    decoded = subprocess.run(
        [_FFMPEG, "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
        capture_output=True,
        check=True,
    )
    assert decoded.stderr == b""
    assert len(decoded.stdout) // 2 == n * 2880

"""Ogg Opus files from raw Opus packets, stored as they are (no re-encode).

The recorder (`recorder.py`) keeps every Opus packet that crossed the
wire. This module wraps them in the container RFC 7845 defines, so any
player (ffmpeg, VLC, a browser's <audio>) opens the file:

- page 0: `OpusHead` alone, beginning-of-stream flag;
- page 1: `OpusTags` alone;
- then data pages, each holding whole packets, granule position = the
  48 kHz sample count of every packet that ends on the page;
- the last page carries the end-of-stream flag.

A packet's duration comes from its TOC byte (RFC 6716 section 3.1), not
from a constant, so a client that changes frame size still gets an exact
timeline. Pre-skip is 0: the device decoding these packets live skips
nothing either, so the file plays what it played.

Pages are written as they fill (about one a second of audio) and flushed
to the OS, so a file cut short by a power loss is still playable up to
its last whole page.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import BinaryIO

# Ogg's CRC: polynomial 0x04C11DB7, initial value 0, no reflection, no
# final xor (RFC 3533 section 6). Not zlib's crc32, which is reflected.
_CRC_POLY = 0x04C11DB7


def _crc_table() -> tuple[int, ...]:
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ _CRC_POLY) if r & 0x80000000 else (r << 1)
            r &= 0xFFFFFFFF
        table.append(r)
    return tuple(table)


_CRC_TABLE = _crc_table()


def ogg_crc32(data: bytes) -> int:
    """The checksum an Ogg page carries, over the page with its CRC field
    zeroed."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) ^ byte) & 0xFF]
    return crc


# Samples per frame at 48 kHz, by TOC configuration number (0-31):
# SILK 0-11 (NB, MB, WB x 10/20/40/60 ms), hybrid 12-15 (SWB, FB x
# 10/20 ms), CELT 16-31 (NB, WB, SWB, FB x 2.5/5/10/20 ms).
_SILK_FRAME = (480, 960, 1920, 2880)
_HYBRID_FRAME = (480, 960)
_CELT_FRAME = (120, 240, 480, 960)
# Longest packet Opus allows: 120 ms.
_MAX_PACKET_SAMPLES = 5760


def opus_packet_samples(packet: bytes) -> int:
    """Duration of one Opus packet in 48 kHz samples, read from its TOC
    byte. 0 for a packet that cannot be one (empty, a code-3 packet with
    no count byte or zero frames, longer than 120 ms)."""
    if not packet:
        return 0
    toc = packet[0]
    config = toc >> 3
    if config < 12:
        per_frame = _SILK_FRAME[config % 4]
    elif config < 16:
        per_frame = _HYBRID_FRAME[config % 2]
    else:
        per_frame = _CELT_FRAME[config % 4]
    code = toc & 0x03
    if code == 0:
        frames = 1
    elif code in (1, 2):
        frames = 2
    else:
        if len(packet) < 2:
            return 0
        frames = packet[1] & 0x3F
    total = frames * per_frame
    if total == 0 or total > _MAX_PACKET_SAMPLES:
        return 0
    return total


def opus_head(*, channels: int, input_sample_rate: int, pre_skip: int = 0) -> bytes:
    """The identification header (RFC 7845 section 5.1), channel mapping
    family 0 (mono or stereo, no mapping table)."""
    return b"OpusHead" + struct.pack("<BBHIhB", 1, channels, pre_skip, input_sample_rate, 0, 0)


def opus_tags(vendor: str, comments: tuple[str, ...] = ()) -> bytes:
    """The comment header (RFC 7845 section 5.2)."""
    out = bytearray(b"OpusTags")
    vendor_bytes = vendor.encode("utf-8")
    out += struct.pack("<I", len(vendor_bytes)) + vendor_bytes
    out += struct.pack("<I", len(comments))
    for comment in comments:
        encoded = comment.encode("utf-8")
        out += struct.pack("<I", len(encoded)) + encoded
    return bytes(out)


# Header-type flags (RFC 3533 section 6).
_FLAG_BOS = 0x02
_FLAG_EOS = 0x04
# A page holds at most 255 lacing values.
_MAX_LACING = 255


def _lacing(length: int) -> list[int]:
    """Lacing values for one packet: 255s, then the remainder (a 0 when
    the length is a multiple of 255, so the packet ends on this page)."""
    return [255] * (length // 255) + [length % 255]


def lacing_count(length: int) -> int:
    return length // 255 + 1


# Largest packet that fits on one page by itself.
MAX_PACKET_BYTES = _MAX_LACING * 255 - 1


def ogg_page(
    *,
    serial: int,
    sequence: int,
    granule: int,
    packets: list[bytes],
    bos: bool = False,
    eos: bool = False,
) -> bytes:
    """One page of whole packets. The caller keeps each page within 255
    lacing values (`lacing_count`)."""
    lacing: list[int] = []
    for packet in packets:
        lacing += _lacing(len(packet))
    if len(lacing) > _MAX_LACING:
        msg = f"{len(lacing)} lacing values do not fit one Ogg page"
        raise ValueError(msg)
    flags = (_FLAG_BOS if bos else 0) | (_FLAG_EOS if eos else 0)
    header = struct.pack(
        "<4sBBqIIIB", b"OggS", 0, flags, granule, serial, sequence, 0, len(lacing)
    ) + bytes(lacing)
    page = bytearray(header + b"".join(packets))
    struct.pack_into("<I", page, 22, ogg_crc32(bytes(page)))
    return bytes(page)


class OggOpusWriter:
    """Writes one logical Opus stream to `fh`.

    `add` buffers packets and writes a page once `packets_per_page` are
    waiting (17 x 60 ms is about a second); the newest packet always
    stays buffered so `finish` has one to put on the end-of-stream page.
    Every page is flushed to the OS as it is written.
    """

    def __init__(
        self,
        fh: BinaryIO,
        *,
        serial: int,
        channels: int,
        input_sample_rate: int,
        pre_skip: int = 0,
        vendor: str = "huxley recorder",
        packets_per_page: int = 17,
    ) -> None:
        self._fh = fh
        self._serial = serial & 0xFFFFFFFF
        self._sequence = 0
        # Counts every decoded sample, pre-skip included (RFC 7845
        # section 4); with pre-skip 0 it is simply the play time.
        self._granule = 0
        self._pending: list[bytes] = []
        self._pending_lacing = 0
        self._pending_samples = 0
        self._packets_per_page = max(1, packets_per_page)
        self._write(
            ogg_page(
                serial=self._serial,
                sequence=self._next_sequence(),
                granule=0,
                packets=[
                    opus_head(
                        channels=channels,
                        input_sample_rate=input_sample_rate,
                        pre_skip=pre_skip,
                    )
                ],
                bos=True,
            )
        )
        self._write(
            ogg_page(
                serial=self._serial,
                sequence=self._next_sequence(),
                granule=0,
                packets=[opus_tags(vendor)],
            )
        )

    @property
    def granule(self) -> int:
        """Granule position after every packet added so far."""
        return self._granule + self._pending_samples

    def add(self, packet: bytes, samples: int) -> None:
        """Queue one packet of `samples` 48 kHz samples. Raises ValueError
        for a packet too big for a page (over `MAX_PACKET_BYTES`)."""
        if len(packet) > MAX_PACKET_BYTES:
            msg = f"packet of {len(packet)} bytes does not fit one Ogg page"
            raise ValueError(msg)
        need = lacing_count(len(packet))
        if self._pending and (
            len(self._pending) >= self._packets_per_page
            or self._pending_lacing + need > _MAX_LACING
        ):
            self._write_pending(eos=False)
        self._pending.append(packet)
        self._pending_lacing += need
        self._pending_samples += samples

    def finish(self) -> None:
        """Write the buffered packets on the end-of-stream page. A stream
        that never got a packet ends with an empty end-of-stream page."""
        self._write_pending(eos=True)

    def _write_pending(self, *, eos: bool) -> None:
        self._granule += self._pending_samples
        self._write(
            ogg_page(
                serial=self._serial,
                sequence=self._next_sequence(),
                granule=self._granule,
                packets=self._pending,
                eos=eos,
            )
        )
        self._pending = []
        self._pending_lacing = 0
        self._pending_samples = 0

    def _next_sequence(self) -> int:
        seq = self._sequence
        self._sequence += 1
        return seq

    def _write(self, page: bytes) -> None:
        self._fh.write(page)
        self._fh.flush()

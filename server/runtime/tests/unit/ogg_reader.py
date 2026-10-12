"""An Ogg page reader for the recorder's tests, written from RFC 3533
independently of `huxley.server.ogg_opus` (bitwise CRC, its own lacing
walk), so the writer is never checked against itself."""

from __future__ import annotations

import struct
from dataclasses import dataclass


def crc_bitwise(data: bytes) -> int:
    """Ogg's CRC one bit at a time: poly 0x04C11DB7, init 0, no reflection,
    no final xor."""
    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else crc << 1
            crc &= 0xFFFFFFFF
    return crc


@dataclass
class Page:
    flags: int
    granule: int
    serial: int
    sequence: int
    crc: int
    crc_ok: bool
    packets: list[bytes]
    continued_last: bool  # the last packet runs on to the next page


def parse_pages(data: bytes) -> list[Page]:
    pages: list[Page] = []
    pos = 0
    while pos < len(data):
        assert data[pos : pos + 4] == b"OggS", f"no capture pattern at {pos}"
        version, flags, granule, serial, sequence, crc, nseg = struct.unpack_from(
            "<BBqIIIB", data, pos + 4
        )
        assert version == 0
        lacing = data[pos + 27 : pos + 27 + nseg]
        end = pos + 27 + nseg + sum(lacing)
        raw = bytearray(data[pos:end])
        raw[22:26] = b"\0\0\0\0"
        body = data[pos + 27 + nseg : end]
        packets: list[bytes] = []
        current = bytearray()
        offset = 0
        for value in lacing:
            current += body[offset : offset + value]
            offset += value
            if value < 255:
                packets.append(bytes(current))
                current = bytearray()
        pages.append(
            Page(
                flags=flags,
                granule=granule,
                serial=serial,
                sequence=sequence,
                crc=crc,
                crc_ok=crc_bitwise(bytes(raw)) == crc,
                packets=packets,
                continued_last=bool(current),
            )
        )
        pos = end
    return pages


def data_packets(data: bytes) -> list[bytes]:
    """The audio packets of an Ogg Opus file (the two header pages
    skipped), checking every page's CRC on the way."""
    pages = parse_pages(data)
    assert all(p.crc_ok for p in pages)
    return [pkt for page in pages[2:] for pkt in page.packets]

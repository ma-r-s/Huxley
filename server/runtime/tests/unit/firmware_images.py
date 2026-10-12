"""Fake ESP32 app images for the firmware tests.

Only the header fields the server reads are real: the image magic, the
app description's magic word, its version string at 0x30, and the ELF's
SHA-256 at 0xB0. The ELF is any bytes; its hash is what matters.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def make_image(
    directory: Path,
    version: str,
    *,
    size: int = 4096,
    embedded_version: str | None = None,
    elf: bytes | None = None,
    write_elf: bool = True,
) -> str:
    """Write `<version>.bin` (and `.elf`) into `directory`; returns the
    elf_sha (9 hex) the image embeds, as a device running it reports."""
    elf_bytes = elf if elf is not None else b"\x7fELF" + version.encode() * 16
    digest = hashlib.sha256(elf_bytes).digest()
    image = bytearray(max(size, 0xD0))
    image[0] = 0xE9
    image[0x20:0x24] = bytes.fromhex("3254cdab")
    name = (embedded_version if embedded_version is not None else version).encode()[:31]
    image[0x30 : 0x30 + len(name)] = name
    image[0xB0:0xD0] = digest
    for i in range(0xD0, len(image)):
        image[i] = i % 251  # a recognisable body, so truncation shows
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{version}.bin").write_bytes(bytes(image))
    if write_elf:
        (directory / f"{version}.elf").write_bytes(elf_bytes)
    return digest.hex()[:9]

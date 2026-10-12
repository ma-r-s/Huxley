#!/usr/bin/env python3
"""Build the bench clips the firmware streams instead of its mic.

The serial console's `say <n>` holds talk while clip n replaces the mic,
so a whole turn runs against the server with nobody speaking (nothing is
played or recorded on this Mac: `say -o` renders to a file). Clips are
PCM16 mono 24 kHz, the wire format, in the `storage` partition:

    "HXCL" u16 version u16 count, then count x {u32 offset, u32 bytes},
    then the PCM (offsets from the partition start, little-endian).

Usage (macOS: `say` and ffmpeg):
    python3 tools/clips.py                 # writes build/clips.bin
    esptool.py --chip esp32s3 -p PORT write_flash 0x420000 build/clips.bin

Edit CLIPS to change what the board "says"; the index is the `say` number.
"""

import pathlib
import struct
import subprocess
import sys
import tempfile

VOICE = "Paulina"  # es_MX; Abuelo's persona speaks Spanish
STORAGE_OFFSET = 0x420000  # partitions.csv: storage
STORAGE_BYTES = 0x1C0000
CLIPS = [
    "¿Qué hora es?",  # 0: a short answer
    "Pon el volumen al ochenta.",  # 1: set_volume
    "Pon el volumen al cuarenta.",  # 2: set_volume
    "Cuéntame un cuento largo, de tres minutos, sobre un gato que vive en un faro.",  # 3: long reply
    "Pon la radio.",  # 4: a stream
    "Para la radio.",  # 5: stop the stream
    "¿Qué día es hoy?",  # 6
]
TAIL_SILENCE_MS = 200


def render(text: str, tmp: pathlib.Path) -> bytes:
    aiff = tmp / "clip.aiff"
    raw = tmp / "clip.raw"
    subprocess.run(["say", "-v", VOICE, "-o", str(aiff), text], check=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(aiff),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-f",
            "s16le",
            str(raw),
        ],
        check=True,
    )
    pcm = raw.read_bytes()
    return pcm + bytes(24000 * 2 * TAIL_SILENCE_MS // 1000)


def main() -> None:
    out = pathlib.Path(__file__).resolve().parent.parent / "build" / "clips.bin"
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        pcms = [render(t, pathlib.Path(d)) for t in CLIPS]
    header = struct.pack("<4sHH", b"HXCL", 1, len(pcms))
    offset = len(header) + 8 * len(pcms)
    table, data = b"", b""
    for pcm in pcms:
        table += struct.pack("<II", offset + len(data), len(pcm))
        data += pcm
    image = header + table + data
    if len(image) > STORAGE_BYTES:
        sys.exit(f"clips.bin is {len(image)} bytes, the storage partition holds {STORAGE_BYTES}")
    out.write_bytes(image)
    for i, (text, pcm) in enumerate(zip(CLIPS, pcms)):
        print(f"  say {i}: {len(pcm) / 48000:4.1f} s  {text}")
    print(f"wrote {out} ({len(image)} bytes)")
    print(f"flash: esptool.py --chip esp32s3 -p PORT write_flash {STORAGE_OFFSET:#x} {out}")


if __name__ == "__main__":
    main()

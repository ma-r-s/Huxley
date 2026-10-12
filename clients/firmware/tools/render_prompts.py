#!/usr/bin/env python3
"""Render the spoken prompts the walkie says on its own (F-0027).

The walkie can't ask the server to speak when there is no server (no
connection, a battery about to cut off), so these few sentences live
inside the firmware: rendered once here with the persona's voice, encoded
with the server's own Opus encoder (24 kHz mono, 60 ms packets: exactly
what the walkie already decodes from the WebSocket), and embedded by the
`hux_prompts` component. An update over the air carries them.

Each prompt is checked before it is written: decoded back and transcribed,
the words must match the text. Nothing is played on this Mac.

File format (`.hxp`, little-endian): "HXP1", then per packet a u16 length
and the raw Opus packet. `hux_prompt_parse.c` reads it.

Usage (needs HUXLEY_OPENAI_API_KEY, read from server/runtime/.env):
    uv run --package huxley python clients/firmware/tools/render_prompts.py
    uv run --package huxley python clients/firmware/tools/render_prompts.py offline

Then build and flash; commit the .hxp files (the build needs no API key).
To change what the walkie says, edit PROMPTS and the enum in
components/hux_prompts/include/hux_prompts.h together.
"""

from __future__ import annotations

import io
import os
import pathlib
import re
import struct
import sys
import unicodedata
import wave

from openai import OpenAI

from huxley.server.opus_codec import SAMPLE_RATE, OpusDecoderStream, OpusEncoderStream

ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = ROOT / "clients/firmware/components/hux_prompts/prompts"
ENV = ROOT / "server/runtime/.env"

# Abuelo's voice (server/personas/abuelos/persona.yaml). The order here is
# the firmware enum's.
VOICE = "coral"
MODEL = "gpt-4o-mini-tts"
INSTRUCTIONS = "Habla en español, despacio, claro y con calidez."
PROMPTS = {
    "offline": "No tengo conexión. Lo sigo intentando.",
    "battery_empty": "Batería agotada. Ponme a cargar.",
}
MAGIC = b"HXP1"
EDGE_MS = 80  # silence kept before and after the words
SILENT = 300  # |sample| under this counts as silence when trimming


def api_key() -> str:
    key = os.environ.get("HUXLEY_OPENAI_API_KEY")
    if key:
        return key
    for line in ENV.read_text().splitlines():
        if line.startswith("HUXLEY_OPENAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit(f"no HUXLEY_OPENAI_API_KEY in the environment or {ENV}")


def trim(pcm: bytes) -> bytes:
    """Cut leading and trailing silence, keeping EDGE_MS at each end."""
    samples = memoryview(pcm).cast("h")
    loud = [i for i, s in enumerate(samples) if abs(s) >= SILENT]
    if not loud:
        sys.exit("the rendered audio is silent")
    edge = SAMPLE_RATE * EDGE_MS // 1000
    start = max(0, loud[0] - edge)
    end = min(len(samples), loud[-1] + edge)
    return pcm[start * 2 : end * 2]


def encode(pcm: bytes) -> list[bytes]:
    enc = OpusEncoderStream()
    packets = enc.push(pcm)
    tail = enc.flush()
    if tail is not None:
        packets.append(tail)
    return packets


def pack(packets: list[bytes]) -> bytes:
    out = bytearray(MAGIC)
    for p in packets:
        out += struct.pack("<H", len(p)) + p
    return bytes(out)


def unpack(blob: bytes) -> list[bytes]:
    if blob[:4] != MAGIC:
        raise ValueError("bad magic")
    off, packets = 4, []
    while off < len(blob):
        (n,) = struct.unpack_from("<H", blob, off)
        packets.append(blob[off + 2 : off + 2 + n])
        off += 2 + n
    return packets


def words(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.findall(r"[a-z]+", plain)


def check(client: OpenAI, name: str, blob: bytes, text: str) -> None:
    """Decode the packets back and transcribe them: the walkie must say
    the text, word for word."""
    dec = OpusDecoderStream()
    pcm = b"".join(dec.decode(p) for p in unpack(blob))
    wav = io.BytesIO()
    with wave.open(wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    wav.name = f"{name}.wav"
    wav.seek(0)
    heard = client.audio.transcriptions.create(
        model="gpt-4o-transcribe", file=wav, language="es"
    ).text
    if words(heard) != words(text):
        sys.exit(f"{name}: rendered {text!r} but it says {heard!r}")
    print(f"  check: heard {heard!r}")


def main() -> None:
    names = sys.argv[1:] or list(PROMPTS)
    unknown = [n for n in names if n not in PROMPTS]
    if unknown:
        sys.exit(f"unknown prompt(s) {unknown}; known: {list(PROMPTS)}")
    client = OpenAI(api_key=api_key())
    OUT.mkdir(parents=True, exist_ok=True)
    for name in names:
        text = PROMPTS[name]
        pcm = client.audio.speech.create(
            model=MODEL,
            voice=VOICE,
            input=text,
            instructions=INSTRUCTIONS,
            response_format="pcm",  # 24 kHz PCM16 mono, the wire format
        ).content
        pcm = trim(pcm)
        peak = max(abs(s) for s in memoryview(pcm).cast("h"))
        packets = encode(pcm)
        blob = pack(packets)
        ms = len(packets) * 60
        print(
            f"{name}: {text!r} {len(pcm) // 48} ms of speech, {len(packets)} packets "
            f"({ms} ms), {len(blob)} bytes, peak {peak} of 32767"
        )
        check(client, name, blob, text)
        (OUT / f"{name}.hxp").write_bytes(blob)


if __name__ == "__main__":
    main()

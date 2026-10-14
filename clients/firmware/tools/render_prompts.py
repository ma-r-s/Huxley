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

    uv run --package huxley python clients/firmware/tools/render_prompts.py --level-only
        (re-level the committed files to the loudness target, same takes)

Every prompt is levelled to -20 LUFS integrated with its true peak at most
-1.5 dBFS (T1.47: one loudness for everything the walkie plays; speech
and streams at -20, cues 3 dB under), by ffmpeg's two-pass loudnorm, and
measured again after the Opus round trip.

    uv run --package huxley python clients/firmware/tools/render_prompts.py --sounds
        (the connection sounds, F-0033: server/personas/_shared/sounds/device/*.wav,
        rendered elsewhere at their own cue level: encoded as they are, then
        checked after the Opus round trip: level, peak, the 4.5-6.5 kHz band)

Then build and flash; commit the .hxp files (the build needs no API key).
To change what the walkie says, edit PROMPTS and the enum in
components/hux_prompts/include/hux_prompts.h together.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import re
import struct
import subprocess
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
    "updating": "Me estoy actualizando. Intenta otra vez en un minuto.",
    "no_wifi": (
        "No encuentro el internet de tu teléfono. Revisa que el teléfono tenga batería "
        "y que la zona wifi esté prendida, o pídele ayuda a alguien."
    ),
    "no_internet": "Tu teléfono no me está dando internet. Revisa que tenga datos, o pídele ayuda a alguien.",
}
SOUNDS_DIR = ROOT / "server/personas/_shared/sounds/device"
SOUNDS = (
    "startup",
    "searching",
    "connected",
    "lost",
)  # F-0033, the firmware enum's order after PROMPTS
LOUDNESS_LUFS = -20.0  # T1.47: speech and streams
TRUE_PEAK_DB = -1.5
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


def ffmpeg(args: list[str], pcm: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-f",
            "s16le",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            "-i",
            "-",
            *args,
        ],
        input=pcm,
        capture_output=True,
        check=True,
    )


def level(pcm: bytes) -> bytes:
    """Two-pass loudnorm to LOUDNESS_LUFS, true peak TRUE_PEAK_DB, linear
    gain when the peak allows it."""
    target = f"I={LOUDNESS_LUFS}:TP={TRUE_PEAK_DB}:LRA=11"
    first = ffmpeg(["-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"], pcm)
    err = first.stderr.decode()
    stats = json.loads(err[err.rindex("{") : err.rindex("}") + 1])
    measured = (
        f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:linear=true"
    )
    out = ffmpeg(
        ["-af", f"loudnorm={target}:{measured}", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"], pcm
    )
    return out.stdout


def loudness(pcm: bytes) -> tuple[float, float]:
    """Integrated LUFS and true peak dBFS (ffmpeg's ebur128)."""
    run = ffmpeg(["-af", "ebur128=peak=true", "-f", "null", "-"], pcm)
    text = run.stderr.decode()
    summary = text[text.rindex("Summary:") :]
    lufs = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1))
    peak = float(re.search(r"Peak:\s+(-?[\d.inf]+) dBFS", summary).group(1))
    return lufs, peak


def band_rms_db(pcm: bytes) -> float:
    """RMS of the 4.5-6.5 kHz band (the startup's sparkle reaches 5.3 kHz)."""
    run = ffmpeg(
        ["-af", "highpass=f=4500,lowpass=f=6500,astats=metadata=0", "-f", "null", "-"], pcm
    )
    m = re.search(r"RMS level dB:\s+(-?[\d.inf]+)", run.stderr.decode())
    return float(m.group(1)) if m else float("-inf")


def peak_rms_db(pcm: bytes) -> tuple[float, float]:
    run = ffmpeg(["-af", "astats=metadata=0", "-f", "null", "-"], pcm)
    text = run.stderr.decode()
    peak = float(re.search(r"Peak level dB:\s+(-?[\d.inf]+)", text).group(1))
    rms = float(re.search(r"RMS level dB:\s+(-?[\d.inf]+)", text).group(1))
    return peak, rms


def sounds() -> None:
    """The connection sounds as they are (their level is the cue standard's),
    checked after the Opus round trip."""
    for name in SOUNDS:
        with wave.open(str(SOUNDS_DIR / f"{name}.wav"), "rb") as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
                sys.exit(f"{name}.wav: not 24 kHz mono PCM16")
            pcm = w.readframes(w.getnframes())
        blob = pack(encode(pcm))
        back = decoded(blob)[: len(pcm)]
        p0, r0 = peak_rms_db(pcm)
        p1, r1 = peak_rms_db(back)
        b0, b1 = band_rms_db(pcm), band_rms_db(back)
        print(
            f"{name}: {len(pcm) // 48} ms, {len(blob)} bytes; peak {p0:.1f} -> {p1:.1f} dBFS, "
            f"rms {r0:.1f} -> {r1:.1f}, 4.5-6.5 kHz {b0:.1f} -> {b1:.1f} dB"
        )
        if abs(r1 - r0) > 1.0 or p1 > -1.0 or (b0 > -60 and abs(b1 - b0) > 3.0):
            sys.exit(f"{name}: the Opus round trip changed it too much")
        (OUT / f"{name}.hxp").write_bytes(blob)


def decoded(blob: bytes) -> bytes:
    dec = OpusDecoderStream()
    return b"".join(dec.decode(p) for p in unpack(blob))


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
    pcm = decoded(blob)
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


def finish(client: OpenAI, name: str, text: str, pcm: bytes) -> None:
    """Level, encode, check the words and the loudness the walkie will play,
    write the file."""
    pcm = level(pcm)
    packets = encode(pcm)
    blob = pack(packets)
    lufs, peak = loudness(decoded(blob))
    print(
        f"{name}: {text!r} {len(pcm) // 48} ms, {len(packets)} packets, {len(blob)} bytes, "
        f"{lufs:.1f} LUFS, true peak {peak:.1f} dBFS (after Opus)"
    )
    if abs(lufs - LOUDNESS_LUFS) > 1.0 or peak > TRUE_PEAK_DB + 0.5:
        sys.exit(f"{name}: {lufs:.1f} LUFS / {peak:.1f} dBTP misses the target")
    check(client, name, blob, text)
    (OUT / f"{name}.hxp").write_bytes(blob)


def main() -> None:
    args = sys.argv[1:]
    if "--sounds" in args:
        OUT.mkdir(parents=True, exist_ok=True)
        sounds()
        return
    level_only = "--level-only" in args
    names = [a for a in args if a != "--level-only"] or list(PROMPTS)
    unknown = [n for n in names if n not in PROMPTS]
    if unknown:
        sys.exit(f"unknown prompt(s) {unknown}; known: {list(PROMPTS)}")
    client = OpenAI(api_key=api_key())
    OUT.mkdir(parents=True, exist_ok=True)
    for name in names:
        text = PROMPTS[name]
        if level_only:
            pcm = decoded((OUT / f"{name}.hxp").read_bytes())  # the same take
            before, _ = loudness(pcm)
            print(f"{name}: was {before:.1f} LUFS")
        else:
            pcm = client.audio.speech.create(
                model=MODEL,
                voice=VOICE,
                input=text,
                instructions=INSTRUCTIONS,
                response_format="pcm",  # 24 kHz PCM16 mono, the wire format
            ).content
            pcm = trim(pcm)
        finish(client, name, text, pcm)


if __name__ == "__main__":
    main()

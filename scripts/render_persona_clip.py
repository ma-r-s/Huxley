#!/usr/bin/env python3
"""Render a persona's spoken clips: sentences the server plays on its own
when the voice model can't answer (`unavailable_sound` in persona.yaml),
so they must exist before they're needed, recorded once in the persona's
voice. Same recipe as the walkie's own prompts
(clients/firmware/tools/render_prompts.py): OpenAI's speech model with the
persona's `voice`, silence trimmed, then transcribed back: the clip must
say the text, word for word, or nothing is written. Nothing is played on
this Mac.

Output: server/personas/<persona>/sounds/<name>.wav, PCM16 mono 24 kHz
(the wire format, played as is).

Usage (needs HUXLEY_OPENAI_API_KEY, read from server/runtime/.env):
    uv run --package huxley python scripts/render_persona_clip.py abuelos unavailable
To change what a persona says, edit CLIPS and render again; commit the WAV.
"""

from __future__ import annotations

import io
import os
import pathlib
import re
import sys
import unicodedata
import wave

import yaml
from openai import OpenAI

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV = ROOT / "server/runtime/.env"
SAMPLE_RATE = 24_000
MODEL = "gpt-4o-mini-tts"
EDGE_MS = 80  # silence kept before and after the words
SILENT = 300  # |sample| under this counts as silence when trimming

# persona -> clip name -> (text, speaking instructions)
CLIPS: dict[str, dict[str, tuple[str, str]]] = {
    "abuelos": {
        "unavailable": (
            "Ahora no puedo contestar. Ya avisé para que lo arreglen.",
            "Habla en español, despacio, claro y con calidez.",
        ),
    },
}


def api_key() -> str:
    key = os.environ.get("HUXLEY_OPENAI_API_KEY")
    if key:
        return key
    for line in ENV.read_text().splitlines():
        if line.startswith("HUXLEY_OPENAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit(f"no HUXLEY_OPENAI_API_KEY in the environment or {ENV}")


def trim(pcm: bytes) -> bytes:
    samples = memoryview(pcm).cast("h")
    loud = [i for i, s in enumerate(samples) if abs(s) >= SILENT]
    if not loud:
        sys.exit("the rendered audio is silent")
    edge = SAMPLE_RATE * EDGE_MS // 1000
    start = max(0, loud[0] - edge)
    end = min(len(samples), loud[-1] + edge)
    return pcm[start * 2 : end * 2]


def to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def words(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.findall(r"[a-z]+", plain)


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    persona, name = sys.argv[1], sys.argv[2]
    if name not in CLIPS.get(persona, {}):
        sys.exit(f"no clip {name!r} for {persona!r}; known: {CLIPS}")
    text, instructions = CLIPS[persona][name]
    spec = yaml.safe_load((ROOT / f"server/personas/{persona}/persona.yaml").read_text())
    voice, language = spec["voice"], spec.get("language_code", "en")
    client = OpenAI(api_key=api_key())
    pcm = client.audio.speech.create(
        model=MODEL,
        voice=voice,
        input=text,
        instructions=instructions,
        response_format="pcm",  # 24 kHz PCM16 mono
    ).content
    pcm = trim(pcm)
    wav = to_wav(pcm)
    probe = io.BytesIO(wav)
    probe.name = f"{name}.wav"
    heard = client.audio.transcriptions.create(
        model="gpt-4o-transcribe", file=probe, language=language
    ).text
    if words(heard) != words(text):
        sys.exit(f"{persona}/{name}: rendered {text!r} but it says {heard!r}")
    out = ROOT / f"server/personas/{persona}/sounds/{name}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(wav)
    peak = max(abs(s) for s in memoryview(pcm).cast("h"))
    print(f"{out.relative_to(ROOT)}: {len(pcm) // 48} ms, peak {peak} of 32767, heard {heard!r}")


if __name__ == "__main__":
    main()

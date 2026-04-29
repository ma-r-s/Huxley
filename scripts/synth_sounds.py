#!/usr/bin/env python3
"""Procedural earcon synthesis for Huxley.

Renders the shared earcon palette into ``server/personas/_shared/sounds/`` at
the format the audio channel expects: PCM16 / 24 kHz / mono. Run from repo
root::

    uv run --package huxley --group synth python scripts/synth_sounds.py

Aesthetic target: soft Japanese / Wii-bell / FM-synthesis tradition. Layered
Risset additive bell + Chowning FM bell, curved exponential envelopes, hall
reverb tail. See ``docs/sounds.md`` for the architecture and
``docs/research/sonic-ux.md`` for the design constraints (especially the
200 Hz - 4 kHz vocal-band masking rule that pushes our fundamentals into the
shimmer register).

Determinism: pure functions of (numpy, scipy, pedalboard) versions. No RNG
in the synthesis path. Same versions == byte-identical output. Versions are
pinned via ``uv.lock``.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray
from pedalboard import (
    Compressor,
    HighpassFilter,
    Pedalboard,
    Reverb,
)
from scipy.io import wavfile

if TYPE_CHECKING:
    from collections.abc import Callable

SAMPLE_RATE = 24_000  # Hz; matches huxley_sdk.audio expected format
F32 = NDArray[np.float32]
F64 = NDArray[np.float64]

# ln(10^(60/20)) — the exponential coefficient that takes a unit-amplitude
# signal to -60 dB over the time constant. Used for "decay to silence" envelopes.
_MINUS_60DB_NEPERS: float = 60.0 / 20.0 * math.log(10.0)  # ~6.9078
DEFAULT_PEAK_DBFS: float = -3.0  # final peak target for every rendered earcon


# ---------------------------------------------------------------------------
# Pitch
# ---------------------------------------------------------------------------

_SEMITONES_FROM_A: dict[str, int] = {
    "C": -9,
    "D": -7,
    "E": -5,
    "F": -4,
    "G": -2,
    "A": 0,
    "B": 2,
}


def note(name: str) -> float:
    """Convert ``"C5"``-style note name to frequency in Hz (A4 = 440)."""
    pitch_class = name[0].upper()
    accidental = 0
    rest = name[1:]
    if rest and rest[0] in "#b":
        accidental = 1 if rest[0] == "#" else -1
        rest = rest[1:]
    octave = int(rest)
    semitones = _SEMITONES_FROM_A[pitch_class] + accidental + 12 * (octave - 4)
    return 440.0 * 2.0 ** (semitones / 12.0)


# ---------------------------------------------------------------------------
# Envelopes — exponential curves only. Linear envelopes sound robotic; the
# curves match how acoustic instruments behave (Risset, Chowning).
# ---------------------------------------------------------------------------


def _exp_rise(n: int, curve: float = 5.0) -> F64:
    """Curved attack ramp 0 -> 1 over ``n`` samples. ``curve`` controls steepness."""
    if n <= 0:
        return np.empty(0, dtype=np.float64)
    return 1.0 - np.exp(-curve * np.linspace(0.0, 1.0, n, dtype=np.float64))


def _exp_fall(n: int, curve: float = 5.0) -> F64:
    """Curved release ramp 1 -> ~0 over ``n`` samples."""
    if n <= 0:
        return np.empty(0, dtype=np.float64)
    return np.exp(-curve * np.linspace(0.0, 1.0, n, dtype=np.float64))


def exp_decay(duration_s: float, curve: float = 5.0) -> F64:
    """Pure exponential decay 1 -> ~0 over the full duration. Bells live here."""
    n = int(duration_s * SAMPLE_RATE)
    return _exp_fall(n, curve=curve)


# ---------------------------------------------------------------------------
# Risset bell — additive synthesis with 11 inharmonic partials.
# Reference: Jean-Claude Risset's 1969 catalog (via Puckette MSP §4.8 and
# the CCRMA/SuperCollider formalizations of the table). Doubled partials at
# 0.56 and 0.92 with a 1-2 Hz detune produce the slow beating that gives
# bells their characteristic shimmer.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RissetPartial:
    freq_mult: float
    amp: float
    duration_mult: float
    detune_hz: float


_RISSET_PARTIALS: tuple[_RissetPartial, ...] = (
    _RissetPartial(0.56, 1.00, 1.000, 0.0),
    _RissetPartial(0.56, 0.67, 0.900, 1.0),
    _RissetPartial(0.92, 1.00, 0.650, 0.0),
    _RissetPartial(0.92, 1.80, 0.550, 1.7),
    _RissetPartial(1.19, 2.67, 0.325, 0.0),
    _RissetPartial(1.70, 1.67, 0.350, 0.0),
    _RissetPartial(2.00, 1.46, 0.250, 0.0),
    _RissetPartial(2.74, 1.33, 0.200, 0.0),
    _RissetPartial(3.00, 1.33, 0.150, 0.0),
    _RissetPartial(3.76, 1.00, 0.100, 0.0),
    _RissetPartial(4.07, 1.33, 0.075, 0.0),
)


def risset_bell(
    freq: float,
    duration_s: float,
    brightness: float = 1.0,
    attack_s: float = 0.005,
) -> F64:
    """Additive bell. ``brightness`` scales the per-partial decay times.

    Higher brightness = more partials linger = more shimmer; lower = darker,
    quicker collapse. Highs always decay faster than lows (the rule that
    separates "bell" from "organ"). Output peak normalized to ~0.9.

    A short ``attack_s`` ramp prevents a hard click at t=0 — without it, all
    11 partials sum at full amplitude on sample 1 and produce an audible tick
    on the strike. 5 ms matches the fm_bell default and the research target.
    """
    n = int(duration_s * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    out = np.zeros(n, dtype=np.float64)
    for p in _RISSET_PARTIALS:
        partial_freq = freq * p.freq_mult + p.detune_hz
        # Per-partial exponential decay; reaches -60dB at duration_mult*brightness*total.
        tau = p.duration_mult * brightness * duration_s
        env = np.exp(-_MINUS_60DB_NEPERS * t / max(tau, 1e-6))
        out += p.amp * env * np.sin(2.0 * np.pi * partial_freq * t)
    peak = float(np.max(np.abs(out)))
    if peak > 0:
        out *= 0.9 / peak
    attack_n = int(attack_s * SAMPLE_RATE)
    if attack_n > 0:
        out[:attack_n] *= _exp_rise(attack_n)
    return out


# ---------------------------------------------------------------------------
# Chowning FM bell — single carrier-modulator pair at non-integer ratio.
# 1:1.4 is the canonical "soft bell" ratio (Chowning 1973). Modulation index
# decays exponentially faster than amplitude — the bell gets duller as it
# rings, brightest at the strike.
# ---------------------------------------------------------------------------


def fm_bell(
    freq: float,
    duration_s: float,
    mod_ratio: float = 1.4,
    mod_index_peak: float = 6.0,
    mod_decay: float = 4.0,
    amp_decay: float = 4.6,
    attack_s: float = 0.010,
) -> F64:
    """Two-operator FM bell. ``mod_ratio`` 1.4 = soft bell, 3.5 = glockenspiel."""
    n = int(duration_s * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    # Modulation envelope decays faster than amplitude — highs die first.
    mod_env = np.exp(-mod_decay * t / duration_s)
    amp_env = np.exp(-amp_decay * t / duration_s)
    # Soft attack so we don't click.
    attack_n = int(attack_s * SAMPLE_RATE)
    if attack_n > 0:
        amp_env[:attack_n] *= _exp_rise(attack_n)
    modulator = mod_index_peak * mod_env * np.sin(2.0 * np.pi * freq * mod_ratio * t)
    return amp_env * np.sin(2.0 * np.pi * freq * t + modulator)


# ---------------------------------------------------------------------------
# Composition helpers
# ---------------------------------------------------------------------------


def chord(voices: list[F64], stagger_ms: float = 0.0) -> F64:
    """Mix voices with optional inter-voice stagger (slight arpeggio for liveness).

    Normalizes by ``1 / N`` (number of voices) rather than by peak so adding
    stagger doesn't silently rebalance per-voice loudness — peak-normalize
    would make a staggered chord 2x louder per-voice than an unstaggered one
    (because non-coincident voices never sum to a peak the normalizer would
    have to bring down). The downstream ``post()`` peak-normalizes to the
    final dBFS target, so absolute level here is decorative.
    """
    stagger = int(stagger_ms / 1000.0 * SAMPLE_RATE)
    total = max(len(v) + stagger * i for i, v in enumerate(voices))
    out = np.zeros(total, dtype=np.float64)
    for i, v in enumerate(voices):
        offset = i * stagger
        out[offset : offset + len(v)] += v
    return out / len(voices)


def mix(*layers: tuple[float, F64]) -> F64:
    """Sum ``(gain, signal)`` pairs, zero-padding shorter signals to the longest.

    Headroom contract: callers ensure the sum of gains times signal peaks
    stays below ~1.5 (``post()`` pre-attenuates by 0.5 to leave reverb room).
    Layered (body 0.7 + shimmer 0.5) on chord-normalized inputs is the
    canonical pattern; well within budget.
    """
    max_len = max(len(s) for _, s in layers)
    out = np.zeros(max_len, dtype=np.float64)
    for gain, sig in layers:
        out[: len(sig)] += gain * sig
    return out


def delayed(audio: F64, ms: float) -> F64:
    """Prepend ``ms`` of silence so layered voices can hit on a stagger-aligned beat."""
    n = int(ms / 1000.0 * SAMPLE_RATE)
    return np.concatenate([np.zeros(n, dtype=np.float64), audio])


def fade_out(audio: F64, ms: float = 15.0) -> F64:
    """Fade the trailing samples to zero. Prevents click on truncation."""
    n = int(ms / 1000.0 * SAMPLE_RATE)
    if n <= 0 or n >= len(audio):
        return audio
    out = audio.copy()
    out[-n:] *= _exp_fall(n)
    return out


# ---------------------------------------------------------------------------
# Post-processing: hall reverb + soft compression + limiter.
# Pedalboard's Reverb is a Freeverb derivative — tasteful, not Lexicon-class
# but in the right tier for under-2s earcons. HighpassFilter at 80 Hz strips
# any sub-rumble that would muddy small speakers.
# ---------------------------------------------------------------------------


def post(
    audio: F64,
    reverb_room: float = 0.85,
    reverb_damping: float = 0.4,
    reverb_wet: float = 0.30,
    reverb_dry: float = 0.75,
    peak_dbfs: float = DEFAULT_PEAK_DBFS,
    pad_tail_s: float = 0.6,
) -> F32:
    """Apply effects chain and return float32 mono ready for WAV write.

    ``pad_tail_s`` extends the buffer with silence so the reverb tail can
    bloom and decay rather than getting truncated at the dry signal end.

    Note on level control: pedalboard's ``Limiter`` is a soft-knee limiter
    backed by a hard clipper at 0 dBFS — its ``threshold_db`` controls where
    compression starts, not where the ceiling sits. To avoid surprise
    clipping we drop the limiter and normalize the chain output to
    ``peak_dbfs`` deterministically. Same end result, no clipper artifacts.
    """
    pad_n = int(pad_tail_s * SAMPLE_RATE)
    # Pre-attenuate to leave headroom for reverb buildup. The Reverb plugin
    # sums dry + wet which can add ~3-4 dB to the input peak before we can
    # rein it in.
    padded = (audio * 0.5).astype(np.float32)
    padded = np.concatenate([padded, np.zeros(pad_n, dtype=np.float32)])
    chain = Pedalboard(
        [
            HighpassFilter(cutoff_frequency_hz=80.0),
            Reverb(
                room_size=reverb_room,
                damping=reverb_damping,
                wet_level=reverb_wet,
                dry_level=reverb_dry,
                width=1.0,
            ),
            Compressor(threshold_db=-18.0, ratio=2.5, attack_ms=10.0, release_ms=200.0),
        ]
    )
    wet: F32 = chain(padded, sample_rate=SAMPLE_RATE)
    # Trim trailing samples below -60 dBFS so the file isn't padded with
    # near-silence indefinitely, but keep a tasteful 80 ms of floor for the
    # transition into model voice.
    abs_wet = np.abs(wet)
    floor = 10 ** (-60.0 / 20.0)
    above = np.where(abs_wet > floor)[0]
    if len(above) > 0:
        end = min(len(wet), above[-1] + int(0.08 * SAMPLE_RATE))
        wet = wet[:end]
    # Deterministic peak normalize to peak_dbfs, then fade trailing edge.
    peak = float(np.max(np.abs(wet)))
    if peak > 0:
        target_linear = 10.0 ** (peak_dbfs / 20.0)
        wet = wet * (target_linear / peak)
    return fade_out(wet.astype(np.float64), ms=15.0).astype(np.float32)


def write_wav(audio: F32, path: Path) -> None:
    """Quantize float audio to PCM16 and write a 24 kHz mono WAV."""
    clipped = np.clip(audio, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), SAMPLE_RATE, pcm16)


# ---------------------------------------------------------------------------
# Sound definitions — the actual palette.
#
# Pitch register notes:
# - Welcome / start chimes (book_start, radio_start) sit around C6-G6 to feel
#   warm and inviting. They play before content audio (book / radio stream),
#   not before model voice, so they can occupy the upper-vocal register
#   without masking concerns.
# - End / info chimes (book_end, news_start, search_start) sit above 1.5 kHz
#   in the shimmer register. They play immediately before LLM speech, and
#   must clear the 200 Hz - 4 kHz vocal band — see docs/research/sonic-ux.md.
# - All voicings are major or sus (no minor, no tritone): the soft-Japanese
#   palette is unambiguously pleasant.
# ---------------------------------------------------------------------------


def book_start() -> F32:
    """Warm welcome (~2.3 s): C6 + G6 perfect fifth, 70 ms stagger, lush tail.

    Plays before audiobook content, not before TTS, so the warm C6 (1047 Hz)
    register is fine. Lush wet (0.40) and longer pad give it gravitas without
    crossing into "cinematic stinger."
    """
    body = chord(
        [
            risset_bell(note("C6"), duration_s=1.6, brightness=1.0),
            risset_bell(note("G6"), duration_s=1.6, brightness=1.0),
        ],
        stagger_ms=70.0,
    )
    shimmer = chord(
        [
            fm_bell(note("C6"), duration_s=1.4, mod_ratio=1.4, mod_index_peak=4.0),
            fm_bell(note("G6"), duration_s=1.4, mod_ratio=1.4, mod_index_peak=4.5),
        ],
        stagger_ms=70.0,
    )
    mixed = mix((0.7, body), (0.5, shimmer))
    return post(
        mixed,
        reverb_room=0.78,  # was 0.88, then 0.82; landed inside the 2.5s RT60 ceiling at 0.78
        reverb_damping=0.35,
        reverb_wet=0.40,
        reverb_dry=0.70,
        pad_tail_s=0.9,
    )


def book_end() -> F32:
    """Gentle conclusion (~1.5 s): G6 + B6 major third, struck together.

    Plays *immediately before* the LLM's "the book just finished" narration,
    so all partials sit above the 1.5 kHz vocal-band threshold (G6 = 1568 Hz,
    B6 = 1976 Hz) — won't mask the first-word consonants of TTS.
    """
    body = chord(
        [
            risset_bell(note("G6"), duration_s=1.0, brightness=0.9),
            risset_bell(note("B6"), duration_s=1.0, brightness=0.9),
        ],
        stagger_ms=0.0,
    )
    shimmer = fm_bell(note("B6"), duration_s=0.9, mod_ratio=1.4, mod_index_peak=3.5)
    mixed = mix((0.7, body), (0.4, shimmer))
    return post(
        mixed,
        reverb_room=0.78,
        reverb_damping=0.45,
        reverb_wet=0.32,
        reverb_dry=0.75,
        pad_tail_s=0.7,
    )


def news_start() -> F32:
    """Sus2 stab (~0.9 s): G6 + A6 + D7, brief and alert.

    Tightened from a 1.3 s body+pad to land closer to "alert tag" than
    "decoration." Sus2 voicing (1-2-5) reads as neutral pleasant — appropriate
    for "here's the news" without committing to celebratory or somber.
    """
    body = chord(
        [
            risset_bell(note("G6"), duration_s=0.55, brightness=0.85),
            risset_bell(note("A6"), duration_s=0.55, brightness=0.85),
            risset_bell(note("D7"), duration_s=0.55, brightness=0.80),
        ],
        stagger_ms=0.0,
    )
    shimmer = fm_bell(note("D7"), duration_s=0.5, mod_ratio=1.4, mod_index_peak=3.0)
    mixed = mix((0.7, body), (0.35, shimmer))
    return post(
        mixed,
        reverb_room=0.75,
        reverb_damping=0.45,
        reverb_wet=0.28,
        reverb_dry=0.78,
        pad_tail_s=0.4,
    )


def radio_start() -> F32:
    """Tuning in (~1.4 s): C6 -> G6 -> C7 FM arpeggio + Risset accent on the C7 apex.

    Three FM bells ascending give the "dial sweep" feel; a Risset bell aligned
    with the apex C7 (160 ms in = 2 stagger steps) gives the final note the
    bell-body other earcons in the palette have, so radio_start doesn't sound
    thinner than its siblings.
    """
    arpeggio = chord(
        [
            fm_bell(note("C6"), duration_s=0.7, mod_ratio=1.4, mod_index_peak=3.5),
            fm_bell(note("G6"), duration_s=0.7, mod_ratio=1.4, mod_index_peak=3.5),
            fm_bell(note("C7"), duration_s=0.7, mod_ratio=1.4, mod_index_peak=3.5),
        ],
        stagger_ms=80.0,
    )
    apex = delayed(
        risset_bell(note("C7"), duration_s=0.7, brightness=0.85),
        ms=160.0,  # 2 * stagger_ms — lines up with the C7 FM strike
    )
    mixed = mix((0.7, arpeggio), (0.35, apex))
    return post(
        mixed,
        reverb_room=0.78,
        reverb_damping=0.45,
        reverb_wet=0.30,
        reverb_dry=0.78,
        pad_tail_s=0.7,
    )


def search_start() -> F32:
    """Single bright pluck (~0.7 s): E7 FM bell + brief Risset shimmer. "On it."

    Trimmed pad and wet vs the original draft so the chime feels like a quick
    "looking it up" tag, not a 1-second decoration. Highest register in the
    palette (E7 = 2637 Hz) — sits well above the vocal band, will not mask
    the LLM's "let me check" pre-narration that follows.
    """
    voice = fm_bell(
        note("E7"),
        duration_s=0.5,
        mod_ratio=1.4,
        mod_index_peak=4.0,
        amp_decay=5.5,
    )
    shimmer = risset_bell(note("E7"), duration_s=0.5, brightness=0.7)
    mixed = mix((0.7, voice), (0.4, shimmer))
    return post(
        mixed,
        reverb_room=0.72,
        reverb_damping=0.5,
        reverb_wet=0.20,  # was 0.25; drier reverb keeps the "quick ping" character
        reverb_dry=0.80,
        pad_tail_s=0.3,  # was 0.5; tail no longer outlasts the strike
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED_SOUNDS_DIR = REPO_ROOT / "server" / "personas" / "_shared" / "sounds"

PALETTE: dict[str, Callable[[], F32]] = {
    "book_start": book_start,
    "book_end": book_end,
    "news_start": news_start,
    "radio_start": radio_start,
    "search_start": search_start,
}


def main() -> int:
    out_dir = SHARED_SOUNDS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, fn in PALETTE.items():
        audio = fn()
        path = out_dir / f"{name}.wav"
        write_wav(audio, path)
        dur = len(audio) / SAMPLE_RATE
        peak = float(np.max(np.abs(audio)))
        peak_db = 20.0 * np.log10(max(peak, 1e-9))
        print(f"  {name:14s}  {path.relative_to(REPO_ROOT)}  {dur:.2f}s  peak={peak_db:+.1f}dB")
    print(f"\nrendered {len(PALETTE)} earcons to {out_dir.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

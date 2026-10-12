"""Opus audio for clients that ask for it (`?audio=opus`).

The wire design follows xiaozhi-esp32 (binary protocol version 1): each
binary WebSocket message is exactly one raw Opus packet, no header, and
the server's `hello` announces the parameters as `audio_params`. See
docs/protocol.md "Opus audio".

This module holds the codec state only: PCM in, packets out, and back.
`AudioServer` owns when to flush, reset and send. libopus is loaded
lazily, on the first opus connection, so a server without it still
serves PCM clients (and logs why an opus client got PCM).
"""

from __future__ import annotations

from typing import Any

# The stream every Huxley client speaks: PCM16 little-endian, 24 kHz, mono.
SAMPLE_RATE = 24000
CHANNELS = 1
FRAME_MS = 60
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 1440
FRAME_BYTES = FRAME_SAMPLES * 2 * CHANNELS  # 2880
# Outbound carries music as well as speech, hence APPLICATION_AUDIO; 32
# kbit/s VBR is ~240 bytes per 60 ms packet (~4 KB/s against 48 KB/s PCM).
BITRATE = 32000
# Longest packet Opus allows is 120 ms. A decode buffer this big never
# truncates, whatever frame size a client picks.
_MAX_DECODE_SAMPLES = SAMPLE_RATE * 120 // 1000

# xiaozhi's field names, sent in `hello.audio_params`. Both directions.
AUDIO_PARAMS: dict[str, Any] = {
    "format": "opus",
    "sample_rate": SAMPLE_RATE,
    "channels": CHANNELS,
    "frame_duration": FRAME_MS,
}


class OpusUnavailableError(Exception):
    """libopus (or the opuslib_next binding) could not be loaded."""


_lib: Any = None
_lib_error: str | None = None


def _load() -> Any:
    """The opuslib_next module, imported once. It raises a bare
    `Exception` at import time when the system libopus is missing, so
    any failure here means "no opus on this host"."""
    global _lib, _lib_error
    if _lib is not None:
        return _lib
    if _lib_error is not None:
        raise OpusUnavailableError(_lib_error)
    try:
        # Lazy: a PCM-only host needs no libopus.
        import opuslib_next
    except Exception as exc:
        _lib_error = f"{type(exc).__name__}: {exc}"
        raise OpusUnavailableError(_lib_error) from exc
    _lib = opuslib_next
    return _lib


def opus_unavailable_reason() -> str | None:
    """None when libopus loads; otherwise why it does not."""
    try:
        _load()
    except OpusUnavailableError as exc:
        return str(exc)
    return None


class OpusEncoderStream:
    """PCM16 in, whole 60 ms Opus packets out.

    PCM accumulates until a full frame is there; `flush()` zero-pads the
    remainder so the tail of a sound is not stranded. `reset()` drops the
    remainder and returns the encoder to a fresh state (OPUS_RESET_STATE),
    so audio after a barge-in does not carry the cut-off sound's state.
    """

    def __init__(self) -> None:
        lib = _load()
        self._enc = lib.Encoder(SAMPLE_RATE, CHANNELS, lib.APPLICATION_AUDIO)
        self._enc.bitrate = BITRATE
        self._enc.vbr = 1
        self._pending = bytearray()

    @property
    def pending_bytes(self) -> int:
        """PCM bytes waiting for the rest of their frame."""
        return len(self._pending)

    def push(self, pcm: bytes) -> list[bytes]:
        """Add PCM; return a packet for every whole frame now available."""
        self._pending += pcm
        frames = len(self._pending) // FRAME_BYTES
        packets = [
            self._encode(bytes(self._pending[i * FRAME_BYTES : (i + 1) * FRAME_BYTES]))
            for i in range(frames)
        ]
        del self._pending[: frames * FRAME_BYTES]
        return packets

    def flush(self) -> bytes | None:
        """Encode the partial frame, zero-padded to 60 ms. None if empty."""
        if not self._pending:
            return None
        frame = bytes(self._pending) + bytes(FRAME_BYTES - len(self._pending))
        self._pending.clear()
        return self._encode(frame)

    def reset(self) -> int:
        """Drop the partial frame and reset the encoder. Returns the PCM
        bytes dropped."""
        dropped = len(self._pending)
        self._pending.clear()
        self._enc.reset_state()
        return dropped

    def _encode(self, frame: bytes) -> bytes:
        return bytes(self._enc.encode(frame, FRAME_SAMPLES))


class OpusDecoderStream:
    """One Opus packet in, PCM16 out. Raises on a packet libopus rejects."""

    def __init__(self) -> None:
        lib = _load()
        self._dec = lib.Decoder(SAMPLE_RATE, CHANNELS)

    def decode(self, packet: bytes) -> bytes:
        if not packet:
            # libopus reads an empty packet as "lost" and returns 120 ms of
            # concealment. A client never means that; refuse it.
            raise ValueError("empty packet")
        return bytes(self._dec.decode(packet, _MAX_DECODE_SAMPLES))

    def reset(self) -> None:
        self._dec.reset_state()

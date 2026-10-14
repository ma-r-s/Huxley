"""The waiting sound (T1.41): silence while the server works reads as
"it's broken" to a blind listener; a soft blip fills it, and it never
plays over real sound, never cuts a send short, and never runs forever."""

from __future__ import annotations

import asyncio
import struct

from huxley.turn.wait_cue import BLIP_MS, WaitCue, render_blip

OPUS_FRAME_BYTES = 24_000 * 60 // 1000 * 2  # 60 ms of PCM16 mono 24 kHz


def test_blip_is_whole_opus_frames_soft_and_clickless() -> None:
    blip = render_blip()
    assert len(blip) == 24_000 * BLIP_MS // 1000 * 2
    assert len(blip) % OPUS_FRAME_BYTES == 0  # no partial frame left behind
    samples = struct.unpack(f"<{len(blip) // 2}h", blip)
    peak = max(abs(s) for s in samples)
    # About -9 dBFS: -26 LUFS momentary max (T1.47), 7 dB under a chime.
    assert 32767 * 10 ** (-10 / 20) < peak <= 32767 * 10 ** (-8 / 20)
    assert abs(samples[0]) < 100 and samples[-1] == 0  # no click at either end


class _Sink:
    def __init__(self, delay: float = 0.0) -> None:
        self.sent: list[bytes] = []
        self.finished = 0
        self.delay = delay

    async def send(self, pcm: bytes) -> None:
        self.sent.append(pcm)
        await asyncio.sleep(self.delay)
        self.finished += 1


async def test_plays_only_after_the_client_is_quiet_then_repeats() -> None:
    sink = _Sink()
    left = [0.05]  # the client still has 50 ms of the model's speech
    cue = WaitCue(sink.send, lambda: left[0], blip=b"\x01\x00", period_s=0.03)
    cue.arm("tool", 0.05)
    await asyncio.sleep(0.08)
    assert sink.sent == []  # not over the speech, not before the quiet
    await asyncio.sleep(0.1)
    assert len(sink.sent) >= 2
    cue.disarm("speech")
    n = len(sink.sent)
    await asyncio.sleep(0.08)
    assert len(sink.sent) == n  # nothing after disarm
    assert not cue.armed


async def test_disarm_before_the_threshold_plays_nothing() -> None:
    sink = _Sink()
    cue = WaitCue(sink.send, lambda: 0.0, blip=b"\x01\x00", period_s=0.03)
    cue.arm("reply", 0.05)
    await asyncio.sleep(0.02)
    cue.disarm("speech")
    await asyncio.sleep(0.1)
    assert sink.sent == []


async def test_disarm_mid_send_lets_that_send_finish_and_sends_no_more() -> None:
    """Cancelling a send mid-drain clears the walkie's shared Opus queue,
    taking the answer's first packets with it (critic, 2026-10-12)."""
    sink = _Sink(delay=0.05)
    cue = WaitCue(sink.send, lambda: 0.0, blip=b"\x01\x00", period_s=0.01)
    cue.arm("tool", 0.0)
    await asyncio.sleep(0.02)  # the first blip is being sent
    assert len(sink.sent) == 1 and sink.finished == 0
    cue.disarm("speech")
    await asyncio.sleep(0.1)
    assert sink.finished == 1  # it finished
    assert len(sink.sent) == 1  # and nothing followed


async def test_it_stops_by_itself_at_the_cap() -> None:
    sink = _Sink()
    cue = WaitCue(sink.send, lambda: 0.0, blip=b"\x01\x00", period_s=0.01, cap_s=0.05)
    cue.arm("tool", 0.0)
    await asyncio.sleep(0.15)
    n = len(sink.sent)
    await asyncio.sleep(0.05)
    assert 0 < n == len(sink.sent)
    assert not cue.armed


async def test_rearming_restarts_the_count() -> None:
    sink = _Sink()
    cue = WaitCue(sink.send, lambda: 0.0, blip=b"\x01\x00", period_s=0.03)
    cue.arm("reply", 0.06)
    await asyncio.sleep(0.04)
    cue.arm("tool", 0.06)  # counts again from now
    await asyncio.sleep(0.04)
    assert sink.sent == []
    await asyncio.sleep(0.05)
    assert sink.sent
    cue.disarm("done")

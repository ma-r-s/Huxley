"""Loudness measurement, the peak limiter and the stream leveler (T1.47).

Reference values for the measurement come from ffmpeg's `ebur128` filter
(an independent BS.1770 implementation) run on the same signals, which
these tests regenerate sample for sample.
"""

from __future__ import annotations

import math
import random
from array import array

import pytest

from huxley.loudness import (
    MAX_BOOST_DB,
    PROGRAM_TARGET_LUFS,
    STREAM_CEILING_DBFS,
    UNKNOWN_START_GAIN_DB,
    WIRE_CEILING_DBFS,
    LevelMemory,
    PeakLimiter,
    StreamLeveler,
    db_to_gain,
    integrated_lufs,
    momentary_max_lufs,
    pcm_bytes,
    pcm_samples,
    sample_peak_dbfs,
    scale_pcm,
    true_peak_dbfs,
)

RATE = 24_000
CHUNK = 4800  # 100 ms of PCM16, the size radio and Spotify read


def _pcm(values: list[float]) -> bytes:
    return pcm_bytes(array("h", [max(-32768, min(32767, round(v))) for v in values]))


def _sine(hz: float, lufs: float, seconds: float) -> bytes:
    # A full-scale sine reads -3.01 LUFS at 997 Hz (BS.1770's calibration).
    amp = 32768 * 10 ** ((lufs + 3.0103) / 20)
    return _pcm([amp * math.sin(2 * math.pi * hz * i / RATE) for i in range(int(RATE * seconds))])


def _white(seed: int = 47, sigma: float = 3000.0, seconds: float = 5.0) -> bytes:
    rng = random.Random(seed)
    return _pcm([rng.gauss(0, sigma) for _ in range(int(RATE * seconds))])


def _pink(seed: int = 48, scale: float = 1500.0, seconds: float = 5.0) -> bytes:
    """Paul Kellet's pink filter over seeded white noise."""
    rng = random.Random(seed)
    b = [0.0] * 7
    out = []
    for _ in range(int(RATE * seconds)):
        w = rng.gauss(0, 1)
        b[0] = 0.99886 * b[0] + w * 0.0555179
        b[1] = 0.99332 * b[1] + w * 0.0750759
        b[2] = 0.96900 * b[2] + w * 0.1538520
        b[3] = 0.86650 * b[3] + w * 0.3104856
        b[4] = 0.55000 * b[4] + w * 0.5329522
        b[5] = -0.7616 * b[5] - w * 0.0168980
        out.append((b[0] + b[1] + b[2] + b[3] + b[4] + b[5] + b[6] + w * 0.5362) * scale)
        b[6] = w * 0.115926
    return _pcm(out)


def _at(pcm: bytes, lufs: float) -> bytes:
    return scale_pcm(pcm, lufs - integrated_lufs(pcm))


def _chunks(pcm: bytes, size: int = CHUNK) -> list[bytes]:
    return [pcm[i : i + size] for i in range(0, len(pcm), size)]


def _peak(pcm: bytes) -> int:
    s = pcm_samples(pcm)
    return max(max(s), -min(s))


class TestMeasurement:
    @pytest.mark.parametrize(
        ("hz", "expected"),
        # ffmpeg ebur128 on the same files: the K-weighting's shape.
        [(997.0, -20.0), (100.0, -21.8), (5000.0, -16.6)],
    )
    def test_sine_integrated(self, hz: float, expected: float) -> None:
        assert integrated_lufs(_sine(hz, -20.0, 3.0)) == pytest.approx(expected, abs=0.2)

    def test_white_noise_matches_ffmpeg(self) -> None:
        assert integrated_lufs(_white()) == pytest.approx(-17.8, abs=0.2)

    def test_pink_noise_matches_ffmpeg(self) -> None:
        assert integrated_lufs(_pink()) == pytest.approx(-18.2, abs=0.2)

    def test_gain_moves_loudness_one_for_one(self) -> None:
        pink = _pink()
        assert integrated_lufs(scale_pcm(pink, -6.0)) == pytest.approx(
            integrated_lufs(pink) - 6.0, abs=0.05
        )

    def test_silence_and_too_short(self) -> None:
        assert integrated_lufs(bytes(RATE * 2)) == -math.inf
        assert integrated_lufs(_sine(997.0, -20.0, 0.3)) == -math.inf

    def test_relative_gate_ignores_the_quiet_part(self) -> None:
        loud = _sine(997.0, -20.0, 3.0)
        quiet = _sine(997.0, -45.0, 3.0)  # 25 LU under: gated out
        assert integrated_lufs(loud + quiet) == pytest.approx(-20.0, abs=0.2)

    def test_momentary_max_of_a_short_burst(self) -> None:
        # 300 ms at -20 inside a 400 ms window: 3/4 of the energy (ffmpeg -21.2).
        burst = _sine(997.0, -20.0, 0.3) + bytes(2 * RATE * 7 // 10)
        assert momentary_max_lufs(burst) == pytest.approx(-21.25, abs=0.2)

    def test_momentary_max_finds_the_loudest_400_ms(self) -> None:
        clip = _sine(997.0, -30.0, 2.0) + _sine(997.0, -18.0, 0.5) + _sine(997.0, -30.0, 2.0)
        assert momentary_max_lufs(clip) == pytest.approx(-18.0, abs=0.3)

    def test_true_peak_sees_an_inter_sample_over(self) -> None:
        # A quarter-rate sine phased so every sample sits at 0.707 of its crest.
        amp = 20000.0
        pcm = _pcm([amp * math.sin(2 * math.pi * i / 4 + math.pi / 4) for i in range(RATE)])
        assert sample_peak_dbfs(pcm) == pytest.approx(
            20 * math.log10(amp * 0.7071 / 32768), abs=0.1
        )
        assert true_peak_dbfs(pcm) == pytest.approx(20 * math.log10(amp / 32768), abs=0.3)


class TestPeakLimiter:
    def test_quiet_audio_at_unity_is_returned_as_is(self) -> None:
        pcm = _sine(997.0, -30.0, 0.2)
        assert PeakLimiter().process(pcm) is pcm

    def test_gain_without_limiting_is_exact(self) -> None:
        pcm = _sine(997.0, -30.0, 0.2)
        out = PeakLimiter().process(pcm, db_to_gain(6.0))
        assert integrated_lufs(out) == pytest.approx(integrated_lufs(pcm) + 6.0, abs=0.05)

    @pytest.mark.parametrize("gain_db", [0.0, 7.0, 20.0])
    def test_never_exceeds_the_ceiling(self, gain_db: float) -> None:
        rng = random.Random(1)
        signals = [
            _white(seed=2, sigma=12000.0, seconds=2.0),
            _sine(150.0, -3.0, 1.0),
            # Impulses landing in the first millisecond of chunks.
            _pcm([(32767.0 if i % CHUNK < 4 else rng.gauss(0, 500)) for i in range(RATE)]),
        ]
        for pcm in signals:
            limiter = PeakLimiter()
            for size in (CHUNK, 960, 3001 * 2):
                for chunk in _chunks(pcm, size):
                    out = limiter.process(chunk, db_to_gain(gain_db))
                    assert _peak(out) <= limiter.ceiling

    def test_a_release_near_unity_never_lets_a_peak_over(self) -> None:
        # Peaks a hair over the ceiling (0.03 %) while the envelope releases
        # toward unity: snapping the release to 1.0 must not skip them.
        limiter = PeakLimiter()
        limiter.process(_sine(997.0, -1.0, 0.1))
        just_over = limiter.ceiling * 1.0003
        quiet = _pcm([(just_over if i % 24 == 12 else 100.0) for i in range(CHUNK // 2)])
        for _ in range(40):
            assert _peak(limiter.process(quiet)) <= limiter.ceiling

    def test_ramped_gain_never_exceeds_the_ceiling(self) -> None:
        limiter = PeakLimiter(STREAM_CEILING_DBFS)
        g = 1.0
        for chunk in _chunks(_white(seed=3, sigma=9000.0, seconds=3.0)):
            nxt = g * 1.3
            assert _peak(limiter.process(chunk, g, nxt)) <= limiter.ceiling
            g = nxt if nxt < 8 else 1.0

    def test_holds_then_releases_back_to_the_fast_path(self) -> None:
        limiter = PeakLimiter()
        loud = _sine(997.0, -1.0, 0.1)
        limiter.process(loud)
        ms, cut = limiter.take_stats()
        assert ms > 0
        assert cut > 0
        quiet = _sine(997.0, -40.0, 0.1)
        for _ in range(15):  # 1.5 s: release done
            limiter.process(quiet)
        assert limiter.process(quiet) is quiet

    def test_odd_trailing_byte_survives(self) -> None:
        pcm = _sine(997.0, -1.0, 0.01) + b"\x01"
        out = PeakLimiter().process(pcm, 2.0)
        assert len(out) == len(pcm)
        assert out[-1:] == b"\x01"

    def test_wire_ceiling_value(self) -> None:
        assert PeakLimiter().ceiling == int(32767 * db_to_gain(WIRE_CEILING_DBFS))


def _run(leveler: StreamLeveler, pcm: bytes) -> bytes:
    return b"".join(leveler.process(c) for c in _chunks(pcm))


class TestStreamLeveler:
    def test_converges_from_ten_db_under(self) -> None:
        # Source at -24 needs +4; an unknown key starts at -6: 10 dB short.
        source = _at(_pink(seconds=20.0), -24.0)
        leveler = StreamLeveler("k", LevelMemory())
        assert leveler.gain_db == UNKNOWN_START_GAIN_DB
        out = _run(leveler, source)
        assert integrated_lufs(out[-5 * 2 * RATE :]) == pytest.approx(PROGRAM_TARGET_LUFS, abs=0.5)
        assert _peak(out) <= int(32767 * db_to_gain(STREAM_CEILING_DBFS))

    def test_converges_from_ten_db_over(self) -> None:
        # Source at -16 needs -4; learned at +6 (say a quieter station
        # shared the key): 10 dB too loud at the start.
        memory = LevelMemory()
        memory.set("k", MAX_BOOST_DB)
        source = _at(_pink(seconds=12.0), -16.0)
        leveler = StreamLeveler("k", memory)
        assert leveler.learned
        out = _run(leveler, source)
        assert integrated_lufs(out[-5 * 2 * RATE :]) == pytest.approx(PROGRAM_TARGET_LUFS, abs=0.5)
        assert _peak(out) <= int(32767 * db_to_gain(STREAM_CEILING_DBFS))
        # Down at no more than 6 dB/s, never below what it needs.
        assert memory.get("k") == pytest.approx(-4.0, abs=0.5)

    def test_a_loud_source_is_cut_without_clipping(self) -> None:
        source = _at(_white(seconds=8.0, sigma=9000.0), -8.0)
        out = _run(StreamLeveler("k", LevelMemory()), source)
        assert integrated_lufs(out[-3 * 2 * RATE :]) == pytest.approx(PROGRAM_TARGET_LUFS, abs=0.5)

    def test_holds_its_gain_through_silence(self) -> None:
        leveler = StreamLeveler("k", LevelMemory())
        _run(leveler, _at(_pink(seconds=12.0), -16.0))
        before = leveler.gain_db
        _run(leveler, bytes(10 * 2 * RATE))
        # The windows straddling the edge still count; the silence doesn't.
        assert leveler.gain_db == pytest.approx(before, abs=0.2)
        out = _run(leveler, _at(_pink(seed=9, seconds=3.0), -16.0))
        assert integrated_lufs(out) == pytest.approx(PROGRAM_TARGET_LUFS, abs=0.5)

    def test_a_quiet_passage_is_not_pumped_up(self) -> None:
        leveler = StreamLeveler("k", LevelMemory())
        _run(leveler, _at(_pink(seconds=15.0), -16.0))
        before = leveler.gain_db
        # 14 dB quieter: under the relative gate.
        _run(leveler, _at(_pink(seed=5, seconds=10.0), -30.0))
        assert leveler.gain_db == pytest.approx(before, abs=0.2)

    def test_a_lasting_drop_is_followed(self) -> None:
        # 12 LU down for good (talk after a loud ad): past the relative
        # gate, which must not lock the gain where the loud part left it.
        leveler = StreamLeveler("k", LevelMemory())
        _run(leveler, _at(_pink(seconds=5.0), -9.0))
        assert leveler.gain_db == pytest.approx(-11.0, abs=0.5)
        out = _run(leveler, _at(_pink(seed=7, seconds=60.0), -21.0))
        assert leveler.gain_db > -2.5
        assert integrated_lufs(out[-10 * 2 * RATE :]) > -23.5

    def test_boost_is_capped(self) -> None:
        leveler = StreamLeveler("k", LevelMemory())
        _run(leveler, _at(_pink(seconds=15.0), -35.0))
        assert leveler.gain_db == pytest.approx(MAX_BOOST_DB, abs=0.01)

    def test_a_key_starts_where_it_left_off(self) -> None:
        memory = LevelMemory()
        first = StreamLeveler("radio:x", memory)
        _run(first, _at(_pink(seconds=12.0), -17.0))
        second = StreamLeveler("radio:x", memory)
        assert second.learned
        assert second.gain_db == pytest.approx(first.gain_db)
        assert StreamLeveler("radio:y", memory).gain_db == UNKNOWN_START_GAIN_DB

    def test_nothing_is_learned_before_a_measurement(self) -> None:
        memory = LevelMemory()
        leveler = StreamLeveler("k", memory)
        _run(leveler, _pink(seconds=0.3))  # under one 400 ms window
        assert memory.get("k") is None
        _run(StreamLeveler("q", memory), bytes(2 * RATE * 2))  # silence measures nothing
        assert memory.get("q") is None

    def test_gain_moves_slowly_up(self) -> None:
        leveler = StreamLeveler("k", LevelMemory())
        source = _at(_pink(seconds=2.0), -26.0)  # wants +6, starts at -6
        _run(leveler, source)
        # 2 s of audio, the first 400 ms before any estimate: at most 2 dB/s.
        assert leveler.gain_db <= UNKNOWN_START_GAIN_DB + 2.0 * 2.0 + 0.01

    def test_report_due_every_30_s_and_stats(self) -> None:
        leveler = StreamLeveler("k", LevelMemory())
        source = _at(_pink(seconds=1.0), -20.0)
        due = 0
        for _ in range(31):
            for c in _chunks(source):
                leveler.process(c)
                due += leveler.report_due()
        assert due == 1
        stats = leveler.stats()
        assert stats["key"] == "k"
        assert stats["program_lufs"] == pytest.approx(-20.0, abs=0.5)
        assert stats["audio_s"] == pytest.approx(31.0, abs=0.1)

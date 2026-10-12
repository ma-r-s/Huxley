"""The recorder: voices as Ogg Opus files, the rest of the speaker as a
timeline of `recorder.segment` log lines (docs/observability.md
"Recordings").

The segmenting is driven here with scripted control frames and packets,
on a fake clock, through the real writer thread; `stop()` drains it, so
every assertion runs on a finished timeline. `test_server_recording.py`
covers the AudioServer's taps.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
import structlog

from huxley.server import recorder as recorder_mod
from huxley.server.recorder import Recorder

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

# 2026-10-12 15:30:00 UTC.
_T0 = datetime(2026, 10, 12, 15, 30, 0, tzinfo=UTC).timestamp()


class _Clock:
    def __init__(self) -> None:
        self.wall_t = _T0
        self.mono_t = 1000.0

    def wall(self) -> float:
        return self.wall_t

    def mono(self) -> float:
        return self.mono_t

    def advance(self, s: float) -> None:
        self.wall_t += s
        self.mono_t += s


def _pkt(n: int, size: int = 60) -> bytes:
    """A 60 ms packet (TOC: SILK NB 60 ms, code 0) with a distinct body.
    The recorder never decodes; it reads the TOC for the duration."""
    return bytes([3 << 3]) + n.to_bytes(2, "big") + bytes(size - 3)


def _ogg_packets(path: Path) -> list[bytes]:
    """Data packets of an Ogg file (headers skipped)."""
    data = path.read_bytes()
    packets: list[bytes] = []
    pos = 0
    page = 0
    while pos < len(data):
        assert data[pos : pos + 4] == b"OggS"
        nseg = data[pos + 26]
        lacing = data[pos + 27 : pos + 27 + nseg]
        body = pos + 27 + nseg
        current = b""
        for value in lacing:
            current += data[body : body + value]
            body += value
            if value < 255:
                if page >= 2:
                    packets.append(current)
                current = b""
        pos = body
        page += 1
    return packets


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def logs() -> Iterator[list[dict[str, Any]]]:
    with structlog.testing.capture_logs() as captured:
        yield captured


def _recorder(tmp_path: Path, clock: _Clock, **kwargs: Any) -> Recorder:
    rec = Recorder(tmp_path, wall=clock.wall, mono=clock.mono, **kwargs)
    rec.start()
    rec.connection_opened("c1", "opus")
    return rec


def _segments(logs: list[dict[str, Any]], **match: Any) -> list[dict[str, Any]]:
    return [
        e
        for e in logs
        if e["event"] == "recorder.segment" and all(e.get(k) == v for k, v in match.items())
    ]


def _files(tmp_path: Path) -> list[Path]:
    root = tmp_path / "recordings"
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _speak(rec: Recorder, clock: _Clock, first: int, count: int) -> list[bytes]:
    sent = []
    for i in range(first, first + count):
        clock.advance(0.06)
        p = _pkt(i)
        rec.speaker_packet("c1", p)
        sent.append(p)
    return sent


def _talk(rec: Recorder, clock: _Clock, first: int, count: int) -> list[bytes]:
    sent = []
    for i in range(first, first + count):
        clock.advance(0.06)
        p = _pkt(i)
        rec.mic_packet("c1", p)
        sent.append(p)
    return sent


# --- One file per voice ------------------------------------------------------


def test_a_model_span_is_one_file(tmp_path: Path, clock: _Clock, logs: list[Any]) -> None:
    rec = _recorder(tmp_path, clock)
    rec.model_speaking(True)
    sent = _speak(rec, clock, 0, 5)
    rec.model_speaking(False)
    assert rec.stop()

    (seg,) = _segments(logs)
    assert seg["source"] == "model"
    assert seg["packets"] == 5
    assert seg["duration_ms"] == 300
    assert seg["bytes"] == sum(len(p) for p in sent)
    assert seg["end_reason"] == "model_done"
    assert seg["cut_by_clear"] is False
    assert seg["dropped"] == 0
    assert seg["conn_id"] == "c1"
    assert seg["start"] == "2026-10-12T15:30:00.060Z"
    assert seg["end"] == "2026-10-12T15:30:00.300Z"
    assert seg["path"] == "recordings/2026/10/12/153000-c1-001-model.ogg"
    (path,) = _files(tmp_path)
    assert path == tmp_path / seg["path"]
    assert _ogg_packets(path) == sent


def test_a_stream_is_a_timeline_line_only(tmp_path: Path, clock: _Clock, logs: list[Any]) -> None:
    rec = _recorder(tmp_path, clock)
    rec.stream_started("s1", "Radio Uno")
    rec.model_speaking(True)  # a stream holds the speaker flag too
    _speak(rec, clock, 0, 20)
    rec.stream_ended("s1", "interrupted")
    rec.model_speaking(False)
    assert rec.stop()

    (seg,) = _segments(logs)
    assert seg["source"] == "stream"
    assert seg["label"] == "Radio Uno"
    assert seg["stream_id"] == "s1"
    assert seg["packets"] == 20
    assert seg["duration_ms"] == 1200
    assert seg["end_reason"] == "interrupted"
    assert seg["path"] is None
    assert _files(tmp_path) == []


def test_a_claim_is_two_files_mic_and_peer(tmp_path: Path, clock: _Clock, logs: list[Any]) -> None:
    rec = _recorder(tmp_path, clock)
    rec.claim_started("claim-1", "comms", "Ana María")
    mic, peer = [], []
    for i in range(6):
        mic += _talk(rec, clock, 100 + i, 1)
        peer += _speak(rec, clock, 200 + i, 1)
    rec.claim_ended("claim-1", "natural")
    assert rec.stop()

    mic_seg = _segments(logs, source="mic")[0]
    peer_seg = _segments(logs, source="claim")[0]
    for seg in (mic_seg, peer_seg):
        assert seg["claim_id"] == "claim-1"
        assert seg["label"] == "Ana María"
        assert seg["end_reason"] == "natural"
        assert seg["packets"] == 6
    assert mic_seg["path"].endswith("-mic-ana-maria.ogg")
    assert peer_seg["path"].endswith("-claim-ana-maria.ogg")
    assert _ogg_packets(tmp_path / mic_seg["path"]) == mic
    assert _ogg_packets(tmp_path / peer_seg["path"]) == peer
    assert len(_files(tmp_path)) == 2


def test_audio_clear_cuts_a_model_segment(tmp_path: Path, clock: _Clock, logs: list[Any]) -> None:
    rec = _recorder(tmp_path, clock)
    rec.model_speaking(True)
    first = _speak(rec, clock, 0, 3)
    rec.audio_clear()
    second = _speak(rec, clock, 3, 2)  # the same reply goes on after the clear
    rec.model_speaking(False)
    assert rec.stop()

    cut, rest = _segments(logs, source="model")
    assert cut["cut_by_clear"] is True
    assert cut["end_reason"] == "audio_clear"
    assert cut["packets"] == 3
    assert rest["cut_by_clear"] is False
    assert rest["end_reason"] == "model_done"
    assert _ogg_packets(tmp_path / cut["path"]) == first
    assert _ogg_packets(tmp_path / rest["path"]) == second


def test_every_press_gets_a_mic_file_committed_or_not(
    tmp_path: Path, clock: _Clock, logs: list[Any]
) -> None:
    rec = _recorder(tmp_path, clock)
    rec.ptt(True)
    short = _talk(rec, clock, 0, 1)  # 60 ms: too short to ever commit
    rec.ptt(False)
    rec.ptt(True)
    long = _talk(rec, clock, 1, 10)
    rec.ptt(False)
    assert rec.stop()

    one, two = _segments(logs, source="mic")
    assert (one["packets"], two["packets"]) == (1, 10)
    assert one["end_reason"] == two["end_reason"] == "ptt_stop"
    assert one["label"] is None
    assert _ogg_packets(tmp_path / one["path"]) == short
    assert _ogg_packets(tmp_path / two["path"]) == long


def test_a_disconnect_closes_everything(tmp_path: Path, clock: _Clock, logs: list[Any]) -> None:
    rec = _recorder(tmp_path, clock)
    rec.ptt(True)
    _talk(rec, clock, 0, 3)
    rec.model_speaking(True)
    _speak(rec, clock, 10, 4)
    rec.connection_closed("c1", "disconnect")
    # Late packets of the dead connection are not recorded.
    _speak(rec, clock, 20, 2)
    # A new connection while the reply is still going: a new segment.
    rec.connection_opened("c2", "opus")
    clock.advance(0.06)
    rec.speaker_packet("c2", _pkt(30))
    rec.model_speaking(False)
    assert rec.stop()

    closed = _segments(logs, conn_id="c1")
    assert {s["source"] for s in closed} == {"mic", "model"}
    assert all(s["end_reason"] == "disconnect" for s in closed)
    assert [s["packets"] for s in closed if s["source"] == "model"] == [4]
    (after,) = _segments(logs, conn_id="c2")
    assert after["source"] == "model"
    assert after["packets"] == 1
    # Every file closed and renamed: no .part left behind.
    files = _files(tmp_path)
    assert len(files) == 3
    assert all(f.suffix == ".ogg" for f in files)


def test_shutdown_closes_open_segments(tmp_path: Path, clock: _Clock, logs: list[Any]) -> None:
    rec = _recorder(tmp_path, clock)
    rec.model_speaking(True)
    _speak(rec, clock, 0, 3)
    assert rec.stop()
    (seg,) = _segments(logs)
    assert seg["end_reason"] == "shutdown"
    assert seg["path"].endswith("-model.ogg")
    stopped = [e for e in logs if e["event"] == "recorder.stopped"]
    assert stopped == [
        {
            "event": "recorder.stopped",
            "log_level": "info",
            "segments": 1,
            "files": 1,
            "dropped": 0,
            "unattributed_drops": 0,
        }
    ]


# --- What the source inference has to absorb ----------------------------------


def test_a_streams_first_packets_before_its_announcement_join_the_stream(
    tmp_path: Path, clock: _Clock, logs: list[Any]
) -> None:
    """The pump writes its first chunk while the coordinator is still
    announcing the stream (seen in the 2026-10-12 log: model_speaking
    true 0.3 ms before stream_started). Those packets are the stream's,
    not a stray model file with an earcon in it."""
    rec = _recorder(tmp_path, clock)
    rec.model_speaking(True)
    _speak(rec, clock, 0, 2)
    rec.stream_started("s1", "Book")
    _speak(rec, clock, 2, 10)
    rec.stream_ended("s1", "natural")
    assert rec.stop()

    (seg,) = _segments(logs)
    assert seg["source"] == "stream"
    assert seg["packets"] == 12
    assert seg["retagged_from"] == "model"
    assert _files(tmp_path) == []  # the model file was dropped, .part included


def test_a_claims_peer_audio_before_its_announcement_stays_in_the_claim_file(
    tmp_path: Path, clock: _Clock, logs: list[Any]
) -> None:
    rec = _recorder(tmp_path, clock)
    rec.model_speaking(True)
    early = _speak(rec, clock, 0, 2)
    rec.claim_started("claim-1", "comms", "Ana")
    later = _speak(rec, clock, 2, 3)
    rec.claim_ended("claim-1", "user_ptt")
    rec.model_speaking(False)
    assert rec.stop()

    (seg,) = _segments(logs)
    assert seg["source"] == "claim"
    assert seg["retagged_from"] == "model"
    assert seg["path"].endswith("-claim-ana.ogg")
    assert _ogg_packets(tmp_path / seg["path"]) == early + later


def test_real_model_speech_before_a_stream_keeps_its_file(
    tmp_path: Path, clock: _Clock, logs: list[Any]
) -> None:
    """Past the retag window the model segment is real speech."""
    rec = _recorder(tmp_path, clock)
    rec.model_speaking(True)
    words = _speak(rec, clock, 0, 20)  # 1.2 s
    rec.stream_started("s1", "Book")
    _speak(rec, clock, 20, 5)
    rec.stream_ended("s1", "natural")
    assert rec.stop()

    model, stream = _segments(logs)
    assert model["source"] == "model"
    assert model["end_reason"] == "next_source"
    assert "retagged_from" not in model
    assert _ogg_packets(tmp_path / model["path"]) == words
    assert stream["source"] == "stream"
    assert stream["packets"] == 5


def test_sounds_are_timeline_only_and_close_when_idle(
    tmp_path: Path, clock: _Clock, logs: list[Any]
) -> None:
    rec = _recorder(tmp_path, clock)
    _speak(rec, clock, 0, 4)  # an earcon: nothing announced it
    clock.advance(3.0)
    rec.ptt(True)  # any later event: the earcon has been idle 3 s
    rec.ptt(False)
    _speak(rec, clock, 4, 2)  # another one
    assert rec.stop()

    first, second = _segments(logs, source="sound")
    assert first["end_reason"] == "idle"
    assert first["packets"] == 4
    assert first["path"] is None
    assert second["end_reason"] == "shutdown"
    assert _files(tmp_path) == []


def test_claim_mic_and_a_press_during_it_are_separate(
    tmp_path: Path, clock: _Clock, logs: list[Any]
) -> None:
    rec = _recorder(tmp_path, clock)
    rec.claim_started("claim-1", "comms", None)
    _talk(rec, clock, 0, 3)
    rec.ptt(True)  # the user presses during the claim
    _talk(rec, clock, 3, 2)
    rec.claim_ended("claim-1", "user_ptt")
    rec.ptt(False)
    assert rec.stop()

    claim_mic, press = _segments(logs, source="mic")
    assert claim_mic["claim_id"] == "claim-1"
    assert claim_mic["label"] == "comms"  # no title: the skill names it
    assert claim_mic["end_reason"] == "ptt_start"
    assert claim_mic["packets"] == 3
    assert "claim_id" not in press
    assert press["end_reason"] == "ptt_stop"


def test_invalid_packets_are_counted_not_written(
    tmp_path: Path, clock: _Clock, logs: list[Any]
) -> None:
    rec = _recorder(tmp_path, clock)
    rec.ptt(True)
    good = _talk(rec, clock, 0, 2)
    rec.mic_packet("c1", b"")
    rec.mic_packet("c1", b"\x03\x00")  # code 3, zero frames
    rec.ptt(False)
    assert rec.stop()
    (seg,) = _segments(logs)
    assert seg["packets"] == 2
    assert seg["invalid"] == 2
    assert _ogg_packets(tmp_path / seg["path"]) == good


def test_a_pcm_connection_is_not_recorded(tmp_path: Path, clock: _Clock, logs: list[Any]) -> None:
    rec = Recorder(tmp_path, wall=clock.wall, mono=clock.mono)
    rec.start()
    rec.connection_opened("p1", "pcm")
    rec.model_speaking(True)
    rec.speaker_packet("p1", _pkt(0))
    rec.model_speaking(False)
    assert rec.stop()
    assert _segments(logs) == []
    assert _files(tmp_path) == []


# --- Never in the way of audio -------------------------------------------------


def test_a_full_queue_drops_and_counts_and_never_raises(
    tmp_path: Path, clock: _Clock, logs: list[Any]
) -> None:
    rec = Recorder(tmp_path, max_queued_packets=3, wall=clock.wall, mono=clock.mono)
    rec.connection_opened("c1", "opus")
    rec.model_speaking(True)
    _speak(rec, clock, 0, 10)  # writer not started: the queue fills at 3
    assert rec.dropped_total == 7
    rec.model_speaking(False)  # control events are never dropped
    rec.start()
    assert rec.stop()

    (seg,) = _segments(logs)
    assert seg["packets"] == 3
    assert seg["dropped"] == 7
    assert seg["end_reason"] == "model_done"


def test_a_broken_queue_never_raises(tmp_path: Path, clock: _Clock) -> None:
    rec = Recorder(tmp_path, wall=clock.wall, mono=clock.mono)

    class _Broken:
        def put(self, _: object) -> None:
            raise RuntimeError("queue gone")

    rec._queue = _Broken()  # type: ignore[assignment]
    rec.speaker_packet("c1", _pkt(0))
    rec.model_speaking(True)
    assert rec.dropped_total == 2


def test_a_disk_error_is_logged_and_the_timeline_goes_on(
    tmp_path: Path, clock: _Clock, logs: list[Any]
) -> None:
    (tmp_path / "recordings").write_text("not a directory")  # mkdir fails
    rec = _recorder(tmp_path, clock)
    rec.model_speaking(True)
    _speak(rec, clock, 0, 3)
    rec.model_speaking(False)
    assert rec.stop()

    failed = [e for e in logs if e["event"] == "recorder.write_failed"]
    assert len(failed) == 1
    assert failed[0]["op"] == "open"
    (seg,) = _segments(logs)
    assert seg["path"] is None
    assert seg["packets"] == 3
    assert "error" in seg


def test_a_write_error_mid_file_keeps_the_part(
    tmp_path: Path, clock: _Clock, logs: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = recorder_mod.OggOpusWriter

    class _Flaky(real_open):  # type: ignore[misc,valid-type]
        def add(self, packet: bytes, samples: int) -> None:
            if packet == _pkt(2):
                raise OSError(28, "No space left on device")
            super().add(packet, samples)

    monkeypatch.setattr(recorder_mod, "OggOpusWriter", _Flaky)
    rec = _recorder(tmp_path, clock)
    rec.model_speaking(True)
    _speak(rec, clock, 0, 5)
    rec.model_speaking(False)
    assert rec.stop()

    (failed,) = [e for e in logs if e["event"] == "recorder.write_failed"]
    assert failed["op"] == "write"
    (seg,) = _segments(logs)
    assert seg["packets"] == 5  # still counted
    assert seg["path"].endswith(".ogg.part")
    assert "No space left" in seg["error"]


def test_stop_is_idempotent_and_later_calls_are_ignored(tmp_path: Path, clock: _Clock) -> None:
    rec = _recorder(tmp_path, clock)
    assert rec.stop()
    assert rec.stop()
    rec.speaker_packet("c1", _pkt(0))  # no-op, no raise
    assert not rec.is_running
    assert not any(t.name == "huxley-recorder" for t in threading.enumerate())


def test_detached_closes_what_is_open(tmp_path: Path, clock: _Clock, logs: list[Any]) -> None:
    rec = _recorder(tmp_path, clock)
    rec.model_speaking(True)
    _speak(rec, clock, 0, 2)
    rec.detached()
    _speak(rec, clock, 2, 1)  # a stray packet after: its own segment
    assert rec.stop()
    first, second = _segments(logs)
    assert first["end_reason"] == "recorder_replaced"
    assert first["packets"] == 2
    assert second["packets"] == 1


def test_iso_and_slug_helpers() -> None:
    assert recorder_mod._iso(_T0 + 0.5) == "2026-10-12T15:30:00.500Z"
    assert recorder_mod._iso(None) is None
    assert recorder_mod._slug("Ana María / Radio 1!") == "ana-maria-radio-1"
    assert recorder_mod._slug(None) == ""
    assert len(recorder_mod._slug("x" * 200)) == 40

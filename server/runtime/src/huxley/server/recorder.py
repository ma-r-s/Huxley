"""Recorder: every voice that crossed the wire as audio files, the rest of
the speaker as a timeline in the log.

Opt-in per persona (`recording: {enabled: true}` in persona.yaml). The
`AudioServer` feeds it from the two points where Opus crosses the wire,
so it records what the client actually got and sent:

- **speaker**: each Opus packet as `_drain_opus` writes it to the socket
  (audio queued and then dropped by `audio_clear` or a cancel never
  reaches the file, as it never reached the client);
- **mic**: each Opus packet the client sent, as it arrived, before any
  gating (a press too short or too quiet to commit is still kept).

PCM clients (dev only) are not recorded.

**Sources.** The speaker's source is inferred from the control frames the
server sends the client, in this order: `claim` (between `claim_started`
and `claim_ended`: a skill's claim, its speaker audio), `stream` (between
`stream_started` and `stream_ended`), `model` (while `model_speaking` is
true), else `sound` (earcons, the waiting cue, tones). The mic has one
segment per press (`ptt_start` to `ptt_stop`), one per claim, and one for
packets outside both (closed after 2 s without any; timeline only, as
they are no one's voice: the tail a client still sends before it hears a
claim ended, say).

**Files** only for voices: mic (presses, claims), `model` and `claim`
segments, as Ogg Opus under `<data_dir>/recordings/YYYY/MM/DD/` (UTC).
`stream` and `sound` segments are timeline only. Every segment ends in one `recorder.segment`
log line. Files are written as `.ogg.part` and renamed on close (fsynced),
so a power cut leaves at worst a truncated, playable `.part`.

**A race the inference has to absorb.** A stream's or a claim's first
packets can reach the wire a few ms before its `stream_started` /
`claim_started` (the pump runs while the coordinator announces it), and
`model_speaking` is already true for them (it means "something owns the
speaker"). A `model` segment younger than `_RETAG_WINDOW_S` and holding
at most `_RETAG_MAX_SAMPLES` of audio when a stream or claim starts is
therefore that stream's or claim's start: it is retagged (its file
dropped for a stream, kept and renamed for a claim) instead of leaving a
stray `model` file with an earcon in it. The audio cap matters because
model audio arrives faster than real time: a whole short answer can be
written within the window, and real speech must not be dropped.

**A gap it cannot absorb.** When something parks a live stream (an
injected turn, a claim) and the model then speaks, the coordinator has
sent no `stream_ended`: the client is told the stream still plays, and
that speech is tagged `stream`, with no file. The frames cannot tell it from
the stream resuming; the fix belongs in the coordinator (announce the
park as `stream_ended`, the resume as `stream_started`). See
docs/remote-operation.md W3.

**Threading.** The event loop only stamps and enqueues (`Recorder` methods
never block and never raise). One background thread runs the segmenting
(`_Timeline`) and every disk operation. Packets beyond
`_MAX_QUEUED_PACKETS` waiting are dropped and counted (the count lands on
the segment's line); control events are never dropped, so the timeline
stays consistent. A disk error is logged (`recorder.write_failed`) and the
segment carries on as timeline only; audio is never affected.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import queue
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from huxley.server.ogg_opus import MAX_PACKET_BYTES, OggOpusWriter, opus_packet_samples
from huxley.server.opus_codec import CHANNELS, SAMPLE_RATE

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import BinaryIO

logger = structlog.get_logger()

SOURCE_MIC = "mic"
SOURCE_MODEL = "model"
SOURCE_STREAM = "stream"
SOURCE_CLAIM = "claim"
SOURCE_SOUND = "sound"
# Sources that get an audio file; the others are timeline lines only.
_AUDIO_SOURCES = frozenset({SOURCE_MIC, SOURCE_MODEL, SOURCE_CLAIM})

# Packets waiting for the writer thread before new ones are dropped. A
# packet is ~240 bytes and 60 ms: 3000 is about 700 KB and three minutes
# of one direction, far more than a healthy disk ever lets pile up.
_MAX_QUEUED_PACKETS = 3000
# A `model` segment this young when a stream or claim starts is that
# stream's or claim's first audio (see the module docstring).
_RETAG_WINDOW_S = 0.5
# ...and holding at most this much audio (48 kHz samples, 1 s). The race
# leaves a packet or two; a longer segment is real speech.
_RETAG_MAX_SAMPLES = 48000
# A `sound` segment, or mic packets outside a press or claim, end after
# this long without a packet: nothing else announces their end.
_IDLE_CLOSE_S = 2.0
# How often the writer thread wakes with nothing queued, to close idle
# segments.
_TICK_S = 0.5
# How long a stop waits for the writer to drain and close its files.
_STOP_TIMEOUT_S = 5.0
_LABEL_SLUG_MAX = 40

# Event kinds, loop -> writer thread.
_CONN_OPEN = "conn_open"
_CONN_CLOSE = "conn_close"
_MIC = "mic"
_SPEAKER = "speaker"
_PTT = "ptt"
_MODEL = "model_speaking"
_STREAM_START = "stream_started"
_STREAM_END = "stream_ended"
_CLAIM_START = "claim_started"
_CLAIM_END = "claim_ended"
_CLEAR = "audio_clear"
_CLOSE_ALL = "close_all"


@dataclass(frozen=True, slots=True)
class _Event:
    kind: str
    t_wall: float
    t_mono: float
    conn_id: str | None = None
    packet: bytes | None = None
    args: tuple[Any, ...] = ()
    # Packets dropped on the loop side (queue full) since the last event
    # that got through: charged to whatever segment is open when it lands.
    dropped_mic: int = 0
    dropped_speaker: int = 0


@dataclass(slots=True, eq=False)
class _Segment:
    conn_id: str
    seq: int
    source: str
    label: str | None
    stream_id: str | None
    claim_id: str | None
    key: tuple[object, ...]
    opened_mono: float
    audio: bool
    first_wall: float | None = None
    last_wall: float | None = None
    last_mono: float = 0.0
    samples: int = 0
    packets: int = 0
    nbytes: int = 0
    dropped: int = 0
    invalid: int = 0
    cut_by_clear: bool = False
    retagged_from: str | None = None
    retagged_samples: int = 0
    error: str | None = None
    fh: BinaryIO | None = None
    writer: OggOpusWriter | None = None
    part_path: Path | None = None
    stem: str | None = None


def _iso(t: float | None) -> str | None:
    if t is None:
        return None
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _slug(text: str | None) -> str:
    """ASCII, lowercase, dash-separated, short: safe in a file name."""
    if not text:
        return ""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    out: list[str] = []
    for ch in ascii_text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:_LABEL_SLUG_MAX].strip("-")


class _Timeline:
    """The segmenter and the files. Runs on the writer thread only.

    Context (what plays, who holds the mic) is server-level and survives
    reconnects; segments belong to one connection and close with it.
    At most one speaker segment and one mic segment are open at a time.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._root = data_dir / "recordings"
        # Context.
        self._model = False
        self._model_span = 0
        self._stream: tuple[str, str | None] | None = None
        self._claim: tuple[str, str, str | None] | None = None
        self._ptt = False
        self._press = 0
        # The Opus connection being recorded (None: none, or a PCM one).
        self._conn: str | None = None
        self._seq: dict[str, int] = {}
        # Open segments.
        self._speaker: _Segment | None = None
        self._mic: _Segment | None = None
        self.segments = 0
        self.files = 0
        self.unattributed_drops = 0

    # --- events -------------------------------------------------------

    def handle(self, ev: _Event) -> None:
        self._charge_drops(ev)
        kind = ev.kind
        if kind == _SPEAKER:
            self._on_speaker_packet(ev)
        elif kind == _MIC:
            self._on_mic_packet(ev)
        elif kind == _MODEL:
            value = bool(ev.args[0])
            if value and not self._model:
                self._model_span += 1
            self._model = value
            self._reconcile_speaker(ev, "model_done" if not value else "next_source")
        elif kind == _STREAM_START:
            stream_id, label = ev.args
            self._stream = (stream_id, label)
            self._reconcile_speaker(ev, "next_source")
        elif kind == _STREAM_END:
            stream_id, end_reason = ev.args
            if self._stream is not None and self._stream[0] == stream_id:
                self._stream = None
            seg = self._speaker
            if seg is not None and seg.source == SOURCE_STREAM and seg.stream_id == stream_id:
                self._close_speaker(end_reason)
            self._reconcile_speaker(ev, "next_source")
        elif kind == _CLAIM_START:
            claim_id, skill, title = ev.args
            self._claim = (claim_id, skill, title)
            self._reconcile_speaker(ev, "next_source")
            self._reconcile_mic("next_source")
        elif kind == _CLAIM_END:
            claim_id, end_reason = ev.args
            if self._claim is not None and self._claim[0] == claim_id:
                self._claim = None
            if self._speaker is not None and self._speaker.claim_id == claim_id:
                self._close_speaker(end_reason)
            if self._mic is not None and self._mic.claim_id == claim_id:
                self._close_mic(end_reason)
            self._reconcile_speaker(ev, "next_source")
            self._reconcile_mic("next_source")
        elif kind == _PTT:
            active = bool(ev.args[0])
            if active:
                self._press += 1
            self._ptt = active
            self._reconcile_mic("ptt_start" if active else "ptt_stop")
        elif kind == _CLEAR:
            if self._speaker is not None:
                self._speaker.cut_by_clear = True
                self._close_speaker("audio_clear")
        elif kind == _CONN_OPEN:
            conn_id, audio_format = ev.args
            self.close_all("next_connection")
            self._conn = conn_id if audio_format == "opus" else None
        elif kind == _CONN_CLOSE:
            (reason,) = ev.args
            if self._speaker is not None and self._speaker.conn_id == ev.conn_id:
                self._close_speaker(reason)
            if self._mic is not None and self._mic.conn_id == ev.conn_id:
                self._close_mic(reason)
            if self._conn == ev.conn_id:
                self._conn = None
            if ev.conn_id is not None:
                self._seq.pop(ev.conn_id, None)
            self._ptt = False
        elif kind == _CLOSE_ALL:
            (reason,) = ev.args
            self.close_all(reason)

    def tick(self, now_mono: float) -> None:
        """Close the segments nothing else ends, once idle."""
        seg = self._speaker
        if (
            seg is not None
            and seg.source == SOURCE_SOUND
            and now_mono - seg.last_mono >= _IDLE_CLOSE_S
        ):
            self._close_speaker("idle")
        seg = self._mic
        if (
            seg is not None
            and seg.key == ("unsolicited",)
            and now_mono - seg.last_mono >= _IDLE_CLOSE_S
        ):
            self._close_mic("idle")

    def close_all(self, reason: str) -> None:
        if self._speaker is not None:
            self._close_speaker(reason)
        if self._mic is not None:
            self._close_mic(reason)

    # --- what is current ----------------------------------------------

    def _speaker_now(
        self,
    ) -> tuple[str, str | None, str | None, str | None, tuple[object, ...]]:
        """(source, label, stream_id, claim_id, key) of the speaker now."""
        if self._claim is not None:
            claim_id, skill, title = self._claim
            return SOURCE_CLAIM, title or skill, None, claim_id, (SOURCE_CLAIM, claim_id)
        if self._stream is not None:
            stream_id, label = self._stream
            return SOURCE_STREAM, label, stream_id, None, (SOURCE_STREAM, stream_id)
        if self._model:
            return SOURCE_MODEL, None, None, None, (SOURCE_MODEL, self._model_span)
        return SOURCE_SOUND, None, None, None, (SOURCE_SOUND,)

    def _mic_now(self) -> tuple[str | None, str | None, tuple[object, ...]]:
        """(label, claim_id, key) of the mic now."""
        if self._ptt:
            return None, None, ("press", self._press)
        if self._claim is not None:
            claim_id, skill, title = self._claim
            return title or skill, claim_id, (SOURCE_CLAIM, claim_id)
        return None, None, ("unsolicited",)

    def _reconcile_speaker(self, ev: _Event, reason: str) -> None:
        seg = self._speaker
        if seg is None:
            return
        source, label, stream_id, claim_id, key = self._speaker_now()
        if seg.key == key:
            return
        if (
            seg.source == SOURCE_MODEL
            and source in (SOURCE_STREAM, SOURCE_CLAIM)
            and ev.t_mono - seg.opened_mono <= _RETAG_WINDOW_S
            and seg.samples <= _RETAG_MAX_SAMPLES
        ):
            self._retag(seg, source, label, stream_id, claim_id, key)
            return
        self._close_speaker(reason)

    def _reconcile_mic(self, reason: str) -> None:
        seg = self._mic
        if seg is None:
            return
        _label, _claim_id, key = self._mic_now()
        if seg.key != key:
            self._close_mic(reason)

    def _retag(
        self,
        seg: _Segment,
        source: str,
        label: str | None,
        stream_id: str | None,
        claim_id: str | None,
        key: tuple[object, ...],
    ) -> None:
        seg.retagged_from = seg.source
        seg.retagged_samples = seg.samples
        seg.source, seg.label, seg.stream_id, seg.claim_id, seg.key = (
            source,
            label,
            stream_id,
            claim_id,
            key,
        )
        if source not in _AUDIO_SOURCES:
            seg.audio = False
            self._discard_file(seg)

    # --- packets ------------------------------------------------------

    def _on_speaker_packet(self, ev: _Event) -> None:
        if ev.conn_id is None or ev.conn_id != self._conn:
            return
        source, label, stream_id, claim_id, key = self._speaker_now()
        if self._speaker is not None and self._speaker.key != key:
            self._close_speaker("next_source")
        if self._speaker is None:
            self._speaker = self._new_segment(
                ev, ev.conn_id, source, label, stream_id, claim_id, key
            )
        self._append(self._speaker, ev)

    def _on_mic_packet(self, ev: _Event) -> None:
        if ev.conn_id is None or ev.conn_id != self._conn:
            return
        label, claim_id, key = self._mic_now()
        if self._mic is not None and self._mic.key != key:
            self._close_mic("next_source")
        if self._mic is None:
            self._mic = self._new_segment(
                ev,
                ev.conn_id,
                SOURCE_MIC,
                label,
                None,
                claim_id,
                key,
                # Packets outside a press or claim: timeline only.
                audio=key != ("unsolicited",),
            )
        self._append(self._mic, ev)

    def _new_segment(
        self,
        ev: _Event,
        conn_id: str,
        source: str,
        label: str | None,
        stream_id: str | None,
        claim_id: str | None,
        key: tuple[object, ...],
        *,
        audio: bool = True,
    ) -> _Segment:
        seq = self._seq.get(conn_id, 0) + 1
        self._seq[conn_id] = seq
        return _Segment(
            conn_id=conn_id,
            seq=seq,
            source=source,
            label=label,
            stream_id=stream_id,
            claim_id=claim_id,
            key=key,
            opened_mono=ev.t_mono,
            audio=audio and source in _AUDIO_SOURCES,
            last_mono=ev.t_mono,
        )

    def _append(self, seg: _Segment, ev: _Event) -> None:
        packet = ev.packet or b""
        if seg.first_wall is None:
            seg.first_wall = ev.t_wall
        seg.last_wall = ev.t_wall
        seg.last_mono = ev.t_mono
        samples = opus_packet_samples(packet)
        if samples == 0 or len(packet) > MAX_PACKET_BYTES:
            # Not storable as an Opus packet (empty, malformed, oversized):
            # counted, never written.
            seg.invalid += 1
            return
        seg.packets += 1
        seg.nbytes += len(packet)
        seg.samples += samples
        if not seg.audio or seg.error is not None:
            return
        if seg.writer is None:
            self._open_file(seg, ev.t_wall)
        writer = seg.writer
        if writer is None:
            return
        try:
            writer.add(packet, samples)
        except (OSError, ValueError) as exc:
            self._file_failed(seg, "write", exc)

    def _charge_drops(self, ev: _Event) -> None:
        if ev.dropped_mic:
            if self._mic is not None:
                self._mic.dropped += ev.dropped_mic
            else:
                self.unattributed_drops += ev.dropped_mic
        if ev.dropped_speaker:
            if self._speaker is not None:
                self._speaker.dropped += ev.dropped_speaker
            else:
                self.unattributed_drops += ev.dropped_speaker

    # --- files --------------------------------------------------------

    def _open_file(self, seg: _Segment, t_wall: float) -> None:
        when = datetime.fromtimestamp(t_wall, UTC)
        day_dir = self._root / f"{when:%Y}" / f"{when:%m}" / f"{when:%d}"
        seg.stem = f"{when:%H%M%S}-{seg.conn_id}-{seg.seq:03d}"
        part = day_dir / f"{seg.stem}-{seg.source}.ogg.part"
        try:
            day_dir.mkdir(parents=True, exist_ok=True)
            fh = part.open("xb")
        except OSError as exc:
            seg.error = f"{type(exc).__name__}: {exc}"
            self._log_write_failed(seg, "open", exc, part)
            return
        seg.fh = fh
        seg.part_path = part
        try:
            seg.writer = OggOpusWriter(
                fh,
                serial=secrets.randbits(32),
                channels=CHANNELS,
                input_sample_rate=SAMPLE_RATE,
            )
        except OSError as exc:
            self._file_failed(seg, "open", exc)

    def _file_failed(self, seg: _Segment, op: str, exc: BaseException) -> None:
        """Stop writing this segment's file, keep what is on disk as
        `.part`, carry on counting."""
        seg.error = f"{type(exc).__name__}: {exc}"
        self._log_write_failed(seg, op, exc, seg.part_path)
        seg.writer = None
        if seg.fh is not None:
            with contextlib.suppress(OSError):
                seg.fh.close()
            seg.fh = None

    def _discard_file(self, seg: _Segment) -> None:
        """Retagged into a timeline-only source: the file goes."""
        seg.writer = None
        if seg.fh is not None:
            with contextlib.suppress(OSError):
                seg.fh.close()
            seg.fh = None
        if seg.part_path is not None:
            try:
                seg.part_path.unlink()
            except OSError as exc:
                self._log_write_failed(seg, "discard", exc, seg.part_path)
            seg.part_path = None

    def _finish_file(self, seg: _Segment) -> Path | None:
        """Close, fsync and rename `.part` to `.ogg`. Returns where the
        audio is (the `.part` if something failed after it was written)."""
        if seg.writer is None or seg.fh is None or seg.part_path is None:
            return seg.part_path if seg.part_path is not None and seg.part_path.exists() else None
        part = seg.part_path
        try:
            seg.writer.finish()
            seg.fh.flush()
            os.fsync(seg.fh.fileno())
        except (OSError, ValueError) as exc:
            self._file_failed(seg, "close", exc)
            return part if part.exists() else None
        with contextlib.suppress(OSError):
            seg.fh.close()
        seg.fh = None
        seg.writer = None
        slug = _slug(seg.label)
        name = f"{seg.stem}-{seg.source}" + (f"-{slug}" if slug else "")
        final = part.parent / f"{name}.ogg"
        n = 1
        while final.exists():
            final = part.parent / f"{name}-{n}.ogg"
            n += 1
        try:
            part.rename(final)
        except OSError as exc:
            seg.error = f"{type(exc).__name__}: {exc}"
            self._log_write_failed(seg, "rename", exc, part)
            return part
        seg.part_path = None
        self._fsync_dir(final.parent)
        self.files += 1
        return final

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        """Make the rename itself durable. Best effort."""
        with contextlib.suppress(OSError):
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    # --- closing ------------------------------------------------------

    def _close_speaker(self, reason: str) -> None:
        seg, self._speaker = self._speaker, None
        if seg is not None:
            self._close(seg, reason)

    def _close_mic(self, reason: str) -> None:
        seg, self._mic = self._mic, None
        if seg is not None:
            self._close(seg, reason)

    def _close(self, seg: _Segment, reason: str) -> None:
        path = self._finish_file(seg) if seg.audio or seg.part_path is not None else None
        self.segments += 1
        fields: dict[str, Any] = {
            "source": seg.source,
            "label": seg.label,
            "start": _iso(seg.first_wall),
            "end": _iso(seg.last_wall),
            "duration_ms": seg.samples // 48,
            "packets": seg.packets,
            "bytes": seg.nbytes,
            "path": self._relative(path),
            "end_reason": reason,
            "cut_by_clear": seg.cut_by_clear,
            "dropped": seg.dropped,
            "conn_id": seg.conn_id,
        }
        if seg.stream_id is not None:
            fields["stream_id"] = seg.stream_id
        if seg.claim_id is not None:
            fields["claim_id"] = seg.claim_id
        if seg.invalid:
            fields["invalid"] = seg.invalid
        if seg.retagged_from is not None:
            fields["retagged_from"] = seg.retagged_from
            fields["retagged_ms"] = seg.retagged_samples // 48
        if seg.error is not None:
            fields["error"] = seg.error
        logger.info("recorder.segment", **fields)

    def _relative(self, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return str(path.relative_to(self._data_dir))
        except ValueError:
            return str(path)

    def _log_write_failed(
        self, seg: _Segment, op: str, exc: BaseException, path: Path | None
    ) -> None:
        logger.warning(
            "recorder.write_failed",
            op=op,
            error=f"{type(exc).__name__}: {exc}",
            path=self._relative(path),
            source=seg.source,
            conn_id=seg.conn_id,
            note="audio unaffected; this segment continues as timeline only",
        )


class Recorder:
    """Loop-facing side: stamps events and hands them to the writer
    thread. Every method is non-blocking and never raises; after `stop`
    they are no-ops.

    `wall` and `mono` are the clocks events are stamped with (wall for
    the times in log lines and file names, monotonic for durations);
    tests pass fakes.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        max_queued_packets: int = _MAX_QUEUED_PACKETS,
        wall: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self._data_dir = data_dir
        self._timeline = _Timeline(data_dir)
        self._queue: queue.SimpleQueue[_Event | None] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._queued_packets = 0
        self._max_queued_packets = max_queued_packets
        self._wall = wall
        self._mono = mono
        # Loop side only.
        self._pending_drop_mic = 0
        self._pending_drop_speaker = 0
        self.dropped_total = 0
        self._thread: threading.Thread | None = None
        self._stopped = False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the writer thread. Raises if the thread cannot start
        (the caller logs it and runs without recording)."""
        if self._thread is not None:
            return
        thread = threading.Thread(target=self._run, name="huxley-recorder", daemon=True)
        thread.start()
        self._thread = thread
        logger.info(
            "recorder.started",
            path=str(self._data_dir / "recordings"),
            max_queued_packets=self._max_queued_packets,
        )

    def stop(self, timeout_s: float = _STOP_TIMEOUT_S) -> bool:
        """Close every open segment, flush, stop the thread. True when the
        writer finished within `timeout_s`. Idempotent."""
        self._request_stop()
        return self._join(timeout_s)

    async def aclose(self, timeout_s: float = _STOP_TIMEOUT_S) -> bool:
        """`stop` without blocking the event loop on the join. The stop
        request itself is enqueued here, on the loop, like every event."""
        self._request_stop()
        return await asyncio.to_thread(self._join, timeout_s)

    def _request_stop(self) -> None:
        if self._stopped:
            return
        # One last event so drops counted since the previous one are
        # charged to the segments they belong to, then the sentinel.
        self._put(_CLOSE_ALL, args=("shutdown",))
        self._stopped = True
        self._queue.put(None)

    def _join(self, timeout_s: float) -> bool:
        thread = self._thread
        if thread is None or not thread.is_alive():
            return True
        thread.join(timeout_s)
        if thread.is_alive():
            logger.warning(
                "recorder.write_failed",
                op="stop",
                error=f"writer still busy after {timeout_s:.0f} s",
                note="files still open stay as .part",
            )
            return False
        return True

    # --- what the AudioServer reports ---------------------------------

    def connection_opened(self, conn_id: str, audio_format: str) -> None:
        self._put(_CONN_OPEN, conn_id=conn_id, args=(conn_id, audio_format))

    def connection_closed(self, conn_id: str, reason: str) -> None:
        self._put(_CONN_CLOSE, conn_id=conn_id, args=(reason,))

    def mic_packet(self, conn_id: str, packet: bytes) -> None:
        self._put(_MIC, conn_id=conn_id, packet=packet)

    def speaker_packet(self, conn_id: str, packet: bytes) -> None:
        self._put(_SPEAKER, conn_id=conn_id, packet=packet)

    def ptt(self, active: bool) -> None:
        self._put(_PTT, args=(active,))

    def model_speaking(self, value: bool) -> None:
        self._put(_MODEL, args=(value,))

    def stream_started(self, stream_id: str, label: str | None) -> None:
        self._put(_STREAM_START, args=(stream_id, label))

    def stream_ended(self, stream_id: str, end_reason: str) -> None:
        self._put(_STREAM_END, args=(stream_id, end_reason))

    def claim_started(self, claim_id: str, skill: str, title: str | None) -> None:
        self._put(_CLAIM_START, args=(claim_id, skill, title))

    def claim_ended(self, claim_id: str, end_reason: str) -> None:
        self._put(_CLAIM_END, args=(claim_id, end_reason))

    def audio_clear(self) -> None:
        self._put(_CLEAR)

    def detached(self) -> None:
        """Another recorder took over the server (a persona swap): close
        what is open now rather than when this one stops."""
        self._put(_CLOSE_ALL, args=("recorder_replaced",))

    # --- plumbing -----------------------------------------------------

    def _put(
        self,
        kind: str,
        *,
        conn_id: str | None = None,
        packet: bytes | None = None,
        args: tuple[Any, ...] = (),
    ) -> None:
        if self._stopped:
            return
        try:
            if packet is not None:
                with self._lock:
                    full = self._queued_packets >= self._max_queued_packets
                    if not full:
                        self._queued_packets += 1
                if full:
                    if kind == _MIC:
                        self._pending_drop_mic += 1
                    else:
                        self._pending_drop_speaker += 1
                    self.dropped_total += 1
                    return
            event = _Event(
                kind=kind,
                t_wall=self._wall(),
                t_mono=self._mono(),
                conn_id=conn_id,
                packet=packet,
                args=args,
                dropped_mic=self._pending_drop_mic,
                dropped_speaker=self._pending_drop_speaker,
            )
            self._pending_drop_mic = self._pending_drop_speaker = 0
            self._queue.put(event)
        except Exception:
            # Never into the audio path.
            self.dropped_total += 1

    def _run(self) -> None:
        timeline = self._timeline
        while True:
            try:
                event = self._queue.get(timeout=_TICK_S)
            except queue.Empty:
                self._safely(timeline.tick, self._mono())
                continue
            if event is None:
                break
            if event.packet is not None:
                with self._lock:
                    self._queued_packets -= 1
            # Idle first, at the event's time: a segment that went quiet
            # before this event ends as `idle`, not as whatever the
            # event does to it.
            self._safely(timeline.tick, event.t_mono)
            self._safely(timeline.handle, event)
        self._safely(timeline.close_all, "shutdown")
        logger.info(
            "recorder.stopped",
            segments=timeline.segments,
            files=timeline.files,
            dropped=self.dropped_total,
            unattributed_drops=timeline.unattributed_drops,
        )

    @staticmethod
    def _safely(fn: Callable[..., None], *args: Any) -> None:
        """A bug in the segmenter must not kill the writer thread."""
        try:
            fn(*args)
        except Exception as exc:
            logger.exception("recorder.write_failed", op="handle", error=f"{exc!r}")

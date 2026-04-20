"""Telegram voice-call transport — encapsulates py-tgcalls.

This is the working recipe from `spikes/test_telegram_mediastream_fifo.py`
extracted into a reusable class for the skill. See
`docs/research/telegram-voice.md` §"Bidirectional live-PCM on p2p"
for the full rationale behind every choice in this file; in brief:

- Outbound: our code writes 24 kHz mono PCM16 to a Unix FIFO; an ffmpeg
  subprocess (spawned by ntgcalls via `MediaSource.SHELL`) reads the FIFO
  and pipes decoded PCM into the WebRTC encoder.
- Inbound: py-tgcalls' `record()` + `@stream_frame` handler delivers
  48 kHz stereo PCM16. The transport downsamples to 24 kHz mono via
  simple decimation + channel averaging before enqueuing.
- The FIFO is opened O_RDWR before dial so ffmpeg's first read doesn't
  see EOF. A writer thread (NOT an asyncio task) produces bytes because
  `play()` blocks the event loop for several seconds during handshake.

The transport is intentionally split from the skill so the skill logic
can be unit-tested against a stub transport without real py-tgcalls.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import struct
import threading
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

logger = structlog.get_logger()

# Huxley internal audio format — PCM16 mono at 24 kHz.
HUXLEY_SAMPLE_RATE_HZ = 24_000
HUXLEY_CHANNELS = 1
BYTES_PER_SAMPLE = 2

# What ntgcalls expects on the outbound path. Telegram's Opus pipeline
# runs natively at 48 kHz. Telling ntgcalls 24 kHz mono forces an
# internal resampler in its audio sink (evidence: both MediaSource.FILE
# and ExternalMedia+send_frame exhibited the same ~5.5 s outbound
# latency; the only shared stage is the audio sink / resampler). We
# feed 48 kHz mono upstream (cheap per-sample duplicate in Python) so
# ntgcalls's sink is a passthrough.
OUTBOUND_SAMPLE_RATE_HZ = 48_000
OUTBOUND_CHANNELS = 1

# What ntgcalls delivers on the inbound path. Requesting 24k mono here
# returns zero-filled frames (internal resampler bug on p2p). We ask
# for 48k stereo and downsample in Python.
PEER_SAMPLE_RATE_HZ = 48_000
PEER_CHANNELS = 2

# Session-file bookkeeping. A persona whose data dir is
# `personas/abuelos/data/` gets its userbot session persisted at
# `personas/abuelos/data/huxley_userbot.session` so the first-run
# SMS-code auth only happens once per deploy. The name matches
# `spikes/test_telegram_call.py`'s session name so Mario can copy
# the already-authenticated spike session file across instead of
# re-running the SMS flow.
_SESSION_NAME = "huxley_userbot"


def _rms_pcm16(data: bytes) -> float:
    """RMS of a PCM16 buffer. For diagnostic heartbeats — silence is
    ~0, voice speech is tens to hundreds. Tolerant of odd lengths."""
    if len(data) < 2:
        return 0.0
    n = len(data) // 2
    samples = struct.unpack(f"<{n}h", data[: n * 2])
    sq = sum(s * s for s in samples)
    return float((sq / n) ** 0.5)


def upsample_24k_mono_to_48k_mono(pcm_24k: bytes) -> bytes:
    """PCM16 24 kHz mono → 48 kHz mono by sample duplication.

    Each input sample becomes two identical output samples. Cheap
    (memcpy-shaped); quality is adequate for Opus encoding since
    Opus's own interpolation smooths the step-duplication artifacts.
    Doubling the byte count.
    """
    if not pcm_24k:
        return b""
    n_samples = len(pcm_24k) // 2
    if n_samples == 0:
        return b""
    out = bytearray(n_samples * 4)
    for i in range(n_samples):
        b0 = pcm_24k[i * 2]
        b1 = pcm_24k[i * 2 + 1]
        out[i * 4] = b0
        out[i * 4 + 1] = b1
        out[i * 4 + 2] = b0
        out[i * 4 + 3] = b1
    return bytes(out)


def downsample_48k_stereo_to_24k_mono(pcm_in: bytes) -> bytes:
    """Convert PCM16 48 kHz stereo → 24 kHz mono.

    Decimation by 2 (keep every other sample) combined with channel
    averaging (L+R)/2. Quality is adequate for voice — we're going
    into Whisper / GPT-4o realtime on the Huxley side, both of which
    happily accept 16 kHz-grade speech. Not a general-purpose
    high-fidelity resampler.

    Input layout:  L0 R0  L1 R1  L2 R2  L3 R3 ...  (4 bytes/frame)
    Output layout: M0     M2     M4     M6     ...  (2 bytes/frame)
    where Mn = (Ln + Rn) / 2 with n stepping by 2.
    """
    n_in_frames = len(pcm_in) // 4  # 4 bytes per stereo frame
    n_out_frames = n_in_frames // 2  # decimate by 2
    if n_out_frames == 0:
        return b""
    out = bytearray(n_out_frames * 2)
    samples = struct.unpack(f"<{n_in_frames * 2}h", pcm_in[: n_in_frames * 4])
    for i in range(n_out_frames):
        src = i * 4  # index of L sample at position 2*i in frame-space
        left = samples[src]
        right = samples[src + 1]
        mono = (left + right) // 2
        struct.pack_into("<h", out, i * 2, mono)
    return bytes(out)


class TransportError(Exception):
    """Raised on any transport-level failure (auth, dial, send)."""


class TelegramTransport:
    """Owns one userbot client + one active p2p call.

    Single-call invariant: the transport handles one call at a time.
    Attempting `place_call` while a call is active raises.
    """

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session_dir: Path,
        userbot_phone: str | None = None,
    ) -> None:
        """Create a transport. `userbot_phone` is only consulted on the
        first-ever auth for this session file — stored in sqlite after,
        skipped on every subsequent startup.
        """
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_dir = session_dir
        self._userbot_phone = userbot_phone

        # Built lazily in `connect()` so construction is cheap and the
        # transport can be instantiated in tests without triggering
        # pyrogram / ntgcalls imports.
        self._app: object | None = None  # pyrogram.Client
        self._call_py: object | None = None  # PyTgCalls

        # Active-call state.
        self._active_user_id: int | None = None
        self._mic_fd: int | None = None
        self._mic_fifo: Path | None = None
        self._writer_thread: threading.Thread | None = None
        self._writer_stop = threading.Event()
        # Thread-safe buffer: the writer thread consumes from this;
        # the async producer (skill's on_mic_frame) appends. `_sent_count`
        # tracks total chunks pushed for first-chunk + heartbeat logging.
        self._outbound_chunks: list[bytes] = []
        self._outbound_lock = threading.Lock()
        self._sent_count = 0
        # Bytes dropped by the outbound backlog-cap policy in `send_pcm`.
        # Accumulated per heartbeat so we can tell "clean call" from
        # "dropping audio to keep latency low."
        self._outbound_dropped_bytes = 0
        # Heartbeat counters — inbound peer-frame arrival and outbound
        # mic-frame push. Logged every 2s during a call so we can see
        # if the pipe went silent mid-conversation. RMS accumulators
        # track audio energy so we can tell "frames arriving but empty"
        # (codec bug) from "frames carrying real speech" (working).
        self._peer_frames_received = 0
        self._peer_bytes_received = 0
        self._peer_rms_sum = 0.0
        self._peer_rms_count = 0
        self._mic_rms_sum = 0.0
        self._mic_rms_count = 0
        # Latency probe for the outbound pipeline: per-call-to-
        # `send_frame` wall time. If each call blocks for ms, ntgcalls
        # is holding the frame in an internal buffer before releasing
        # it to the encoder. If each call returns in µs, the delay is
        # downstream of Python (WebRTC encoder, network, peer jitter
        # buffer). Reset per heartbeat.
        self._send_frame_ms_sum = 0.0
        self._send_frame_ms_count = 0
        self._send_frame_ms_max = 0.0
        self._heartbeat_task: asyncio.Task[None] | None = None

        # Incoming audio: downsampled peer PCM enqueued by the
        # stream_frame handler; speaker_source drains it.
        self._inbound_queue: asyncio.Queue[bytes] | None = None

        self._ended = asyncio.Event()

    async def connect(self) -> None:
        """Start pyrogram + PyTgCalls. Idempotent."""
        if self._call_py is not None:
            return
        from pyrogram import Client  # type: ignore[attr-defined]
        from pytgcalls import PyTgCalls

        app = Client(
            _SESSION_NAME,
            api_id=self._api_id,
            api_hash=self._api_hash,
            workdir=str(self._session_dir),
            phone_number=self._userbot_phone,
        )
        call_py = PyTgCalls(app)
        self._wire_peer_audio_handler(call_py)
        await call_py.start()
        self._app = app
        self._call_py = call_py
        await logger.ainfo("comms_telegram.transport.connected")

    def _wire_peer_audio_handler(self, call_py: object) -> None:
        """Attach the stream_frame filter that pumps peer audio into
        `_inbound_queue`, plus a chat_update filter that ends the claim
        when the peer hangs up. Must run before `call_py.start()` per
        the upstream example pattern.
        """
        from pytgcalls import filters as fl
        from pytgcalls.types import ChatUpdate, Device, Direction

        # Log once per call so we know frames are flowing without
        # spamming the log with one event per 10ms frame.
        saw_first_frame = {"value": False}

        @call_py.on_update(  # type: ignore[attr-defined, untyped-decorator]
            fl.stream_frame(devices=Device.MICROPHONE | Device.SPEAKER),
        )
        async def on_peer_audio(_, update) -> None:  # type: ignore[no-untyped-def]
            if update.direction != Direction.INCOMING:
                return
            if self._inbound_queue is None:
                return  # no active call — drop frames
            if not saw_first_frame["value"]:
                saw_first_frame["value"] = True
                await logger.ainfo(
                    "comms_telegram.transport.first_peer_frame",
                    frame_count=len(update.frames),
                    first_frame_bytes=len(update.frames[0].frame) if update.frames else 0,
                )
            for frame in update.frames:
                self._peer_frames_received += 1
                self._peer_bytes_received += len(frame.frame)
                self._peer_rms_sum += _rms_pcm16(frame.frame)
                self._peer_rms_count += 1
                mono24k = downsample_48k_stereo_to_24k_mono(frame.frame)
                if mono24k:
                    with contextlib.suppress(asyncio.QueueFull):
                        self._inbound_queue.put_nowait(mono24k)

        @call_py.on_update(  # type: ignore[attr-defined, untyped-decorator]
            fl.chat_update(
                ChatUpdate.Status.DISCARDED_CALL | ChatUpdate.Status.BUSY_CALL,
            ),
        )
        async def on_chat_update(_, update) -> None:  # type: ignore[no-untyped-def]
            # Peer hung up OR call failed to establish → close the claim
            # so grandpa doesn't sit on a dead-silent line forever.
            # Setting _ended short-circuits the speaker_source iterator.
            await logger.ainfo(
                "comms_telegram.transport.chat_update",
                status=str(update.status),
                chat_id=update.chat_id,
            )
            if update.chat_id == self._active_user_id:
                self._ended.set()
                saw_first_frame["value"] = False  # reset for next call

    async def resolve_contact(self, identifier: str) -> int:
        """Return the Telegram `user_id` for a phone number OR @handle.

        Precondition: the target must exist in the userbot's dialog
        history (sent at least one message) or contacts list. Cold
        lookups by phone fail with `PEER_ID_INVALID` on Telegram's
        side — see spike docs.
        """
        if self._app is None:
            msg = "resolve_contact() before connect()"
            raise TransportError(msg)
        from pyrogram.errors import RPCError  # type: ignore[attr-defined]

        try:
            users = await self._app.get_users(identifier)  # type: ignore[attr-defined]
        except RPCError as exc:
            msg = f"Telegram couldn't resolve {identifier!r}: {exc}"
            raise TransportError(msg) from exc
        user = users[0] if isinstance(users, list) else users
        return int(user.id)

    async def place_call(self, user_id: int) -> None:
        """Dial the peer with ExternalMedia.AUDIO on the outbound side.

        Returns when `play()` returns — at that point the call is
        connected on the WebRTC layer. Peer audio may arrive before
        or after; the handler queues it either way.

        Bypasses `MediaSource.FILE` + FIFO + ThreadedReader because
        ThreadedReader hard-codes a 1s read-ahead batch with 2x
        buffering (threaded_reader.cpp:35, `maxBufferSize = 100
        frames`), causing ~5.5 s outbound latency. ExternalMedia +
        `send_frame` posts PCM straight into ntgcalls's send queue.
        """
        if self._active_user_id is not None:
            msg = f"place_call: already in a call with user_id={self._active_user_id}"
            raise TransportError(msg)
        if self._call_py is None:
            msg = "place_call() before connect()"
            raise TransportError(msg)

        from pathlib import Path

        from ntgcalls import MediaSource
        from pytgcalls.types import RecordStream
        from pytgcalls.types.raw import AudioParameters, AudioStream, Stream

        # FIFO path — deterministic per process so cleanup can find it.
        fifo = Path(f"/tmp/huxley_comms_mic_{os.getpid()}.pcm")
        with contextlib.suppress(FileNotFoundError):
            fifo.unlink()
        os.mkfifo(str(fifo))
        # O_RDWR avoids the reader-opens-before-writer race. O_NONBLOCK
        # prevents indefinite kernel-wait on FIFO writes (macOS `U`
        # state processes that even kill -9 can't stop).
        fd = os.open(str(fifo), os.O_RDWR | os.O_NONBLOCK)
        # Prefill with 80 ms of silence so ntgcalls's FileReader first
        # read returns bytes immediately rather than blocking on a
        # newly-created empty FIFO.
        silence = b"\x00\x00" * (OUTBOUND_SAMPLE_RATE_HZ * 80 // 1000)
        os.write(fd, silence)
        self._mic_fd = fd
        self._mic_fifo = fifo

        # Start the writer thread BEFORE dialing so ffmpeg / ntgcalls
        # never see an empty FIFO during setup.
        self._writer_stop.clear()
        thread = threading.Thread(
            target=self._writer_loop,
            args=(fd,),
            daemon=True,
            name="comms-telegram-writer",
        )
        thread.start()
        self._writer_thread = thread

        # Raw Stream + MediaSource.FILE on the FIFO directly.
        out_stream = Stream(
            microphone=AudioStream(
                MediaSource.FILE,
                str(fifo),
                AudioParameters(OUTBOUND_SAMPLE_RATE_HZ, OUTBOUND_CHANNELS),
            ),
        )

        # Spin up the inbound queue BEFORE recording starts so no frame
        # is dropped between `record()` and the first put_nowait.
        self._inbound_queue = asyncio.Queue(maxsize=500)  # ~5s at 10ms frames
        self._active_user_id = user_id
        self._ended.clear()

        try:
            await self._call_py.play(user_id, out_stream)  # type: ignore[attr-defined]
            await self._call_py.record(  # type: ignore[attr-defined]
                user_id,
                RecordStream(
                    audio=True,
                    audio_parameters=AudioParameters(PEER_SAMPLE_RATE_HZ, PEER_CHANNELS),
                ),
            )
        except Exception:
            # Roll back the mic resources on failure so the skill can retry.
            await self._tear_down_call()
            raise

        # Heartbeat task — logs inbound/outbound frame counts every 2s
        # so we can tell "call running but went silent" from "call never
        # had audio." Runs until `_ended` is set by tear-down.
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        await logger.ainfo(
            "comms_telegram.transport.call_placed",
            user_id=user_id,
            transport="ExternalMedia.AUDIO + send_frame",
        )

    async def _heartbeat_loop(self) -> None:
        while not self._ended.is_set():
            await asyncio.sleep(2.0)
            if self._ended.is_set():
                return
            peer_rms = self._peer_rms_sum / self._peer_rms_count if self._peer_rms_count else 0.0
            mic_rms = self._mic_rms_sum / self._mic_rms_count if self._mic_rms_count else 0.0
            with self._outbound_lock:
                outbound_backlog = sum(len(c) for c in self._outbound_chunks)
                dropped = self._outbound_dropped_bytes
                self._outbound_dropped_bytes = 0
            sf_mean_ms = (
                self._send_frame_ms_sum / self._send_frame_ms_count
                if self._send_frame_ms_count
                else 0.0
            )
            await logger.ainfo(
                "comms_telegram.transport.heartbeat",
                peer_frames=self._peer_frames_received,
                peer_bytes=self._peer_bytes_received,
                peer_mean_rms=round(peer_rms, 1),
                mic_chunks=self._sent_count,
                mic_mean_rms=round(mic_rms, 1),
                send_frame_mean_ms=round(sf_mean_ms, 2),
                send_frame_max_ms=round(self._send_frame_ms_max, 2),
                outbound_backlog_bytes=outbound_backlog,
                outbound_dropped_bytes=dropped,
                inbound_queue_depth=(
                    self._inbound_queue.qsize() if self._inbound_queue is not None else 0
                ),
            )
            # Reset per-heartbeat accumulators so each window shows
            # "is audio flowing right now?" rather than smoothing across
            # the whole call.
            self._peer_rms_sum = 0.0
            self._peer_rms_count = 0
            self._mic_rms_sum = 0.0
            self._mic_rms_count = 0
            self._send_frame_ms_sum = 0.0
            self._send_frame_ms_count = 0
            self._send_frame_ms_max = 0.0

    async def send_pcm(self, pcm_24k_mono: bytes) -> None:
        """Queue a PCM chunk for outbound send via the FIFO writer.

        Upsamples 24 kHz mono (Huxley's internal rate) to 48 kHz mono
        so ntgcalls can pass-through to the Opus encoder without
        resampling internally — eliminates the audio-sink resampler
        buffer that accumulates latency.
        """
        if not pcm_24k_mono or self._active_user_id is None:
            return
        pcm_48k_mono = upsample_24k_mono_to_48k_mono(pcm_24k_mono)
        with self._outbound_lock:
            was_first = self._sent_count == 0
            self._outbound_chunks.append(pcm_48k_mono)
            self._sent_count += 1
            self._mic_rms_sum += _rms_pcm16(pcm_24k_mono)
            self._mic_rms_count += 1
            # Cap backlog at ~200 ms of PCM to prevent unbounded growth
            # if browser mic clock drifts past writer pace. At 48 kHz
            # mono PCM16 that's 96 KB / 5 = 19.2 KB.
            _max_bytes = OUTBOUND_SAMPLE_RATE_HZ * BYTES_PER_SAMPLE // 5
            total = sum(len(c) for c in self._outbound_chunks)
            dropped = 0
            while total > _max_bytes and self._outbound_chunks:
                removed = self._outbound_chunks.pop(0)
                total -= len(removed)
                dropped += len(removed)
            if dropped:
                self._outbound_dropped_bytes += dropped
        if was_first:
            # Sync logger from within async context — structlog sync
            # logging is safe from any thread/context.
            structlog.get_logger().info(
                "comms_telegram.transport.first_send_pcm",
                chunk_bytes_in=len(pcm_24k_mono),
                chunk_bytes_out=len(pcm_48k_mono),
            )

    def _writer_loop(self, fd: int) -> None:
        """Pump `_outbound_chunks` to the FIFO, letting cat/ntgcalls
        set the pace via backpressure.

        Experimental: no real-time pacing. We write whatever's queued
        as fast as the kernel FIFO accepts it. When the FIFO fills up
        (O_NONBLOCK raises BlockingIOError), we sleep briefly and
        retry. When the queue is empty, we sleep briefly and loop.
        This lets ntgcalls pull bytes at exactly the rate its encoder
        wants — ideally no buffer accumulates anywhere.
        """
        leftover = bytearray()
        while not self._writer_stop.is_set():
            # Drain everything available in outbound_chunks into leftover
            # so our writes are as big as possible (fewer syscalls).
            with self._outbound_lock:
                while self._outbound_chunks:
                    leftover.extend(self._outbound_chunks.pop(0))

            if not leftover:
                # Nothing to write — yield briefly.
                time.sleep(0.005)
                continue

            try:
                written = os.write(fd, bytes(leftover))
                del leftover[:written]
            except BrokenPipeError:
                break
            except BlockingIOError:
                # Kernel FIFO buffer full — wait for ntgcalls to drain.
                time.sleep(0.005)
                continue
            except OSError:
                break

    async def peer_audio_chunks(self) -> AsyncIterator[bytes]:
        """Async iterator yielding 24 kHz mono PCM chunks from the peer.

        Used by the skill as `InputClaim.speaker_source`. Ends when
        the call ends (`end_call` sets `_ended`).
        """
        q = self._inbound_queue
        if q is None:
            await logger.awarning("comms_telegram.transport.speaker_source_no_queue")
            return
        await logger.ainfo("comms_telegram.transport.speaker_source_started")
        yielded = 0
        while not self._ended.is_set():
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=0.5)
            except TimeoutError:
                continue
            if chunk:
                yielded += 1
                if yielded == 1:
                    await logger.ainfo(
                        "comms_telegram.transport.speaker_source_first_yield",
                        chunk_bytes=len(chunk),
                    )
                yield chunk
        await logger.ainfo(
            "comms_telegram.transport.speaker_source_ended",
            yielded_total=yielded,
        )

    async def end_call(self) -> None:
        """Hang up and clean up the outbound FIFO + writer thread.

        Bounded by hard timeouts so an ntgcalls-side hang (which we've
        observed — `leave_call` can block indefinitely if the peer
        already hung up or the underlying WebRTC state is inconsistent)
        doesn't wedge the observer-unwind chain that has to fire
        `_observer_on_end` for the mic-mode protocol to flip back.
        """
        if self._active_user_id is None:
            return
        user_id = self._active_user_id
        await logger.ainfo("comms_telegram.transport.ending_call", user_id=user_id)

        from pytgcalls.exceptions import NotInCallError

        if self._call_py is not None:
            try:
                await asyncio.wait_for(
                    self._call_py.leave_call(user_id),  # type: ignore[attr-defined]
                    timeout=2.0,
                )
            except (NotInCallError, TimeoutError, Exception) as exc:
                await logger.awarning(
                    "comms_telegram.transport.leave_call_swallowed",
                    exc_type=type(exc).__name__,
                )

        await self._tear_down_call()
        await logger.ainfo("comms_telegram.transport.ended_call", user_id=user_id)

    async def _tear_down_call(self) -> None:
        self._ended.set()
        self._writer_stop.set()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._heartbeat_task
            self._heartbeat_task = None
        if self._writer_thread is not None:
            # Offload the blocking join to a worker thread so we don't
            # stall the event loop while waiting for the writer to
            # notice the stop event.
            await asyncio.to_thread(self._writer_thread.join, 2.0)
            self._writer_thread = None
        if self._mic_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._mic_fd)
            self._mic_fd = None
        if self._mic_fifo is not None:
            with contextlib.suppress(FileNotFoundError):
                self._mic_fifo.unlink()
            self._mic_fifo = None
        self._active_user_id = None
        self._inbound_queue = None
        with self._outbound_lock:
            self._outbound_chunks.clear()
            self._sent_count = 0
            self._outbound_dropped_bytes = 0
        self._peer_frames_received = 0
        self._peer_bytes_received = 0
        self._peer_rms_sum = 0.0
        self._peer_rms_count = 0
        self._mic_rms_sum = 0.0
        self._mic_rms_count = 0

    async def disconnect(self) -> None:
        """Stop pyrogram + PyTgCalls. Hangs up any active call first."""
        if self._active_user_id is not None:
            await self.end_call()
        if self._app is not None:
            with contextlib.suppress(Exception):
                await self._app.stop()  # type: ignore[attr-defined]
            self._app = None
        self._call_py = None
        await logger.ainfo("comms_telegram.transport.disconnected")


def normalize_phone(raw: str) -> str:
    """Strip whitespace + hyphens + parens from a phone number string.

    Persona YAML may have phones in various formats ("+57 315 328 3397",
    "+573153283397", "+57-315-328-3397"); normalize to the canonical
    "+NN...". pyrogram is forgiving but normalization helps matching.
    """
    return re.sub(r"[\s\-()]", "", raw.strip())

"""Telegram voice-call transport -- encapsulates py-tgcalls.

See `docs/research/telegram-voice.md` for the full rationale.

- Outbound: ExternalMedia.AUDIO + dedicated real-time OS thread.
  Python writes 24 kHz mono PCM16 to a thread-safe deque via send_pcm().
  A dedicated OS thread runs at a strict 10 ms/frame cadence, upsamples
  to 48 kHz mono, and calls send_frame() via asyncio.run_coroutine_threadsafe.
  No ffmpeg, no FIFO, no kernel pipe buffer. This is the same pattern used
  by production WebRTC bridges (Janus, Asterisk, Twilio): a real-time OS
  thread owns the sending clock so asyncio jitter never reaches ntgcalls.
- Inbound: py-tgcalls' record() + @stream_frame handler delivers 48 kHz
  stereo PCM16. The transport downsamples to 24 kHz mono via decimation +
  channel averaging before enqueuing.

The transport is split from the skill so skill logic can be unit-tested
against a stub without real py-tgcalls.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
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

# Huxley internal audio format -- PCM16 mono at 24 kHz.
HUXLEY_SAMPLE_RATE_HZ = 24_000
HUXLEY_CHANNELS = 1
BYTES_PER_SAMPLE = 2

# Outbound format sent via ExternalMedia. ntgcalls expects 48 kHz mono
# PCM16 frames of exactly 10 ms each (960 bytes). Python upsamples from
# Huxley's 24 kHz internal rate in the send thread.
OUTBOUND_SAMPLE_RATE_HZ = 48_000
OUTBOUND_CHANNELS = 1

# ExternalMedia frame: 48kHz * 2 bytes/sample * 1 ch / 100 frames/s = 960 bytes
_SEND_FRAME_BYTES = OUTBOUND_SAMPLE_RATE_HZ * BYTES_PER_SAMPLE * OUTBOUND_CHANNELS // 100
_SEND_INTERVAL_S = 0.010  # 10 ms between frames

# Outbound backlog cap: ~200 ms of 48 kHz mono PCM16 before dropping.
_OUTBOUND_MAX_BYTES = OUTBOUND_SAMPLE_RATE_HZ * BYTES_PER_SAMPLE * OUTBOUND_CHANNELS // 5

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
    """RMS of a PCM16 buffer. For diagnostic heartbeats -- silence is
    ~0, voice speech is tens to hundreds. Tolerant of odd lengths."""
    if len(data) < 2:
        return 0.0
    n = len(data) // 2
    samples = struct.unpack(f"<{n}h", data[: n * 2])
    sq = sum(s * s for s in samples)
    return float((sq / n) ** 0.5)


def downsample_48k_stereo_to_24k_mono(pcm_in: bytes) -> bytes:
    """Convert PCM16 48 kHz stereo -> 24 kHz mono.

    Decimation by 2 (keep every other sample) combined with channel
    averaging (L+R)/2. Quality is adequate for voice -- we're going
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


def upsample_24k_mono_to_48k_mono(pcm_in: bytes) -> bytes:
    """2x linear-interpolation upsample: 24 kHz mono PCM16 -> 48 kHz mono PCM16.

    Each input sample maps to two output samples:
      out[2i]   = in[i]
      out[2i+1] = (in[i] + in[i+1]) // 2   for i < n-1
      out[2n-2] = in[n-1]                   last sample: original
      out[2n-1] = in[n-1]                   last sample: repeated
    """
    n = len(pcm_in) // 2
    if n == 0:
        return b""
    samples = struct.unpack(f"<{n}h", pcm_in[: n * 2])
    out = bytearray(n * 4)
    for i in range(n - 1):
        struct.pack_into("<h", out, i * 4, samples[i])
        interp = (int(samples[i]) + int(samples[i + 1])) >> 1
        struct.pack_into("<h", out, i * 4 + 2, interp)
    struct.pack_into("<h", out, (n - 1) * 4, samples[-1])
    struct.pack_into("<h", out, (n - 1) * 4 + 2, samples[-1])
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
        first-ever auth for this session file -- stored in sqlite after,
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
        # Event loop reference captured in place_call(); needed by the
        # send thread to submit coroutines via run_coroutine_threadsafe.
        self._loop: asyncio.AbstractEventLoop | None = None

        # Outbound send thread -- dedicated OS thread at 10 ms cadence.
        # Drains _send_deque, upsamples to 48 kHz, calls send_frame via
        # run_coroutine_threadsafe so asyncio jitter never reaches ntgcalls.
        self._send_thread: threading.Thread | None = None
        self._send_stop = threading.Event()
        self._send_deque: collections.deque[bytes] = collections.deque()
        self._send_lock = threading.Lock()
        self._outbound_dropped_bytes = 0
        self._sent_count = 0

        # Heartbeat counters -- inbound peer-frame arrival and outbound
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
                return  # no active call -- drop frames
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
            # Peer hung up OR call failed to establish -> close the claim
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
        side -- see spike docs.
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
        """Dial the peer via ExternalMedia.AUDIO + dedicated send thread.

        Returns when `play()` returns -- at that point the call is
        connected on the WebRTC layer. Peer audio may arrive before
        or after; the handler queues it either way.

        Outbound path: a dedicated OS thread runs at a strict 10 ms
        cadence, drains _send_deque (filled by send_pcm()), upsamples
        24 kHz mono -> 48 kHz mono, and calls send_frame() via
        run_coroutine_threadsafe so asyncio jitter never reaches the
        ntgcalls encoder. No FIFO, no ffmpeg, no kernel pipe buffer.
        """
        if self._active_user_id is not None:
            msg = f"place_call: already in a call with user_id={self._active_user_id}"
            raise TransportError(msg)
        if self._call_py is None:
            msg = "place_call() before connect()"
            raise TransportError(msg)

        from ntgcalls import ExternalMedia
        from pytgcalls.types import MediaStream, RecordStream
        from pytgcalls.types.raw import AudioParameters

        # Spin up the inbound queue BEFORE recording starts so no frame
        # is dropped between `record()` and the first put_nowait.
        self._inbound_queue = asyncio.Queue(maxsize=500)  # ~5s at 10ms frames
        self._active_user_id = user_id
        self._loop = asyncio.get_running_loop()
        self._ended.clear()

        # Start the send thread BEFORE play() so send_frame calls can
        # land immediately after the call connects.
        self._send_stop.clear()
        send_thread = threading.Thread(
            target=self._send_loop_thread,
            daemon=True,
            name="comms-telegram-send",
        )
        send_thread.start()
        self._send_thread = send_thread

        out_stream = MediaStream(
            ExternalMedia.AUDIO,
            AudioParameters(OUTBOUND_SAMPLE_RATE_HZ, OUTBOUND_CHANNELS),
        )

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
            await self._tear_down_call()
            raise

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        await logger.ainfo(
            "comms_telegram.transport.call_placed",
            user_id=user_id,
            transport="ExternalMedia",
            sample_rate=OUTBOUND_SAMPLE_RATE_HZ,
            channels=OUTBOUND_CHANNELS,
        )

    def _send_loop_thread(self) -> None:
        """Dedicated OS thread: sends audio to ntgcalls at strict 10 ms cadence.

        Drains _send_deque (24 kHz mono PCM16 enqueued by send_pcm()),
        upsamples to 48 kHz mono, assembles 10 ms frames (960 bytes), and
        submits send_frame() to the event loop via run_coroutine_threadsafe.
        Sends silence when the deque runs dry so ntgcalls stays fed between
        speech turns.

        Owning the clock in an OS thread (not asyncio) is the key: Python's
        event-loop scheduling jitter never reaches the WebRTC encoder, so
        frames arrive at exactly 10 ms intervals from ntgcalls' perspective.
        """
        from pytgcalls.types import Device, Frame

        accumulator = bytearray()
        next_tick = time.monotonic() + _SEND_INTERVAL_S

        while not self._send_stop.is_set():
            # Drain deque into accumulator, upsampling to 48kHz as we go.
            with self._send_lock:
                while self._send_deque:
                    raw = self._send_deque.popleft()
                    accumulator.extend(upsample_24k_mono_to_48k_mono(raw))

            # Assemble one 10ms frame (960 bytes at 48kHz mono). Send silence
            # if the deque ran dry -- keeps ntgcalls' jitter buffer stable.
            if len(accumulator) >= _SEND_FRAME_BYTES:
                frame = bytes(accumulator[:_SEND_FRAME_BYTES])
                del accumulator[:_SEND_FRAME_BYTES]
            else:
                frame = b"\x00" * _SEND_FRAME_BYTES

            loop = self._loop
            call_py = self._call_py
            active_id = self._active_user_id
            if (
                call_py is not None
                and active_id is not None
                and loop is not None
                and not loop.is_closed()
            ):
                try:
                    asyncio.run_coroutine_threadsafe(
                        call_py.send_frame(  # type: ignore[attr-defined]
                            active_id,
                            Device.MICROPHONE,
                            frame,
                            Frame.Info(capture_time=int(time.time() * 1000)),
                        ),
                        loop,
                    )
                except RuntimeError:
                    break  # loop is closing

            # Sleep until the next 10 ms boundary.
            now = time.monotonic()
            sleep_s = next_tick - now
            next_tick += _SEND_INTERVAL_S
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                # Behind schedule -- reset clock rather than spinning.
                next_tick = time.monotonic() + _SEND_INTERVAL_S

    async def _heartbeat_loop(self) -> None:
        while not self._ended.is_set():
            await asyncio.sleep(2.0)
            if self._ended.is_set():
                return
            peer_rms = self._peer_rms_sum / self._peer_rms_count if self._peer_rms_count else 0.0
            mic_rms = self._mic_rms_sum / self._mic_rms_count if self._mic_rms_count else 0.0
            with self._send_lock:
                backlog = sum(len(c) for c in self._send_deque)
                dropped = self._outbound_dropped_bytes
                self._outbound_dropped_bytes = 0
            await logger.ainfo(
                "comms_telegram.transport.heartbeat",
                peer_frames=self._peer_frames_received,
                peer_bytes=self._peer_bytes_received,
                peer_mean_rms=round(peer_rms, 1),
                mic_chunks=self._sent_count,
                mic_mean_rms=round(mic_rms, 1),
                outbound_backlog_bytes=backlog,
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

    async def send_pcm(self, pcm_24k_mono: bytes) -> None:
        """Queue a PCM chunk for the send thread.

        The send thread drains _send_deque, upsamples to 48 kHz, and
        calls send_frame() at exactly 10 ms intervals. A ~200 ms
        backlog cap prevents unbounded growth if the thread falls behind.
        """
        if not pcm_24k_mono or self._active_user_id is None:
            return
        with self._send_lock:
            was_first = self._sent_count == 0
            self._sent_count += 1
            self._mic_rms_sum += _rms_pcm16(pcm_24k_mono)
            self._mic_rms_count += 1
            self._send_deque.append(pcm_24k_mono)
            total = sum(len(c) for c in self._send_deque)
            dropped = 0
            while total > _OUTBOUND_MAX_BYTES and self._send_deque:
                removed = self._send_deque.popleft()
                total -= len(removed)
                dropped += len(removed)
            if dropped:
                self._outbound_dropped_bytes += dropped
        if was_first:
            await logger.ainfo(
                "comms_telegram.transport.first_send_pcm",
                chunk_bytes=len(pcm_24k_mono),
            )

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
        """Hang up and clean up the send thread.

        Bounded by hard timeouts so an ntgcalls-side hang (which we've
        observed -- `leave_call` can block indefinitely if the peer
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
        self._send_stop.set()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._heartbeat_task
            self._heartbeat_task = None
        if self._send_thread is not None:
            await asyncio.to_thread(self._send_thread.join, 2.0)
            self._send_thread = None
        self._loop = None
        self._active_user_id = None
        self._inbound_queue = None
        with self._send_lock:
            self._send_deque.clear()
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

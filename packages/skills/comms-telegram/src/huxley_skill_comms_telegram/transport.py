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
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()

# Huxley internal audio format — PCM16 mono at 24 kHz. Also what we
# tell ntgcalls to produce on the outbound path (no resampling needed).
HUXLEY_SAMPLE_RATE_HZ = 24_000
HUXLEY_CHANNELS = 1
BYTES_PER_SAMPLE = 2

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
        # tracks total chunks pushed so we can log first-chunk arrival.
        self._outbound_chunks: list[bytes] = []
        self._outbound_lock = threading.Lock()
        self._sent_count = 0

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
        """Dial the peer and open the outbound FIFO + writer thread.

        Returns when `play()` returns — at that point the call is
        connected on the WebRTC layer. Peer audio may arrive before
        or after; the handler queues it either way.
        """
        if self._active_user_id is not None:
            msg = f"place_call: already in a call with user_id={self._active_user_id}"
            raise TransportError(msg)
        if self._call_py is None:
            msg = "place_call() before connect()"
            raise TransportError(msg)

        from ntgcalls import MediaSource
        from pytgcalls.types import RecordStream
        from pytgcalls.types.raw import AudioParameters, AudioStream, Stream

        # FIFO path — deterministic per process so cleanup can find it.
        fifo = Path(f"/tmp/huxley_comms_mic_{os.getpid()}.pcm")
        with contextlib.suppress(FileNotFoundError):
            fifo.unlink()
        os.mkfifo(str(fifo))
        # O_RDWR avoids the "reader opens, writer races, reader sees
        # EOF" pitfall that cost us 2 spike rounds — see research doc.
        fd = os.open(str(fifo), os.O_RDWR)
        # Prefill with 80 ms of silence so ffmpeg's first read returns
        # immediately with valid bytes.
        silence = b"\x00\x00" * (HUXLEY_SAMPLE_RATE_HZ * 80 // 1000)
        os.write(fd, silence)
        self._mic_fd = fd
        self._mic_fifo = fifo

        # Start the writer thread BEFORE dialing. play() blocks the
        # event loop for several seconds during WebRTC handshake; if
        # the writer were an asyncio task it would starve ffmpeg,
        # which would exit on empty input, which would stall the call.
        self._writer_stop.clear()
        thread = threading.Thread(
            target=self._writer_loop,
            args=(fd,),
            daemon=True,
            name="comms-telegram-writer",
        )
        thread.start()
        self._writer_thread = thread

        # Raw Stream + MediaSource.SHELL — bypasses MediaStream's
        # check_stream() which would run ffprobe on the FIFO and hang.
        shell = (
            f"ffmpeg -f s16le -ar {HUXLEY_SAMPLE_RATE_HZ} -ac {HUXLEY_CHANNELS} "
            f"-i {fifo} "
            f"-f s16le -ar {HUXLEY_SAMPLE_RATE_HZ} -ac {HUXLEY_CHANNELS} -v quiet pipe:1"
        )
        out_stream = Stream(
            microphone=AudioStream(
                MediaSource.SHELL,
                shell,
                AudioParameters(HUXLEY_SAMPLE_RATE_HZ, HUXLEY_CHANNELS),
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

        await logger.ainfo(
            "comms_telegram.transport.call_placed",
            user_id=user_id,
            fifo=str(fifo),
        )

    def send_pcm(self, pcm_24k_mono: bytes) -> None:
        """Queue a PCM chunk for outbound send. Safe to call from an
        asyncio coroutine — the actual write happens in the writer
        thread. Zero-length chunks are ignored.
        """
        if not pcm_24k_mono:
            return
        with self._outbound_lock:
            was_first = self._sent_count == 0
            self._outbound_chunks.append(pcm_24k_mono)
            self._sent_count += 1
        if was_first:
            # Fire-and-forget log — can't await from a sync function,
            # but structlog's sync logger is fine from any thread.
            structlog.get_logger().info(
                "comms_telegram.transport.first_send_pcm",
                chunk_bytes=len(pcm_24k_mono),
            )

    def _writer_loop(self, fd: int) -> None:
        """Drain `_outbound_chunks` to the FIFO. Pads with silence if
        the producer is slower than ffmpeg's read pace.

        The FIFO buffer in the kernel is ~64 KB on macOS; if we fall
        behind we lose nothing — ffmpeg just reads silence until we
        catch up. If we get ahead, write() blocks briefly (non-fatal).
        """
        # 20 ms worth of silence at Huxley's rate — 480 samples = 960 bytes.
        silence_chunk = b"\x00\x00" * (HUXLEY_SAMPLE_RATE_HZ * 20 // 1000)
        while not self._writer_stop.is_set():
            with self._outbound_lock:
                chunk: bytes = (
                    self._outbound_chunks.pop(0) if self._outbound_chunks else silence_chunk
                )
            try:
                os.write(fd, chunk)
            except BrokenPipeError:
                # ffmpeg closed — call ended. Stop cleanly.
                break
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
        """Hang up and clean up the outbound FIFO + writer thread."""
        if self._active_user_id is None:
            return
        user_id = self._active_user_id
        await logger.ainfo("comms_telegram.transport.ending_call", user_id=user_id)

        from pytgcalls.exceptions import NotInCallError

        if self._call_py is not None:
            with contextlib.suppress(NotInCallError, Exception):
                await self._call_py.leave_call(user_id)  # type: ignore[attr-defined]

        await self._tear_down_call()

    async def _tear_down_call(self) -> None:
        self._ended.set()
        self._writer_stop.set()
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=2.0)
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

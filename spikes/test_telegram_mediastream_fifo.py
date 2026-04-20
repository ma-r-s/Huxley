"""Telegram live-PCM spike via MediaStream+FIFO+ffmpeg (T1.10 attempt 4).

Reasoning: the only outbound path proven working on p2p is
`MediaStream(path_to_wav_file)` — the very first spike. That path
internally compiles to `AudioStream(MediaSource.SHELL, 'ffmpeg -i
<path> ...', ...)`, i.e. an ffmpeg subprocess that decodes the file
and pipes PCM to ntgcalls.

If we point that ffmpeg at a Unix FIFO + pass `-f s16le -ar 24000
-ac 1` via `ffmpeg_parameters`, ffmpeg treats the FIFO as a live raw
PCM16 source. Python writes to the FIFO at real-time pace; ffmpeg
block-reads and pipes to ntgcalls; peer hears the stream. No
`send_frame`, no `ExternalMedia` — we use the one code path that has
proven working on p2p for us, just adapted to live input.

For inbound we try `record()` at AudioQuality.HIGH (48 kHz stereo)
to rule out a 24 kHz-mono internal resampling bug — spike 2 and 3
both returned zero-filled frames on inbound at 24 kHz mono, which
smells like an internal resampler issue.

NTGCALLS_LOG_LEVEL=VERBOSE env var is set by the wrapper so we
actually see what the C++ layer is doing if anything still fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import struct
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_DIR = REPO_ROOT / "spikes"
SESSION_NAME = "huxley_userbot"

# Outbound — Huxley's native rate. ffmpeg converts to whatever Telegram needs.
OUT_SAMPLE_RATE_HZ = 24_000
OUT_CHANNELS = 1
OUT_FRAME_MS = 20
OUT_FRAME_SAMPLES = OUT_SAMPLE_RATE_HZ * OUT_FRAME_MS // 1000

# Inbound — use HIGH quality so we let ntgcalls deliver in its most-native
# shape. If this returns non-zero PCM, we know the 24 kHz mono inbound
# path specifically is broken and just downsample in Python after.
IN_SAMPLE_RATE_HZ = 48_000
IN_CHANNELS = 2

CALL_DURATION_S = 18.0

MIC_FIFO = Path("/tmp/huxley_spike_mediastream_mic.pcm")


def _load_creds() -> tuple[int, str, str]:
    tg = (REPO_ROOT / "telegram").read_text()
    api_id = int(re.search(r"App api_id:\s*\n\s*(\d+)", tg).group(1))
    api_hash = re.search(r"App api_hash:\s*\n\s*([a-f0-9]+)", tg).group(1)
    phones = (REPO_ROOT / "telegram.phones").read_text()
    target = re.search(r"^TARGET_PHONE=(\S+)", phones, re.MULTILINE).group(1)
    return api_id, api_hash, target


def _make_fresh_fifo(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    os.mkfifo(str(path))


def _rms_pcm16(data: bytes) -> float:
    if len(data) < 2:
        return 0.0
    n = len(data) // 2
    samples = struct.unpack(f"<{n}h", data[: n * 2])
    sq_sum = sum(s * s for s in samples)
    return math.sqrt(sq_sum / n)


def _sine_writer_thread(fd: int, stop: threading.Event, stats: dict) -> None:
    """Write a rising sine sweep to the FIFO in a real OS thread.

    Runs in its own thread (not asyncio task) because pytgcalls' play()
    calls into C++ ntgcalls which can block the event loop. A thread
    runs independently and keeps feeding ffmpeg regardless of what
    the event loop is doing.
    """
    phase = 0.0
    stream_start = time.monotonic()
    next_write_at = stream_start
    last_heartbeat = stream_start
    while not stop.is_set():
        elapsed = time.monotonic() - stream_start
        freq = 440.0 + (880.0 - 440.0) * (elapsed / 10.0) if elapsed < 10.0 else 880.0
        omega = 2 * math.pi * freq / OUT_SAMPLE_RATE_HZ
        buf = bytearray()
        for _ in range(OUT_FRAME_SAMPLES):
            v = int(math.sin(phase) * 10_000)
            buf.extend(struct.pack("<h", v))
            phase += omega
            if phase > 2 * math.pi:
                phase -= 2 * math.pi
        try:
            os.write(fd, bytes(buf))
            stats["frames"] += 1
            stats["bytes"] += len(buf)
        except BrokenPipeError:
            print("[mic] ffmpeg closed the pipe; stopping writer", flush=True)
            break
        except BlockingIOError:
            time.sleep(OUT_FRAME_MS / 1000)
            continue

        now = time.monotonic()
        if now - last_heartbeat > 2.0:
            print(
                f"[mic] heartbeat: {stats['frames']} frames "
                f"({stats['bytes']} bytes) @ {elapsed:.1f}s",
                flush=True,
            )
            last_heartbeat = now

        next_write_at += OUT_FRAME_MS / 1000
        time.sleep(max(0.0, next_write_at - time.monotonic()))


async def main() -> None:
    api_id, api_hash, target_phone = _load_creds()

    from ntgcalls import MediaSource
    from pyrogram import Client
    from pytgcalls import PyTgCalls
    from pytgcalls import filters as fl
    from pytgcalls.exceptions import NotInCallError
    from pytgcalls.types import Device, Direction, RecordStream
    from pytgcalls.types.raw import AudioParameters, AudioStream, Stream

    _make_fresh_fifo(MIC_FIFO)
    # Open the FIFO O_RDWR BEFORE ffmpeg starts. Side effects:
    #   - Non-blocking (RDWR on a FIFO doesn't wait for another opener).
    #   - Guarantees a writer is always attached from Python's POV.
    #     ffmpeg's initial read never sees EOF → doesn't exit early.
    # Without this, ffmpeg wins the open race, reads zero bytes, and
    # treats it as end-of-stream.
    mic_fd = os.open(str(MIC_FIFO), os.O_RDWR)
    print(f"[setup] FIFO ready (fd={mic_fd}): {MIC_FIFO}", flush=True)

    app = Client(
        SESSION_NAME,
        api_id=api_id,
        api_hash=api_hash,
        workdir=str(SESSION_DIR),
    )
    call_py = PyTgCalls(app)

    received: dict[str, int | float] = {
        "frames": 0,
        "bytes": 0,
        "rms_accum": 0.0,
        "first_frame_at": -1.0,
    }
    call_start = [0.0]
    first_logged = [False]

    @call_py.on_update(fl.stream_frame(devices=Device.MICROPHONE | Device.SPEAKER))
    async def on_peer_audio(_, update) -> None:  # type: ignore[no-untyped-def]
        if update.direction != Direction.INCOMING:
            return
        if not first_logged[0]:
            received["first_frame_at"] = round(time.monotonic() - call_start[0], 3)
            first_logged[0] = True
            print(
                f"[recv] first peer-audio frame at +{received['first_frame_at']}s",
                flush=True,
            )
        for frame in update.frames:
            received["frames"] = int(received["frames"]) + 1
            received["bytes"] = int(received["bytes"]) + len(frame.frame)
            received["rms_accum"] = float(received["rms_accum"]) + _rms_pcm16(frame.frame)

    await call_py.start()

    print(f"[call] Resolving {target_phone} ...", flush=True)
    users = await app.get_users(target_phone)
    target = users[0] if isinstance(users, list) else users
    target_id = target.id
    print(f"[call] Target = {target.first_name} (user_id={target_id})", flush=True)

    await app.send_message(
        target_id,
        "🧪 Huxley FIFO-via-MediaStream spike. Answer and you should hear a "
        "rising sine tone (440→880Hz over 10s). Then say a few words; the "
        "spike reads peer audio at HIGH quality (48k stereo).",
    )

    # Pre-fill the FIFO with a chunk of silence so ffmpeg has bytes
    # to read the moment it opens the file. Without this, ffmpeg's
    # first read could return 0 bytes and trigger the "reader thinks
    # it hit EOF" exit path before our writer thread produces a frame.
    prefill = b"\x00\x00" * OUT_FRAME_SAMPLES * 4  # 80ms of silence
    os.write(mic_fd, prefill)
    print(f"[setup] pre-filled FIFO with {len(prefill)} bytes", flush=True)

    stop = threading.Event()
    mic_stats = {"frames": 0, "bytes": 0}
    mic_thread = threading.Thread(
        target=_sine_writer_thread,
        args=(mic_fd, stop, mic_stats),
        daemon=True,
        name="sine-writer",
    )
    mic_thread.start()
    print("[mic] writer thread started", flush=True)

    # Outbound via raw Stream + MediaSource.SHELL. We build the ffmpeg
    # command explicitly so (a) ntgcalls skips `check_stream()` (which
    # runs ffprobe on the input path and hangs on a FIFO that lacks a
    # recognizable header), and (b) we have precise control over the
    # decode flags (`-f s16le -ar 24000 -ac 1` telling ffmpeg how to
    # interpret the FIFO bytes).
    shell_cmd = (
        f"ffmpeg -f s16le -ar {OUT_SAMPLE_RATE_HZ} -ac {OUT_CHANNELS} "
        f"-i {MIC_FIFO} "
        f"-f s16le -ar {OUT_SAMPLE_RATE_HZ} -ac {OUT_CHANNELS} -v quiet pipe:1"
    )
    out_stream = Stream(
        microphone=AudioStream(
            MediaSource.SHELL,
            shell_cmd,
            AudioParameters(OUT_SAMPLE_RATE_HZ, OUT_CHANNELS),
        ),
    )
    print(f"[setup] SHELL cmd: {shell_cmd}", flush=True)

    call_start[0] = time.monotonic()
    try:
        print("[call] Dialing (MediaStream + FIFO + ffmpeg)...", flush=True)
        await call_py.play(target_id, out_stream)
        print(
            f"[call] play() returned after {(time.monotonic() - call_start[0]) * 1000:.0f} ms",
            flush=True,
        )

        # Subscribe to peer audio at HIGH quality (48k stereo) — rules
        # out the 24k-mono inbound resampler as a silent-frames culprit.
        await call_py.record(
            target_id,
            RecordStream(
                audio=True,
                audio_parameters=AudioParameters(IN_SAMPLE_RATE_HZ, IN_CHANNELS),
            ),
        )
        print("[call] RecordStream active at 48k stereo", flush=True)

        await asyncio.sleep(CALL_DURATION_S)
    except Exception as exc:
        print(f"[call] ERROR: {type(exc).__name__}: {exc}", flush=True)
        import traceback

        traceback.print_exc()
    finally:
        stop.set()
        mic_thread.join(timeout=2.0)
        with contextlib.suppress(OSError):
            os.close(mic_fd)

        with contextlib.suppress(NotInCallError, Exception):
            await call_py.leave_call(target_id)
        with contextlib.suppress(Exception):
            await app.stop()

    frames = int(received["frames"])
    rms = float(received["rms_accum"]) / frames if frames else 0.0
    summary = {
        "outbound": {
            "mic_frames_written": int(mic_stats["frames"]),
            "mic_bytes_written": int(mic_stats["bytes"]),
        },
        "inbound": {
            "peer_frames_received": frames,
            "peer_bytes_received": int(received["bytes"]),
            "peer_audio_mean_rms": round(rms, 1),
            "peer_audio_nonzero": rms > 50.0,
            "first_peer_frame_at_s": float(received["first_frame_at"]),
        },
        "format": {
            "outbound": f"{OUT_SAMPLE_RATE_HZ}Hz {OUT_CHANNELS}ch @ {OUT_FRAME_MS}ms",
            "inbound": f"{IN_SAMPLE_RATE_HZ}Hz {IN_CHANNELS}ch (HIGH quality)",
        },
    }
    print("\n[spike] --- RESULT ---", flush=True)
    print(json.dumps(summary, indent=2), flush=True)

    with contextlib.suppress(FileNotFoundError):
        MIC_FIFO.unlink()


if __name__ == "__main__":
    asyncio.run(main())

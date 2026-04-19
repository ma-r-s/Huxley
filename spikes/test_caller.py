"""Stand-in for Mario's web-app phone UI — smoke-test the calls skill.

Run while the Huxley server is up (`cd packages/core && uv run huxley`).
Triggers a ring, opens the caller WebSocket, streams a sine tone as
"caller voice," prints incoming PCM stats from grandpa's mic. Ctrl-C
to hang up.

    cd /Users/mario/Projects/Personal/Code/Huxley
    uv run python spikes/test_caller.py
    # or override the defaults:
    uv run python spikes/test_caller.py --from Mama --secret hunter2

Expected flow against AbuelOS persona:

1. POST /call/ring fires → server logs `calls.ring_accepted from_name=Mario`
   → coordinator inject_turn fires the Spanish announcement
2. Grandpa's web client (http://localhost:5173 in another window) hears
   "Llamada de Mario, contestando en tres, dos, uno..."
3. LLM dispatches `answer_call` → `InputClaim` latches → this script
   starts receiving grandpa's mic PCM (printed as "rx" lines), and
   the sine tone we send shows up as the "caller's voice" in
   grandpa's speakers
4. Hang up: Ctrl-C closes the WS. Server logs
   `calls.caller_disconnected`. Grandpa-side, the claim ends — today
   via the workaround (PTT or PREEMPT inject) since the skill can't
   yet cancel the claim from caller-WS-close (Stage 2.1 work).

NOT a Pytest test — this hits a real server + a real OpenAI session.
Lives under spikes/ so it's not auto-collected.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import struct
import sys
import time
from pathlib import Path

import websockets

# Reuse the same env-loading shape as realtime_suspend.py. The default
# secret matches AbuelOS persona.yaml's placeholder, so a fresh checkout
# works without env setup; production deployments should set
# HUXLEY_CALLS_SECRET to override.
ENV_PATH = Path(__file__).resolve().parent.parent / "packages" / "core" / ".env"
if ENV_PATH.exists():
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

DEFAULT_SECRET = os.environ.get("HUXLEY_CALLS_SECRET", "change-me-set-HUXLEY_CALLS_SECRET-env")
DEFAULT_HOST = os.environ.get("HUXLEY_TEST_CALLER_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("HUXLEY_SERVER_PORT", "8765"))

SAMPLE_RATE = 24_000  # Matches the device + OpenAI Realtime PCM16 contract.
FRAME_DURATION_MS = 50  # ~typical mic upload cadence
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_DURATION_MS // 1000  # 1200
BYTES_PER_FRAME = SAMPLES_PER_FRAME * 2  # PCM16


_START = time.monotonic()


def _log(tag: str, msg: str, **kv: object) -> None:
    extras = " ".join(f"{k}={v}" for k, v in kv.items())
    print(f"[{time.monotonic() - _START:7.3f}s] {tag:18s} {msg} {extras}".rstrip())


def _sine_frame(
    phase: float, freq_hz: float = 440.0, amplitude: int = 6000
) -> tuple[bytes, float]:
    """One PCM16 frame of a sine tone. Returns (frame_bytes, new_phase).

    Phase tracking matters across frames — if we recompute from sample
    index 0 each frame the waveform clicks at frame boundaries. Caller
    threads `phase` through.
    """
    samples = []
    omega = 2 * math.pi * freq_hz / SAMPLE_RATE
    for i in range(SAMPLES_PER_FRAME):
        v = int(math.sin(phase + omega * i) * amplitude)
        samples.append(struct.pack("<h", v))
    new_phase = (phase + omega * SAMPLES_PER_FRAME) % (2 * math.pi)
    return b"".join(samples), new_phase


async def _ring(host: str, port: int, secret: str, from_name: str) -> bool:
    """HTTP GET /call/ring. Returns True on 200, False otherwise.

    Raw asyncio TCP rather than urllib so we don't block the event loop
    (and don't pull in aiohttp just for one request)."""
    import urllib.parse

    path = f"/call/ring?{urllib.parse.urlencode({'from': from_name})}"
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"X-Shared-Secret: {secret}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
    except Exception as e:
        _log("ring", "connect_failed", error=str(e))
        return False
    try:
        writer.write(request.encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=5)
    except Exception as e:
        _log("ring", "io_failed", error=str(e))
        writer.close()
        return False
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode(errors="replace")
    parts = status_line.split(" ", 2)
    try:
        status = int(parts[1])
    except (IndexError, ValueError):
        _log("ring", "bad_response", line=status_line)
        return False
    _log("ring", f"HTTP {status}", body=body.decode(errors="replace").strip())
    return status == 200


async def _caller_ws_loop(host: str, port: int, secret: str) -> None:
    """Open WS /call, send sine-tone PCM frames at SAMPLE_RATE pace, log
    incoming PCM byte counts. Ctrl-C cleanly closes."""
    url = f"ws://{host}:{port}/call?secret={secret}"
    _log("ws", "connecting", url=url)
    try:
        ws = await websockets.connect(url, max_size=2 * 1024 * 1024)
    except Exception as e:
        _log("ws", "connect_failed", error=str(e))
        return
    _log("ws", "connected")

    sender_task: asyncio.Task[None] | None = None
    receiver_task: asyncio.Task[None] | None = None

    async def send_loop() -> None:
        """Stream a sine tone at real-time pace (one frame every 50 ms)."""
        phase = 0.0
        sent = 0
        next_send = time.monotonic()
        try:
            while True:
                frame, phase = _sine_frame(phase)
                await ws.send(frame)
                sent += len(frame)
                if sent % (BYTES_PER_FRAME * 40) == 0:
                    # Log roughly once per 2 seconds of audio sent.
                    _log("tx", "sine", total_kb=f"{sent / 1024:.1f}")
                next_send += FRAME_DURATION_MS / 1000
                await asyncio.sleep(max(0.0, next_send - time.monotonic()))
        except websockets.ConnectionClosed:
            _log("tx", "ws_closed")

    async def recv_loop() -> None:
        rx = 0
        rx_frames = 0
        last_log = time.monotonic()
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    rx += len(msg)
                    rx_frames += 1
                    now = time.monotonic()
                    if now - last_log >= 2.0:
                        _log("rx", "pcm", total_kb=f"{rx / 1024:.1f}", frames=rx_frames)
                        last_log = now
                else:
                    _log("rx", "text_frame_unexpected", value=str(msg)[:80])
        except websockets.ConnectionClosed:
            _log("rx", "ws_closed")

    sender_task = asyncio.create_task(send_loop(), name="caller_send")
    receiver_task = asyncio.create_task(recv_loop(), name="caller_recv")

    try:
        # Run until either side ends (Ctrl-C cancels both via the shielded
        # KeyboardInterrupt handling in main). Wait_first lets us notice
        # if the server closes first.
        done, pending = await asyncio.wait(
            [sender_task, receiver_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        for t in done:
            # Surface any unexpected exception.
            exc = t.exception()
            if exc is not None and not isinstance(exc, asyncio.CancelledError):
                _log("ws", "task_error", task=t.get_name(), error=str(exc))
    finally:
        await ws.close()
        _log("ws", "closed")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--from", dest="from_name", default="Mario", help="Caller name (default: Mario)"
    )
    parser.add_argument("--secret", default=DEFAULT_SECRET, help="Shared secret (env wins)")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--ring-only",
        action="store_true",
        help="Fire ring then exit; don't open caller WS (useful for the announcement-only smoke).",
    )
    parser.add_argument(
        "--ws-delay-s",
        type=float,
        default=0.5,
        help="Seconds to wait between ring + WS connect (default: 0.5). The "
        "skill expects WS to be available by the time answer_call dispatches; "
        "0.5s gives the LLM time to start the announcement.",
    )
    args = parser.parse_args()

    _log("init", "starting", host=args.host, port=args.port, caller=args.from_name)

    accepted = await _ring(args.host, args.port, args.secret, args.from_name)
    if not accepted:
        _log("init", "ring rejected — bailing")
        sys.exit(1)
    if args.ring_only:
        return

    await asyncio.sleep(args.ws_delay_s)
    await _caller_ws_loop(args.host, args.port, args.secret)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _log("init", "interrupted — bye")

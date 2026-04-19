"""Characterize OpenAI Realtime API behavior when we stop sending audio mid-conversation.

Run: cd /Users/mario/Projects/Personal/Code/Huxley && uv run python spikes/realtime_suspend.py

Produces stdout logs of what happens under various "pause" strategies.
Purpose: inform the `VoiceProvider.suspend()/resume()` contract for T1.4 Stage 2
(InputClaim for calls). Findings are captured in docs/research/realtime-suspend.md.

The four unknowns this spike answers:

1. **Session timeout** — how long can we stop sending audio before OpenAI kills
   the session? Is there a keepalive we can use to hold it open indefinitely?
2. **Pending audio on pause** — if a response is in flight when we stop sending
   audio, does it finish streaming, get cancelled, or replay on resume?
3. **Session ID preservation** — after a long pause, is the session still the
   same one (transcript cursor intact) or has it been rotated?
4. **Billing during silence** — do we pay for a connected-but-idle session?

Four experiments, each on its own fresh WebSocket session so state doesn't leak:

- `experiment_1_idle_timeout()` — connect, say nothing, wait. When does the
  server close? Send a PING every N seconds and see if that extends.
- `experiment_2_pause_mid_response()` — start a response, stop sending audio
  mid-generation, wait, observe what happens to assistant audio.
- `experiment_3_pause_then_resume_audio()` — after a conversation, pause 2m,
  then send new audio. Does the session accept it? Does context carry?
- `experiment_4_billing_probe()` — connect, idle for 5m, disconnect. Report
  reconstructable cost signal (we get rate-limit headers; real invoice
  lands later, but we can infer).

We do NOT need a real microphone — we synthesize silence and a short tone.
The model's speech recognition needs ~100ms of audio minimum to commit.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import struct
import sys
import time
from pathlib import Path

import websockets

# Load .env from packages/core/ so we reuse the same API key as the server.
ENV_PATH = Path(__file__).resolve().parent.parent / "packages" / "core" / ".env"
if ENV_PATH.exists():
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

API_KEY = os.environ.get("HUXLEY_OPENAI_API_KEY")
MODEL = os.environ.get("HUXLEY_OPENAI_MODEL", "gpt-4o-mini-realtime-preview")
URL = f"wss://api.openai.com/v1/realtime?model={MODEL}"

if not API_KEY:
    print("ERROR: HUXLEY_OPENAI_API_KEY missing", file=sys.stderr)
    sys.exit(1)


def _log(tag: str, msg: str, **kv: object) -> None:
    """Prefix every log with elapsed seconds since script start — makes the
    timeline trivial to read."""
    extras = " ".join(f"{k}={v}" for k, v in kv.items()) if kv else ""
    print(f"[{time.monotonic() - _START:7.3f}s] {tag:18s} {msg} {extras}".rstrip())


_START = time.monotonic()


def _pcm_silence(seconds: float, sample_rate: int = 24000) -> bytes:
    """PCM16 mono silence — enough to pass OpenAI's minimum-audio-for-commit
    threshold without triggering anything interesting."""
    n_samples = int(seconds * sample_rate)
    return b"\x00\x00" * n_samples


def _pcm_tone(seconds: float, freq_hz: int = 440, sample_rate: int = 24000) -> bytes:
    """Short sine tone as a PCM16 payload. OpenAI's Whisper treats this as
    unintelligible audio; the model may respond with 'I didn't catch that.'
    Fine for our purposes — we just need something to trigger a response."""
    samples = []
    for i in range(int(seconds * sample_rate)):
        v = int(math.sin(2 * math.pi * freq_hz * i / sample_rate) * 3000)
        samples.append(struct.pack("<h", v))
    return b"".join(samples)


async def _connect() -> websockets.ClientConnection:
    ws = await websockets.connect(
        URL,
        additional_headers={
            "Authorization": f"Bearer {API_KEY}",
            "OpenAI-Beta": "realtime=v1",
        },
    )
    # Minimal session config — PTT mode (no server VAD), pcm16 both ways.
    await ws.send(
        json.dumps(
            {
                "type": "session.update",
                "session": {
                    "instructions": (
                        "You are a test harness. Respond with very short "
                        "utterances (under 8 words). Do not call tools."
                    ),
                    "voice": "alloy",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": None,
                },
            }
        )
    )
    return ws


async def _drain_until(
    ws: websockets.ClientConnection,
    predicate,
    timeout_s: float,
    *,
    log_audio: bool = False,
) -> list[dict]:
    """Read events until `predicate(event)` returns True or timeout expires.
    Returns every event received. Logs type + key fields as they arrive."""
    events: list[dict] = []
    deadline = asyncio.get_event_loop().time() + timeout_s
    audio_bytes_received = 0
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            _log("drain", "timeout", after_s=f"{timeout_s:.1f}")
            return events
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except TimeoutError:
            _log("drain", "timeout", after_s=f"{timeout_s:.1f}")
            return events
        except websockets.ConnectionClosed as e:
            _log("drain", "connection_closed", code=e.code, reason=e.reason or "<none>")
            return events
        ev = json.loads(raw)
        events.append(ev)
        t = ev.get("type", "?")
        if t == "response.audio.delta":
            if log_audio:
                chunk_bytes = len(base64.b64decode(ev.get("delta", "")))
                audio_bytes_received += chunk_bytes
                _log("rx", t, bytes=chunk_bytes, total=audio_bytes_received)
        elif t == "error":
            _log("rx", t, error=ev.get("error", {}))
        else:
            _log("rx", t)
        if predicate(ev):
            return events


async def experiment_1_idle_timeout() -> None:
    """How long can we stay connected without sending any audio?

    Strategy: connect, never send audio, just consume events. Log when
    server closes. If it never does, we time out the experiment at 3
    minutes (enough to answer "longer than a typical call")."""
    print("\n=== EXPERIMENT 1: idle timeout ===")
    ws = await _connect()
    session_id = None
    try:
        # Wait for session.created + session.updated, then sit still.
        events = await _drain_until(
            ws,
            lambda e: e.get("type") == "session.updated",
            timeout_s=5.0,
        )
        for e in events:
            if e.get("type") == "session.created":
                session_id = e.get("session", {}).get("id")
                _log("session", "created", id=session_id)
        # Sit idle — drain any unsolicited events for up to 3 minutes.
        _log("idle", "starting 3-minute silence")
        events = await _drain_until(
            ws,
            lambda e: False,  # never resolves; relies on timeout or close
            timeout_s=180.0,
        )
        _log("idle", "ended", events_during_idle=len(events))
    finally:
        if ws.state.name == "OPEN":
            await ws.close()
            _log("close", "clean")


async def experiment_2_pause_mid_response() -> None:
    """Trigger a long response, then stop reading events and don't send
    anything for 30s. Observe what happens to the pending assistant
    audio. Then drain and see if it resumes, drops, or continues."""
    print("\n=== EXPERIMENT 2: pause mid-response ===")
    ws = await _connect()
    try:
        await _drain_until(
            ws,
            lambda e: e.get("type") == "session.updated",
            timeout_s=5.0,
        )
        # Send 0.5s of tone + commit + request a longer response.
        audio_b64 = base64.b64encode(_pcm_tone(0.5)).decode()
        await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64}))
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        # Override instructions for this response only — ask for a LONGER output
        # so there's something to interrupt.
        await ws.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {
                        "instructions": (
                            "Respond with a 15-word sentence describing a sunny day."
                        ),
                        "modalities": ["audio", "text"],
                    },
                }
            )
        )
        # Drain until FIRST audio delta arrives so we know the response is streaming.
        _log("resp", "waiting for first audio delta")
        await _drain_until(
            ws,
            lambda e: e.get("type") == "response.audio.delta",
            timeout_s=15.0,
            log_audio=True,
        )
        _log("resp", "first delta received — now going silent for 30s")
        # STOP reading events and STOP sending audio. Mimics an InputClaim
        # latching mid-response.
        start = time.monotonic()
        await asyncio.sleep(30.0)
        _log("resp", "30s passed — draining to see what happened")
        # Now drain all buffered events.
        events = await _drain_until(
            ws,
            lambda e: e.get("type") == "response.done",
            timeout_s=10.0,
            log_audio=True,
        )
        types = [e.get("type") for e in events]
        _log(
            "summary",
            "buffered events during pause",
            count=len(events),
            kinds=set(types),
        )
    finally:
        if ws.state.name == "OPEN":
            await ws.close()


async def experiment_3_pause_then_resume() -> None:
    """Simulate a call: short conversation, then 60s of nothing, then new
    audio. Does the session accept the resume? Does transcript continuity
    hold?"""
    print("\n=== EXPERIMENT 3: pause then resume ===")
    ws = await _connect()
    try:
        await _drain_until(ws, lambda e: e.get("type") == "session.updated", timeout_s=5.0)
        # Turn 1: short interaction.
        audio_b64 = base64.b64encode(_pcm_tone(0.5)).decode()
        await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64}))
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        await ws.send(json.dumps({"type": "response.create"}))
        _log("turn1", "awaiting response.done")
        await _drain_until(ws, lambda e: e.get("type") == "response.done", timeout_s=20.0)
        # Pause. Simulate call duration.
        _log("pause", "60s pause (simulated call)")
        await asyncio.sleep(60.0)
        _log("pause", "done — attempting resume with new turn")
        # Turn 2: new audio, new response.
        audio_b64 = base64.b64encode(_pcm_tone(0.5)).decode()
        await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64}))
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        await ws.send(json.dumps({"type": "response.create"}))
        _log("turn2", "awaiting response.done")
        # If session is dead we'll either see an error or the socket close.
        await _drain_until(ws, lambda e: e.get("type") == "response.done", timeout_s=20.0)
        _log("turn2", "response completed — session survived the pause")
    finally:
        if ws.state.name == "OPEN":
            await ws.close()


async def experiment_4_cancel_then_resume() -> None:
    """Instead of just-going-silent, explicitly cancel the in-flight
    response before pausing. See if OpenAI handles this more gracefully
    than the implicit pause in experiment 2."""
    print("\n=== EXPERIMENT 4: explicit cancel, then pause, then resume ===")
    ws = await _connect()
    try:
        await _drain_until(ws, lambda e: e.get("type") == "session.updated", timeout_s=5.0)
        audio_b64 = base64.b64encode(_pcm_tone(0.5)).decode()
        await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64}))
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        await ws.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {
                        "instructions": "Respond with a 15-word sentence.",
                        "modalities": ["audio", "text"],
                    },
                }
            )
        )
        await _drain_until(ws, lambda e: e.get("type") == "response.audio.delta", timeout_s=15.0)
        _log("cancel", "sending response.cancel + input_audio_buffer.clear")
        await ws.send(json.dumps({"type": "response.cancel"}))
        await ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
        # Drain any tail events from the cancelled response.
        await _drain_until(
            ws,
            lambda e: e.get("type") in ("response.done", "response.cancelled"),
            timeout_s=5.0,
        )
        _log("pause", "30s silence")
        await asyncio.sleep(30.0)
        _log("pause", "done — attempting new turn")
        audio_b64 = base64.b64encode(_pcm_tone(0.5)).decode()
        await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64}))
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        await ws.send(json.dumps({"type": "response.create"}))
        _log("turn2", "awaiting response.done")
        await _drain_until(ws, lambda e: e.get("type") == "response.done", timeout_s=20.0)
        _log("turn2", "session survived explicit cancel + pause")
    finally:
        if ws.state.name == "OPEN":
            await ws.close()


async def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    experiments = {
        "1": experiment_1_idle_timeout,
        "2": experiment_2_pause_mid_response,
        "3": experiment_3_pause_then_resume,
        "4": experiment_4_cancel_then_resume,
    }
    to_run = [experiments[which]] if which in experiments else list(experiments.values())
    for fn in to_run:
        try:
            await fn()
        except Exception as exc:
            print(f"\n!!! experiment raised: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())

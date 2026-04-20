"""Telegram voice-call spike — verify py-tgcalls end-to-end on macOS.

T1.10 pre-work. Decides whether `huxley-skill-comms-telegram` can use
py-tgcalls as its real-time voice transport. Informs the skill's
design (how it pairs with pyrogram, what audio format bridges to
Huxley's `InputClaim`, what failure modes to expect).

Run once to verify the install + auth + outbound-call path works on
this machine, then decide whether to invest in the full skill. If the
spike reveals dealbreakers (install fails on arm64, audio quality
bad, latency unacceptable, library crashes), fall back to Twilio.

Usage:

    # Phase 1 — interactive first-run auth (saves session file).
    # Mario must run this; pyrogram prompts for the SMS code Telegram
    # sent to the userbot's SIM. Once the session file exists,
    # subsequent runs skip the prompt.
    cd /Users/mario/Projects/Personal/Code/Huxley
    uv run python spikes/test_telegram_call.py auth

    # Phase 2 — place a test call. Userbot sends Mario a text asking
    # him to message back; once contact is established, it places a
    # 20-second call streaming a 440 Hz sine tone and hangs up.
    uv run python spikes/test_telegram_call.py call

Credentials are loaded from two gitignored root files:
  - `telegram` — Mario's export from my.telegram.org/apps (api_id + hash)
  - `telegram.phones` — USERBOT_PHONE + TARGET_PHONE
Both match gitignore patterns and never land in the repo.
"""

from __future__ import annotations

import asyncio
import math
import re
import struct
import sys
import time
from pathlib import Path

# ---------- Credential loading ----------

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_DIR = REPO_ROOT / "spikes"  # session file stored here (gitignored)
SESSION_NAME = "huxley_userbot"
TELEGRAM_FILE = REPO_ROOT / "telegram"
PHONES_FILE = REPO_ROOT / "telegram.phones"


def _parse_telegram_file() -> tuple[int, str]:
    """Extract api_id + api_hash from Mario's my.telegram.org/apps export.

    The export is a human-readable dump; we look for the two lines
    following the `App api_id:` / `App api_hash:` labels.
    """
    text = TELEGRAM_FILE.read_text()
    api_id_match = re.search(r"App api_id:\s*\n\s*(\d+)", text)
    api_hash_match = re.search(r"App api_hash:\s*\n\s*([a-f0-9]+)", text)
    if not api_id_match or not api_hash_match:
        msg = (
            f"Could not parse api_id/api_hash from {TELEGRAM_FILE}. "
            f"Is the my.telegram.org/apps export intact?"
        )
        raise RuntimeError(msg)
    return int(api_id_match.group(1)), api_hash_match.group(1)


def _parse_phones_file() -> tuple[str, str]:
    """Extract USERBOT_PHONE + TARGET_PHONE from the phones dotfile."""
    text = PHONES_FILE.read_text()
    userbot = re.search(r"^USERBOT_PHONE=(\S+)", text, re.MULTILINE)
    target = re.search(r"^TARGET_PHONE=(\S+)", text, re.MULTILINE)
    if not userbot or not target:
        msg = f"Could not parse USERBOT_PHONE/TARGET_PHONE from {PHONES_FILE}."
        raise RuntimeError(msg)
    return userbot.group(1), target.group(1)


# ---------- Sine-tone generator ----------


def _generate_tone_wav(path: Path, duration_s: float = 20.0, freq_hz: int = 440) -> None:
    """Write a WAV file containing a pure sine tone. py-tgcalls uses
    ffmpeg internally to transcode; a standard 16-bit PCM WAV at
    48 kHz is the boring happy-path input."""
    sample_rate = 48_000
    n_samples = int(duration_s * sample_rate)
    amplitude = 8000  # leave plenty of headroom; int16 max = 32767

    # 16-bit PCM WAV header (mono).
    byte_rate = sample_rate * 2  # 1 channel * 2 bytes per sample
    data_bytes = n_samples * 2
    chunk_size = 36 + data_bytes
    header = (
        b"RIFF"
        + struct.pack("<I", chunk_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + struct.pack("<H", 1)  # PCM
        + struct.pack("<H", 1)  # mono
        + struct.pack("<I", sample_rate)
        + struct.pack("<I", byte_rate)
        + struct.pack("<H", 2)  # block align
        + struct.pack("<H", 16)  # bits per sample
        + b"data"
        + struct.pack("<I", data_bytes)
    )
    samples = bytearray()
    omega = 2 * math.pi * freq_hz / sample_rate
    for i in range(n_samples):
        v = int(math.sin(omega * i) * amplitude)
        samples.extend(struct.pack("<h", v))
    path.write_bytes(header + bytes(samples))


# ---------- Auth phase ----------


async def auth_phase() -> None:
    """Run interactive first-run auth. Mario runs this himself; pyrogram
    prompts for the SMS code Telegram sends to the userbot's SIM. On
    success, `{SESSION_NAME}.session` is written to `spikes/` and
    subsequent runs skip the prompt."""
    api_id, api_hash = _parse_telegram_file()
    userbot_phone, _ = _parse_phones_file()

    # Local import so the script can be imported for quick syntax checks
    # even before `uv pip install pyrogram py-tgcalls` runs.
    from pyrogram import Client

    print(f"[auth] Starting pyrogram Client with api_id={api_id}, phone={userbot_phone}")
    print("[auth] Pyrogram will prompt for the SMS code Telegram sends to the SIM.")
    print("[auth] Type the code when asked, then any 2FA password if the account has one.")
    print()

    app = Client(
        SESSION_NAME,
        api_id=api_id,
        api_hash=api_hash,
        phone_number=userbot_phone,
        workdir=str(SESSION_DIR),
    )

    async with app:
        me = await app.get_me()
        print(f"[auth] Signed in as {me.first_name} (id={me.id}, phone={me.phone_number})")
        print(f"[auth] Session saved to {SESSION_DIR}/{SESSION_NAME}.session")


# ---------- Call phase ----------


async def call_phase() -> None:
    """Place a 20-second test call to TARGET_PHONE with a sine tone.

    Precondition: TARGET must have initiated contact with the userbot
    (sent one message) so pyrogram can resolve the target by phone
    number without peer-id errors. Script prints instructions if the
    contact isn't established yet."""
    api_id, api_hash = _parse_telegram_file()
    _, target_phone = _parse_phones_file()

    from pyrogram import Client
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream

    app = Client(
        SESSION_NAME,
        api_id=api_id,
        api_hash=api_hash,
        workdir=str(SESSION_DIR),
    )
    call_py = PyTgCalls(app)

    # Generate the tone once.
    tone_path = Path("/tmp/huxley_spike_tone.wav")
    print(f"[call] Generating 20s 440 Hz sine tone at {tone_path}")
    _generate_tone_wav(tone_path, duration_s=20.0, freq_hz=440)

    await call_py.start()
    try:
        # Resolve the target. Pyrogram's `get_users` with a phone number
        # only works if the contact is in the userbot's address book OR
        # the target messaged the userbot first. We handle both; if
        # neither has happened, print guidance and bail gracefully.
        print(f"[call] Resolving target {target_phone} → Telegram user_id")
        try:
            users = await app.get_users(target_phone)
        except Exception as exc:
            print(f"[call] get_users failed: {type(exc).__name__}: {exc}")
            print("[call] Ask Mario to send ANY message from his personal Telegram")
            print("[call]   (phone " + target_phone + ") to the userbot, then re-run.")
            return
        target = users[0] if isinstance(users, list) else users
        target_id = target.id
        print(f"[call] Target resolved: {target.first_name} (user_id={target_id})")

        # Announce the call so Mario knows what's coming.
        await app.send_message(
            target_id,
            "🧪 Huxley spike: the userbot is about to ring you. "
            "Answer and you'll hear a 440 Hz test tone for ~20 seconds. "
            "Any call quality notes (crackle, latency, dropout) are useful feedback.",
        )
        print("[call] Pre-call notice sent.")

        # Place the call. `play` in p2p mode with a user_id initiates an
        # outbound call if none is active; the target's Telegram client
        # rings like any other Telegram call.
        print("[call] Dialing...")
        start = time.monotonic()
        await call_py.play(target_id, MediaStream(str(tone_path)))
        dial_ms = (time.monotonic() - start) * 1000
        print(f"[call] Call accepted / streaming (dial+answer took {dial_ms:.0f}ms)")

        # Let the tone play. 20s of audio + a little slack.
        await asyncio.sleep(22)

        # Hang up defensively: if either side closed first (user tapped
        # end, or the stream ran out and ntgcalls auto-disconnected),
        # leave_call raises ConnectionNotFound / NotInCallError — which
        # is the OK case, not a failure.
        print("[call] Hanging up.")
        import contextlib as _ctx

        from pytgcalls.exceptions import NotInCallError

        with _ctx.suppress(NotInCallError, Exception):
            await call_py.leave_call(target_id)
        print("[call] Done. Ask Mario for quality notes.")

    finally:
        import contextlib

        # Ensure clean shutdown even if the call handshake / stream raised.
        with contextlib.suppress(Exception):
            await app.stop()


# ---------- Entry point ----------


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "auth":
        asyncio.run(auth_phase())
    elif mode == "call":
        asyncio.run(call_phase())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

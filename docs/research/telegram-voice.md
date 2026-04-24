# Telegram voice (py-tgcalls) — characterization

**Date**: 2026-04-19 · **Spike**: [`spikes/test_telegram_call.py`](../../spikes/test_telegram_call.py) · **Purpose**: decide whether `huxley-skill-comms-telegram` (T1.10) can use `py-tgcalls` as its real-time voice transport, and characterize the install / auth / call path well enough to design the skill around it.

## TL;DR — ship it

- **Install**: 4 seconds on macOS arm64; prebuilt wheels for every needed piece.
- **Auth**: one-shot interactive SMS-code flow; produces a 28 KB sqlite session file that works headless from then on.
- **P2P private calls**: supported in `py-tgcalls 2.2.11`. Same `play(chat_id, MediaStream(...))` API as group voice chats; p2p just means `chat_id` is a positive user id instead of a negative supergroup id.
- **Real call end-to-end verified**: outbound from the userbot (+57 315 328 3397) to Mario's personal Telegram account, rings like any other Telegram call, audio streams cleanly (440 Hz sine tone → Mario confirmed heard).
- **One library swap required**: mainline `pyrogram` 2.0.106 is missing `GroupcallForbidden` in its errors module; `py-tgcalls` fails to import against it. `kurigram` (maintained fork, pyrogram 2.2.x API) is a drop-in replacement and works. `pyrofork` (another fork) ships broken on PyPI — missing top-level `__init__.py`; avoid.
- **Recommendation**: commit to py-tgcalls + kurigram for T1.10. Skip Twilio unless operational issues emerge in real use.

## The four unknowns, answered

### 1. Does the install work on macOS arm64 (Mario's dev) and Linux arm64 (OrangePi5)?

**macOS arm64**: yes, clean install in ~4 seconds. `uv pip install py-tgcalls kurigram tgcrypto` pulled prebuilt wheels for every piece including the native C++/WebRTC backend (`ntgcalls-2.1.0`). No compilation, no build-from-source, no ffmpeg-headers-required footgun.

**Linux arm64**: not yet tested physically, but PyPI shows prebuilt wheels tagged `linux_aarch64` for both `ntgcalls` and `kurigram`. Should be the same 4-second install on the future OrangePi5. Re-run this spike on the actual device once it exists.

### 2. Can we authenticate a userbot and persist the session?

Yes. Pyrogram/kurigram's interactive flow:

1. `Client(phone_number=...)` → library sends a login code request to Telegram.
2. Telegram SMSs the code to the SIM behind the phone number.
3. Library prompts on stdin for the code; also prompts for 2FA password if the account has one.
4. On success, writes `huxley_userbot.session` (sqlite, ~28 KB) to the configured `workdir`.

From then on, any `Client(...)` init with the same `session_name` + `workdir` skips all of the above and just connects via the saved auth tokens. The session file is the identity — treat it like a private key (gitignored via `*.session` in this repo).

### 3. Does p2p private calling (user → user) actually work in py-tgcalls 2.2.x?

Yes. Confirmed by the `example/p2p_example/example_p2p.py` script in the upstream repo, and by this spike's successful test call.

API shape is identical to group voice chats:

```python
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream

call_py = PyTgCalls(pyrogram_client)
await call_py.start()
await call_py.play(target_user_id, MediaStream("/path/to/audio.wav"))
# ... call runs ...
await call_py.leave_call(target_user_id)
```

The only difference between a 1:1 private call and a group voice chat is what integer you pass as `chat_id`:

- **Positive** user_id (e.g. `7392572538`) → private p2p call; target's Telegram client rings like a phone call.
- **Negative** supergroup id (e.g. `-1001185324811`) → joins a voice chat in that chat.

### 4. What's the dial / answer / connection latency?

From this spike's successful run:

- **Dial to answer**: 5,216 ms measured from `play()` invocation to ntgcalls reporting the call connected and streaming. This includes:
  - Pyrogram → Telegram MTProto request
  - Telegram routing the call to the target's device
  - Target's phone ringing
  - Target's user tapping "answer"
  - WebRTC handshake completing
- Most of that 5s is human response time (Mario tapping answer). Pure protocol overhead is probably in the 500–1500ms range for a call-setup round-trip.
- **Audio quality (Mario's ear, reported post-call)**: "clean tone". No crackle, no dropouts, no encoder artifacts on a pure 440 Hz sine through Telegram's Opus pipeline. If the encoder had been mis-configured or the WAV format mis-matched, a sine wave is the audio that would most obviously break — it didn't, so the whole `WAV → ffmpeg → Opus → WebRTC → Telegram client → speaker` pipeline is healthy on the happy path.
- **Perceived stream-start latency (Mario's ear)**: "almost right away" — the tone began audibly within a sub-perceptual gap of pressing answer. No multi-second dead air between answer and audio, which would have indicated a handshake / buffering issue. For voice-conversation UX this is the key signal: the skill won't introduce noticeable gaps between "user is now on the call" and "grandpa's Huxley is streaming."

For reference, typical Telegram-to-Telegram call setup latency on a real user's device (from CLI to "phone ringing") is usually sub-second; the multi-second end-to-end in this spike is expected and matches normal phone-answer-time human behavior.

## The provider-Python-library ecosystem mess (important, write it down)

`py-tgcalls` 2.2.11 doesn't pin a specific MTProto library — it imports whichever of `pyrogram` / `telethon` / `hydrogram` is installed and adapts. **But it assumes the installed `pyrogram` is recent enough to include error classes like `GroupcallForbidden`**, which mainline pyrogram (2.0.106 on PyPI) hasn't shipped. Mainline pyrogram maintenance has been slow; py-tgcalls's code is written against a fork.

This means **choosing the MTProto library is a real decision for T1.10**, and the wrong choice is a hard `ImportError` at init time.

Forks tried in this spike:

| Package               | Result                                                                                                                                 |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `pyrogram` 2.0.106    | ❌ `ImportError: cannot import name 'GroupcallForbidden'` — mainline hasn't shipped that error class. Stale.                           |
| `pyrofork` 2.3.69     | ❌ Broken install from PyPI — top-level `pyrogram/__init__.py` missing; namespace-package collision. Unusable as installed.            |
| **`kurigram` 2.2.22** | ✅ Drop-in replacement; installs as the `pyrogram` package; has `GroupcallForbidden`; `PyTgCalls(Client(...))` works; spike succeeded. |

**Recommendation for the T1.10 skill**: pin `kurigram>=2.2,<3` in the skill's `pyproject.toml`. Avoid mainline `pyrogram` and `pyrofork`. Re-check every 6 months; if py-tgcalls moves to a different maintained fork as its reference, we follow.

## Operational concerns for the real skill (T1.10)

- **Userbot identity**: requires a real Telegram user account with a phone number. Mario used a spare SIM. The account exists as a regular Telegram user and shows up in contact lists with whatever profile name / avatar is set — worth setting both to "Huxley" or similar before deploying so the user sees a recognizable identity rather than a raw phone number.
- **Session file = identity**: back it up; if lost, re-auth is required (SMS code flow again). Moving the session to a new machine is a file copy.
- **Anti-abuse posture**: userbot pattern is "discouraged" in Telegram TOS but tolerated for non-spammy legitimate use. Family-only calls at human cadence are invisible to abuse heuristics. If an account ever gets restricted, recovery is a re-register on the same SIM; business continuity is fine for the shape of this use case.
- **No `BotFather` involvement**: userbots aren't Telegram bots; they use the full MTProto user API. Don't try to configure this through BotFather.
- **SMS code delivery reliability**: one-time concern at deployment. The spike's auth phase received the SMS within seconds on Tigo Colombia.

## API surface the skill will use

Validated working in the spike:

- `pyrogram.Client(session_name, api_id, api_hash, workdir=..., phone_number=...)` — pass phone_number only for first-run auth.
- `async with Client as app:` — context-managed session.
- `app.get_users(phone_str_or_user_id)` — resolves a target to a `User` object with `.id`. Requires that the target has some form of prior contact with the userbot (sent a message, is in the address book, etc.); a cold number the userbot has never seen will fail with a `PEER_ID_INVALID`.
- `app.send_message(user_id, text)` — standard text message. Useful for "I'm about to call you" heads-up notes.
- `pytgcalls.PyTgCalls(app)` — wraps the pyrogram client.
- `await call_py.start()` — spawns ntgcalls native threads.
- `await call_py.play(user_id, MediaStream(path_or_url))` — if no active call, initiates an outbound call and begins streaming. If a call is already active with that user, swaps the stream source.
- `pytgcalls.types.MediaStream(path, ...)` — wraps an audio/video source. Path can be a local file or an HTTP URL; ntgcalls uses ffmpeg internally to decode → Opus.
- `await call_py.leave_call(user_id)` — ends the call. Raises `NotInCallError` / `ConnectionNotFound` if already ended — **treat those as OK**, since the other side hanging up is a normal termination path.

Not yet tested but needed for the full skill:

- **Incoming call handler**: `@call_py.on_update(fl.chat_update(ChatUpdate.Status.INCOMING_CALL))` — fires when the userbot receives a call. The T1.10 skill uses this to bridge inbound calls from family members → device-side `InputClaim`. Worked in the reference `p2p_example.py` upstream; likely works, but this spike didn't exercise it.

## Bidirectional live-PCM on p2p — the working recipe (added 2026-04-19)

This is the pattern `huxley-skill-telegram` must use. Five spikes worth of learning compressed into one working sequence in [`spikes/test_telegram_mediastream_fifo.py`](../../spikes/test_telegram_mediastream_fifo.py).

**Outbound (Huxley → peer)**:

```python
from ntgcalls import MediaSource
from pytgcalls.types.raw import AudioParameters, AudioStream, Stream

mic_fifo = Path("/tmp/huxley_call_mic.pcm")
os.mkfifo(mic_fifo)
# O_RDWR (not O_WRONLY) before dialing: keeps a writer attached from
# Python's POV even before the app starts producing frames. Without it,
# ffmpeg's initial read sees 0 bytes → EOF → exits → call stalls.
mic_fd = os.open(mic_fifo, os.O_RDWR)
os.write(mic_fd, b"\x00\x00" * 24000 * 0.08)  # 80ms silence prefill

# Writer MUST be an OS thread, not an asyncio task. play() blocks the
# event loop in C++ for ~7s during WebRTC handshake; an asyncio writer
# would starve ffmpeg, and ffmpeg would exit on empty input.
threading.Thread(target=pcm_writer_loop, args=(mic_fd, ...), daemon=True).start()

# Use raw Stream + MediaSource.SHELL, NOT MediaStream. MediaStream runs
# check_stream() which invokes ffprobe on the FIFO path and hangs forever
# trying to sniff a header that doesn't exist.
shell = (
    f"ffmpeg -f s16le -ar 24000 -ac 1 -i {mic_fifo} "
    f"-f s16le -ar 24000 -ac 1 -v quiet pipe:1"
)
stream = Stream(
    microphone=AudioStream(MediaSource.SHELL, shell, AudioParameters(24000, 1)),
)
await call_py.play(peer_id, stream)
```

**Inbound (peer → Huxley)**:

```python
from pytgcalls.types import Device, Direction, RecordStream
from pytgcalls import filters as fl

# MUST be 48 kHz stereo, not 24 kHz mono. Requesting 24k mono from
# record() returns zero-filled frames (an ntgcalls internal-resampler
# bug on p2p). HIGH quality delivers real PCM; downsample to 24k mono
# in Python for the OpenAI Realtime session.
await call_py.record(
    peer_id,
    RecordStream(audio=True, audio_parameters=AudioParameters(48000, 2)),
)

@call_py.on_update(fl.stream_frame(devices=Device.MICROPHONE))
async def on_peer_audio(_, update):
    if update.direction != Direction.INCOMING:
        return
    for f in update.frames:
        pcm48k_stereo = f.frame
        # downsample to 24k mono -> forward to InputClaim
```

**Package pins** (update for bidir support):

- `py-tgcalls==2.2.11`
- `kurigram>=2.2,<3`
- **`ntgcalls>=2.2.1b2`** — earlier versions have [ntgcalls#44](https://github.com/pytgcalls/ntgcalls/issues/44), a PacedSender bug that silently drops outbound RTP on p2p calls. Our original spike worked on 2.1.0 because file playback hit a different code path; live-frame paths need the fix.

**Gotchas that cost time during this spike**:

1. `ExternalMedia.AUDIO + send_frame` — does NOT work on p2p. Claims success, frames never reach the peer, AND the act of calling `send_frame` suppresses inbound. Appears to be supergroup-only. Don't use.
2. `MediaStream(fifo_path, ffmpeg_parameters=...)` — hangs in `check_stream()` which runs ffprobe on the FIFO. Use raw `Stream(AudioStream(MediaSource.SHELL, ...))` to skip the probe.
3. Opening the FIFO `O_WRONLY | O_NONBLOCK` after ffmpeg starts — races with ffmpeg's first read. Use `O_RDWR` before dial and prefill with a chunk of silence.
4. Writing from an asyncio task — `play()` blocks the event loop during handshake, starving the task. Use a real OS thread; communicate via a thread-safe queue if needed.
5. `record()` at 24 kHz mono — returns all-zero PCM frames on p2p. Use 48 kHz stereo and resample in Python.

**Why so many false starts**: none of these are documented. Upstream examples all target group voice chats (negative chat IDs). The file-based playback we first tested hides the live-streaming gotchas because it doesn't need a real-time source. The skill pattern here is essentially discovered from source-reading + trial-and-error, not from any canonical example. If upstream adds a p2p live-PCM example we should update this doc.

## Next steps

1. **✅ Bidirectional live PCM proven working** (2026-04-19): outbound Mario heard clearly, inbound captured Mario's voice at RMS 314.9 through `record()` + `stream_frame`.
2. **Re-run the install-only path on the OrangePi5** when hardware arrives, to verify arm64 Linux parity.
3. **Start T1.10 skill implementation** (in progress): `huxley-skill-comms-telegram` package with pinned `kurigram>=2.2,<3` + `py-tgcalls==2.2.11` + `ntgcalls>=2.2.1b2`. Mirrors the skill pattern from `huxley-skill-timers`; adds the Telegram-specific transport code using the recipe above.

## Running the spike yourself

```bash
cd /Users/mario/Projects/Personal/Code/Huxley

# One-time: install the transient deps into the workspace venv.
uv pip install py-tgcalls kurigram tgcrypto

# Phase 1 — interactive first-run auth. Prompts for SMS code on the
# spare SIM; you type it in. Once done, `spikes/huxley_userbot.session`
# exists and Phase 2 runs headlessly.
uv run python spikes/test_telegram_call.py auth

# Precondition before Phase 2: from the TARGET phone's regular
# Telegram client, send ANY message ("hola") to the userbot account.
# This puts the target in the userbot's dialog history so
# get_users(phone) can resolve them.

# Phase 2 — place a 20-second test call streaming a 440 Hz sine tone
# to the target. Target rings, answers, hears the tone, call ends.
uv run python spikes/test_telegram_call.py call
```

Credential files (gitignored):

- `telegram` — export of the my.telegram.org/apps page (api_id + hash).
- `telegram.phones` — plain KEY=VALUE for `USERBOT_PHONE=+...` and `TARGET_PHONE=+...`.

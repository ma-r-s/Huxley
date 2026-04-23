# `huxley-skill-comms-telegram`

Places p2p Telegram voice calls to named contacts and bridges the call to grandpa's mic/speaker through the existing `InputClaim` plumbing. First consumer of `InputClaim` with a non-trivial `speaker_source` — proves out the "skill drives both directions of audio, framework provides the seat at the single microphone/speaker" design.

## What it does

**Outbound**: User says "llama a mi hija". The LLM dispatches `call_contact(name="hija")`. The skill looks up the contact in the persona config, resolves the phone number to a Telegram user_id via the userbot, places a p2p call, and returns a `ToolResult` with `side_effect=InputClaim(...)`. The framework latches the mic and speaker to the skill at the turn's terminal barrier.

**Inbound**: When a contact calls grandpa's userbot, the skill accepts immediately (any delay degrades WebRTC audio quality), announces the caller via `ctx.inject_turn_and_wait` (blocks until the LLM finishes speaking), then starts the `InputClaim` to bridge audio both ways.

For the duration of either call:

- Every PCM chunk from grandpa's microphone (~50 ms at 24 kHz mono) is forwarded into the Telegram call via a Unix FIFO + `ffmpeg` subprocess.
- Every peer-audio frame py-tgcalls delivers is downsampled from 48 kHz stereo to 24 kHz mono (via a pure Python helper) and yielded to the framework's speaker queue.

When the claim ends — grandpa presses PTT, a medication reminder preempts, the peer hangs up, an error fires — the skill hangs up the Telegram call via `on_claim_end`. Peer hangup is detected via ntgcalls' `DISCARDED_CALL` event, which causes `peer_audio_chunks()` to exhaust naturally; the speaker pump sees the iterator end and closes the claim with `NATURAL`.

## Tools

- **`call_contact(name: str)`** — place a call to the named contact. `name` is lowercased + whitespace-stripped before lookup, so user speech and persona config don't need to match case. Returns `{ok: true, contact: <name>}` on success; otherwise `{ok: false, error: "..."}` with an LLM-facing Spanish message explaining the failure ("no tengo a X en la lista", "no pude conectar la llamada").

## Persona config

The `skills.comms_telegram:` block in `persona.yaml`:

```yaml
skills:
  comms_telegram:
    api_id: 12345678 # int, from my.telegram.org/apps
    api_hash: "abcdef..." # 32-char hex
    userbot_phone: "+57…" # first-run auth only; persists in session file
    contacts:
      hija: "+57 318 685 1696"
      hijo: "+573001234567"
```

- **`api_id` / `api_hash`** _(required)_ — Telegram application credentials. Create once at [my.telegram.org/apps](https://my.telegram.org/apps) (see `docs/research/telegram-voice.md` for how to fill the form). Missing or wrong-type values make `setup()` raise — the skill refuses to register without them.
- **`userbot_phone`** _(optional except first run)_ — phone number of the spare SIM the userbot signs in as. Consulted only on first startup; from then on the sqlite session file in the persona data dir authenticates silently. If the session is deleted, set this again and the SMS-code flow fires once.
- **`contacts`** _(required for the skill to be useful)_ — name → phone mapping. Phones can be in any format; the skill normalizes (strips spaces, dashes, parens). Missing / empty / non-string values log a warning and get dropped.

## Architecture — how the audio actually flows

See `docs/research/telegram-voice.md` §"Bidirectional live-PCM on p2p" for the full spike story and every gotcha. Short version:

```
┌─────────────────┐   24k mono PCM      ┌───────────────────────────┐
│ grandpa mic     │  (50ms chunks)     │  TelegramTransport        │
│  → on_mic_frame │────────────────────▶│  .send_pcm()              │
└─────────────────┘                    │   │ queue to writer thread │
                                       │   ▼                        │
                                       │  OS thread writes PCM to   │
                                       │  FIFO (/tmp/huxley_comms_  │
                                       │  mic_<pid>.pcm, O_RDWR,    │
                                       │  silence-prefilled)        │
                                       │   │                        │
                                       │   ▼                        │
                                       │  ffmpeg subprocess         │
                                       │  (spawned by ntgcalls via  │
                                       │  MediaSource.SHELL) reads  │
                                       │  FIFO, pipes to ntgcalls   │
                                       │   │                        │
                                       │   ▼                        │
                                       │  ntgcalls → WebRTC → peer  │
                                       └───────────────────────────┘
                                                     │
                                                     │ 48k stereo PCM
                                                     ▼
                                       ┌───────────────────────────┐
                                       │ peer audio → stream_frame │
                                       │ handler (48k stereo)      │
                                       │   │                        │
                                       │   ▼                        │
                                       │ downsample_48k_stereo_to_  │
                                       │ 24k_mono() (pure Python:   │
                                       │ decimate by 2 + avg L/R)  │
                                       │   │                        │
                                       │   ▼                        │
                                       │ asyncio.Queue(maxsize=500)│
                                       │   │                        │
                                       │   ▼                        │
┌─────────────────┐   24k mono PCM      │  peer_audio_chunks()       │
│ grandpa speaker │◀────────────────────│  async generator           │
│ (speaker_source │                     └───────────────────────────┘
│  on InputClaim) │
└─────────────────┘
```

The pattern is shaped by five spike-learned gotchas — see the research doc for the forensics. In brief: p2p-private calls (positive user_id) are incompatible with `ExternalMedia.AUDIO` + `send_frame`, with `MediaStream(fifo_path, ...)` (ffprobe hangs on the FIFO), with a `Stream.speaker=...` sink (that slot is an input source, not a capture sink), and with `record()` at 24 kHz mono (returns zero-filled frames — an ntgcalls resampler bug). The recipe above avoids all of them.

## Package pins

```toml
dependencies = [
    "huxley-sdk",
    "kurigram>=2.2,<3",    # mainline pyrogram 2.0.x is missing GroupcallForbidden
    "py-tgcalls==2.2.11",
    "ntgcalls>=2.2.1b2",   # earlier versions have ntgcalls#44 (PacedSender bug)
    "tgcrypto",            # speeds up MTProto; optional but recommended
]
```

## Not yet implemented

- **Multiple concurrent calls** — transport enforces a single-call invariant. Second `call_contact` during an active call would currently fail inside `place_call` with a clean `TransportError`; a better shape would be to end the previous one first (opinion: probably not what grandpa wants either way — he'd get double-billed in mind-space).
- **Contact disambiguation** — two contacts named "hija" → only the last-loaded wins. Fine for v1 (unlikely with the target user), but might want a "did you mean…" flow later.

## Persona constraints this skill respects

See [`docs/skills/README.md`](./README.md#persona-constraints--what-your-skill-should-respect) for the constraints framework.

- **`never_say_no`** — the error messages the tool returns are LLM-facing prompts, not literal speech. The persona's `never_say_no` constraint applies to how the LLM renders them. We give the LLM enough context ("contacto no encontrado; contactos conocidos: X, Y, Z") that it can offer an alternative ("¿quieres llamar a Y?") instead of a raw refusal.

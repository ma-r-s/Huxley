# `huxley-skill-telegram`

Bridges Telegram into Huxley as a single shared transport for **voice calls** and **text messages**. One Pyrogram userbot session, one contacts whitelist, one auth context — both modes share the same `setup()`/`teardown()` lifecycle.

The voice-call half was the first consumer of `InputClaim` with a non-trivial `speaker_source`, proving out the "skill drives both directions of audio, framework provides the seat at the single microphone/speaker" design. The messaging half was the first real consumer of `inject_turn` for a non-timer use case — and the test case that surfaced the framework-level same-key inject drop-in-flight footgun (worked around at the skill layer with a per-sender debounce/coalesce buffer; see [Messaging](#messaging) below).

## What it does

### Calls

**Outbound**: User says "llama a mi hija". The LLM dispatches `call_contact(name="hija")`. The skill looks up the contact in the persona config, resolves the phone number to a Telegram user_id via the userbot, places a p2p call, and returns a `ToolResult` with `side_effect=InputClaim(...)`. The framework latches the mic and speaker to the skill at the turn's terminal barrier.

**Inbound**: When a contact calls grandpa's userbot, the skill accepts immediately (any delay degrades WebRTC audio quality), announces the caller via `ctx.inject_turn_and_wait` (blocks until the LLM finishes speaking), then starts the `InputClaim` to bridge audio both ways.

For the duration of either call:

- Every PCM chunk from grandpa's microphone (~50 ms at 24 kHz mono) is forwarded into the Telegram call via a Unix FIFO + `ffmpeg` subprocess.
- Every peer-audio frame py-tgcalls delivers is downsampled from 48 kHz stereo to 24 kHz mono (via a pure Python helper) and yielded to the framework's speaker queue.

When the claim ends — grandpa presses PTT, a medication reminder preempts, the peer hangs up, an error fires — the skill hangs up the Telegram call via `on_claim_end`. Peer hangup is detected via ntgcalls' `DISCARDED_CALL` event, which causes `peer_audio_chunks()` to exhaust naturally; the speaker pump sees the iterator end and closes the claim with `NATURAL`.

**UI surface during a call**: the skill sets `InputClaim.title=<contact_name>` so UI-capable clients can render "Hablando con Mario" as the status (huxley-web does; the ESP32 audio-only client ignores it). The orb on huxley-web also drives its animation from the real peer audio during `live` orb state, so the ring reacts to the other person's voice in real time. See `docs/protocol.md` for the `claim_started` wire payload.

**Single-slot policy**: while a call is active, a second inbound ring raises `ClaimBusyError` at the coordinator. The skill catches it and sends `reject_call` to the peer (they get a BUSY signal). The existing transport-level `_active_user_id` check is still the first line of defense; `ClaimBusyError` is defense in depth.

**Claim-end narration**: on `NATURAL` claim end (peer hangup), the skill fires `inject_turn("la llamada con X terminó")` via `asyncio.create_task` — it cannot await inline because `on_claim_end` runs inside the FocusManager's actor callback chain; awaiting `inject_turn`'s `fm.wait_drained()` from there would deadlock on `Queue.join`. The `create_task` defers the inject to the next event-loop tick when the FM is idle. Fire-through (vs. queue) is guaranteed by the framework's `is_ended`-gated inject check — see [the focus-plane decisions ADR](../decisions.md#2026-04-24--post-smoke-test-fixes-to-the-focus-plane-is_ended-gate-playback-drain-wait-idle-inject-during-claim-claim-title-for-ui).

### Messaging

**Outbound**: User says "manda un mensaje a mi hija diciéndole que ya almorcé". The LLM dispatches `send_message(name="hija", text="Ya almorcé.")`. The skill validates the contact, validates the 4096-char Telegram cap, resolves the phone to a user_id, and calls `transport.send_text()`. Returns `{ok: true, contact, chars, sent_at}` so the LLM can confirm to the user.

**Inbound (proactive inbox)**: A Pyrogram `MessageHandler` (filters: `private & incoming` — the `incoming` filter is critical, otherwise every outbound `send_text` echoes back into the handler and triggers a feedback loop) is registered in the same `_wire_handlers` pass that wires the call observers, **before** `app.start()` so messages arriving in the first seconds aren't missed. Each inbound message is appended to a per-sender debounce buffer (`InboxBuffer`); the buffer fires `inject_turn(NORMAL, dedup_key=msg_burst:<user_id>)` after a configurable debounce window (default 2.5s) elapses with no further messages from that sender. Burst-y conversations ("hola"/"papá"/"¿estás?") collapse into ONE inject ("hija te envió 3 mensajes: '...', '...' y '...'") rather than three competing announcements.

`NORMAL` priority queues behind active calls (Stage 2b focus-plane behavior), so an inbound message during a phone call doesn't interrupt the call — it'll fire on the next quiet turn-end after the call ends.

**Backfill on connect**: On startup with `inbound.enabled`, the skill walks `get_dialogs()` for whitelisted contacts with non-zero `unread_messages_count`, fetches up to `backfill_max` (default 50) recent unread messages within the `backfill_hours` window (default 6h), groups them per-sender, and fires one summary inject ("Mientras no estabas, llegaron mensajes nuevos: hija (3) y hijo (1). ¿Te los leo?"). Closes the "server crashed at 3am, missed daughter's 4am 'are you ok?'" silent-loss gap that's particularly bad for a blind user with no notification surface.

## Tools

- **`call_contact(name: str)`** — place a call to the named contact. `name` is lowercased + whitespace-stripped before lookup, so user speech and persona config don't need to match case. Returns `{ok: true, contact: <name>}` on success; otherwise `{ok: false, error: "..."}` with an LLM-facing Spanish message explaining the failure ("no tengo a X en la lista", "no pude conectar la llamada").

- **`send_message(name: str, text: str)`** — send a Telegram text message. Same name normalization as `call_contact`. Validates the 4096-char per-message cap with a Spanish-friendly error ("muy largo, acórtalo o divídelo"). Returns `{ok: true, contact, chars, sent_at}` (UTC ISO-8601 timestamp, second precision); the LLM uses these to confirm or recover.

## Why a skill-side debounce buffer (and not just `dedup_key`)

The framework's `inject_turn(dedup_key=...)` collapses pending duplicates in the queue (last-writer-wins) but **silently drops** same-key calls that arrive while one with that key is already firing (`coordinator._current_injected_dedup_key`). For burst-y messaging this means the user hears the first message and silently loses the rest — a real safety gap.

The `InboxBuffer` solves this at the skill layer:

- **Per-sender state stays resident through the flush**, not popped before the task is spawned. Messages arriving during a flush append into the same state.
- **Post-flush hook**: when the in-flight flush completes, if any messages accumulated during it, a fresh debounce timer schedules a follow-up burst.
- **Result**: every message is announced exactly once, in coalesced batches sized by the user's typing cadence. No drops, no duplicate parallel announcements, no "next burst silently lost because the first burst's inject was still narrating."

`dedup_key=msg_burst:<user_id>` is kept on the inject as defense-in-depth against the narrow window where two `_on_timer_fired` callbacks could race (timer + flush_all both popping the same state). The buffer is the primary mechanism; the dedup_key is the seatbelt.

## Persona config

The `skills.telegram:` block in `persona.yaml`:

```yaml
skills:
  telegram:
    api_id: 12345678 # int, from my.telegram.org/apps
    api_hash: "abcdef..." # 32-char hex
    userbot_phone: "+57…" # first-run auth only; persists in session file
    contacts:
      hija: "+57 318 685 1696"
      hijo: "+573001234567"
    inbound:
      enabled: true # listens for incoming calls AND messages
      auto_answer: contacts_only # "contacts_only" | "all" | false
      unknown_messages: drop # "drop" | "announce" — symmetric with auto_answer
      debounce_seconds: 2.5 # per-sender coalesce window for inbound bursts
      backfill_hours: 6 # 0 disables; window for unread-on-connect backfill
      backfill_max: 50 # 0 disables; cap on backfill messages per session
```

- **`api_id` / `api_hash`** _(required)_ — Telegram application credentials. Create once at [my.telegram.org/apps](https://my.telegram.org/apps) (see `docs/research/telegram-voice.md` for how to fill the form). Missing or wrong-type values are soft-fails — the skill registers but `call_contact` / `send_message` return LLM-facing errors explaining the gap.
- **`userbot_phone`** _(optional except first run)_ — phone number of the spare SIM the userbot signs in as. Consulted only on first startup; from then on the sqlite session file in the persona data dir authenticates silently. If the session is deleted, set this again and the SMS-code flow fires once.
- **`contacts`** _(required for the skill to be useful)_ — name → phone mapping. Phones can be in any format; the skill normalizes (strips spaces, dashes, parens). Missing / empty / non-string values log a warning and get dropped.
- **`inbound.enabled`** — when true, eagerly connects at setup, builds the user_id→name reverse map, registers the call + message handlers, and runs the backfill pass.
- **`inbound.auto_answer`** — `contacts_only` (default) rejects unknown callers; `all` accepts; `false` disables inbound entirely.
- **`inbound.unknown_messages`** — `drop` (default, mirrors `contacts_only` for messages: spam vector mitigation) silently logs and drops messages from senders not in the contacts whitelist; `announce` surfaces them as `"un número desconocido"` (use only on locked-down deployments where the userbot's number is private).
- **`inbound.debounce_seconds`** — per-sender coalesce window. Long enough to absorb a typed-burst-then-pause; short enough to feel responsive. Bumping past ~5s starts to feel laggy.
- **`inbound.backfill_hours` / `inbound.backfill_max`** — bound the on-connect backfill. Set either to `0` to disable.

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
- **`read_unread` / `reply_to_last` tools** — deferred from T1.11 v1. The LLM can already say "Carlos te dijo: '...', ¿quieres responder?" from the inject preview and call `send_message`. Add these only if real usage shows the preview is insufficient.
- **Voice-note send (`send_voice_note`)** — needs mic-claim coordination; will require an `AudioStream`/InputClaim shape decision separate from text messaging.
- **Voice-note receive** — currently the inbound handler ignores non-text messages (stickers, photos, voice notes). Voice notes likely want either Whisper transcription server-side ("hija te envió un mensaje de voz que dice: ...") or playback as an `AudioStream` side-effect; locked design pending.
- **MESSAGE_THREAD UI view** — soft-blocked on T2.5. Voice-only path doesn't need it; tap-to-open-thread plus inbox LIST view land as a follow-up after the skill UI plane ships.

## Persona constraints this skill respects

See [`docs/skills/README.md`](./README.md#persona-constraints--what-your-skill-should-respect) for the constraints framework.

- **`never_say_no`** — the error messages the tool returns are LLM-facing prompts, not literal speech. The persona's `never_say_no` constraint applies to how the LLM renders them. We give the LLM enough context ("contacto no encontrado; contactos conocidos: X, Y, Z") that it can offer an alternative ("¿quieres llamar a Y?") instead of a raw refusal.

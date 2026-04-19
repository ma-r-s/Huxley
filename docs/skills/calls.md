# `huxley-skill-calls`

Inbound voice calls. The first real consumer of the T1.4 Stage 2 `InputClaim` primitive. Mario's web-app phone UI rings grandpa's device; this skill answers, relays PCM in both directions, and announces end-of-call. Built on the Stage 1 focus management substrate — preemption, ducking, user-PTT-ends-everything all fall out of `FocusManager` composition without dedicated logic.

## What it does

Mario's web app does:

1. `GET ws://device:8765/call/ring?from=Mario` with `X-Shared-Secret: <value>` header.
2. `WS  ws://device:8765/call?secret=<value>` for caller-side audio (PCM16 mono @ 24 kHz both directions).

Server flow:

1. AudioServer's `/call/ring` route validates the secret and fires `CallsSkill.on_ring(params)`.
2. Skill stores pending state and `inject_turn(PREEMPT)` with an instruction telling the LLM to announce the caller and call `answer_call` after counting down ("Llamada de Mario, contestando en tres, dos, uno...").
3. LLM speaks the announcement, then dispatches `answer_call`.
4. `answer_call` returns `ToolResult(side_effect=InputClaim(...))` — the framework starts the claim at the turn's terminal barrier: `provider.suspend()` stops the OpenAI session from processing user audio, `MicRouter.claim()` swaps grandpa's mic to the skill handler, and the speaker pump starts forwarding caller PCM to grandpa's speaker.
5. Either side ends the call (see "End mechanics" below); the claim's `on_claim_end(reason)` fires; the skill announces the end via `inject_turn(NORMAL)`.

## Tools

- **`answer_call()`** — opens the line. Returns `InputClaim` side-effect. The LLM is instructed via the ring prompt to call this immediately after the countdown, **without** waiting for grandpa to say "sí" — the countdown itself is the implicit consent.
- **`reject_call()`** — drops a pending call before answer. Used when grandpa says "no" or "ahora no" during the countdown.
- **`end_call()`** — explicit hangup during an active call. Rare — the caller hanging up or grandpa pressing PTT typically ends the call; this is for "cuelga" / "termina la llamada" voice intent.

## Persona config

```yaml
skills:
  calls:
    secret: "your-shared-secret-here" # or set HUXLEY_CALLS_SECRET env var (env wins)


    # Optional prompt overrides — the skill ships Spanish/AbuelOS-toned defaults.
    # `{from_name}` is substituted with the `from` query param of the ring URL.
    # ring_prompt: |
    #   Suena el teléfono. Tienes una llamada de {from_name}. Anuncia la
    #   llamada — di algo como "Llamada de {from_name}, contestando en tres,
    #   dos, uno" — y después llama a la herramienta `answer_call`.
    # end_natural_prompt: |
    #   El otro lado de la llamada colgó. Avísale al usuario brevemente —
    #   algo como "{from_name} colgó". Una sola frase corta.
    # end_user_ptt_prompt: |
    #   El usuario terminó la llamada. Confirma brevemente — algo como
    #   "llamada finalizada". Una sola frase corta.
    # end_error_prompt: |
    #   La llamada se cortó por un problema técnico. Avísale al usuario —
    #   "la llamada se cortó, lo siento". Una sola frase corta.
```

**Secret precedence**: `HUXLEY_CALLS_SECRET` env var beats `persona.skills.calls.secret`. Use the env var in production so the secret doesn't sit in checked-in yaml.

If neither is set, the skill loads but logs `calls.no_secret_configured` and the `/call/ring` + `/call` routes return 503 — the framework declines to expose unauthenticated trigger endpoints.

## UX (locked 2026-04-19 with Mario)

- **Auto-pickup with countdown.** The ring prompt instructs the LLM to announce the caller and start a 3-second countdown, then dispatch `answer_call` automatically. No "press to answer" — grandpa is blind and shouldn't have to learn a voice command under the stress of a ringing phone.
- **Opt-out during countdown.** If grandpa says "no" or "ahora no" during the countdown, the LLM is told to call `reject_call` instead of `answer_call`.
- **Caller hangs up = primary end mechanic.** When the `/call` WS closes, the skill calls `ctx.cancel_active_claim(reason=NATURAL)` (Stage 2.1); the observer's `on_claim_end` fires, the inject narrates "{from_name} colgó", and the provider resumes for grandpa's next interaction.
- **Grandpa's PTT = secondary end mechanic.** During a call, grandpa pressing PTT triggers the coordinator's `interrupt()` path. The active claim ends with `ClaimEndReason.USER_PTT`; the skill narrates "Llamada finalizada"; the normal PTT turn proceeds.
- **No speech-based "adiós" detection.** The OpenAI Realtime session is suspended during the call (that's literally what `InputClaim` is — grandpa's mic goes to the caller, not OpenAI). To detect "adiós" we'd need parallel speech recognition or partial un-suspend, both of which add latency/cost and defeat the point. Grandpa saying goodbye is heard by **the caller**, who clicks hang up.

## The conversation matrix (this skill's cells)

Every interaction below works via FocusManager composition — the skill writes zero new preemption logic.

| Active       | Incoming             | Outcome                                              |
| ------------ | -------------------- | ---------------------------------------------------- |
| Audiobook    | Ring                 | Book pauses (PREEMPT inject); announcement plays     |
| News reading | Ring                 | Same — PREEMPT preempts any CONTENT-channel stream   |
| Active call  | inject_turn(PREEMPT) | Claim ends `PREEMPTED`; medication reminder narrates |
| Active call  | inject_turn(NORMAL)  | Queues behind call; fires after call ends            |
| Active call  | User PTT             | Claim ends `USER_PTT`; "Llamada finalizada"          |
| Active call  | Caller hangs up WS   | Claim cleanup; "{from_name} colgó"                   |
| Active call  | Second incoming ring | Skill rejects with 409 Busy on the HTTP route        |
| Pending call | User PTT             | Pending state cleared; turn proceeds normally        |
| Pending call | inject_turn(PREEMPT) | Claim dropped pre-start; `on_claim_end(PREEMPTED)`   |

## Web client contract

For Mario's web app (or any future caller client):

**Ring endpoint** — `GET /call/ring?from=<name>`:

- Header: `X-Shared-Secret: <value>` (required)
- Returns `200 ringing\n` if accepted, `401 bad secret\n`, `409 busy\n` (grandpa already on a call or pending), `503 calls disabled\n` (server has no secret configured).
- Side-effect: skill announces the call to grandpa via `inject_turn`.

**Caller WebSocket** — `WS /call?secret=<value>`:

- Auth via query param (browsers can't easily set custom headers on WS upgrade).
- Sends: PCM16 mono @ 24 kHz binary frames (caller's voice).
- Receives: PCM16 mono @ 24 kHz binary frames (grandpa's voice).
- Close to hang up. Skill detects close, ends the claim, narrates "{from_name} colgó".

**Audio format**: PCM16 (signed 16-bit little-endian), mono, 24 kHz. Matches the device's main WebSocket so no transcoding lives in the relay path. Web app responsibility:

- Capture mic via `AudioWorklet` (NOT `MediaRecorder`, which produces compressed WebM/Opus).
- Downsample browser's native 48 kHz to 24 kHz (linear interpolation is fine).
- Send raw PCM16 binary frames over the WebSocket (binary, not JSON-wrapped).
- For receive: queue incoming binary frames into an `AudioContext` for playback.

A reference snippet of the AudioWorklet path lives outside this repo (Mario's web app); add a link here once the PWA repo is public.

## Scope limits (MVP)

- **HTTP `/call/ring` is GET-only.** `websockets` v16 only allows GET through `process_request`. POST would be more RESTful but functionally identical for this internal trigger; not worth a separate HTTP server.
- **Single shared secret across all callers.** When you add the second family member, replace with per-caller tokens (filed as Stage 2.3 in triage).
- **No voicemail.** Ring fires but answer never dispatches (timeout / explicit reject) → silence on grandpa's side. Future: inject_turn "Mario te llamó pero no contestaste" (filed as Stage 2.2).
- **Single concurrent call.** Second ring during active call gets 409 Busy. No call-waiting / hold semantics.
- **Stage 4 ClientEvent not yet used.** Ring trigger is HTTP for MVP per the critic's "ship the rough thing in week 1, migrate when UX is proven" recommendation. ~50 LOC of HTTP glue gets thrown away when Stage 4 lands.

## Logging

- `calls.setup_complete` — `has_secret` (bool)
- `calls.no_secret_configured` — warning at setup if neither env var nor persona config supplied a secret
- `calls.invalid_prompt_override` — persona config supplied a non-string prompt override; default kept
- `calls.ring_accepted` — `from_name`. Inject is firing.
- `calls.ring_rejected_busy` — second ring while pending or active
- `calls.caller_connected` — caller WS upgraded successfully
- `calls.caller_text_frame_ignored` — caller sent JSON / text instead of binary PCM (protocol mismatch)
- `calls.caller_disconnected` — caller WS closed
- `calls.caller_read_loop_failed` — exception during caller WS read (logged with traceback)
- `calls.second_caller_rejected` — concurrent caller WS arrived; closed with 1008
- `calls.answer` — `from_name`. Tool dispatched, claim about to start at terminal barrier.
- `calls.answer_no_caller` — answer_call dispatched but no caller WS is connected
- `calls.answer_already_active` — answer_call dispatched while a claim is already running
- `calls.rejected_by_user` — reject_call tool fired
- `calls.end_by_tool` — end_call tool fired
- `calls.ended` — `reason` (natural / user_ptt / preempted / error), `from_name`. Lifecycle terminal.
- `calls.mic_send_failed` — debug-level; transient send failure on caller WS, usually means caller disappeared mid-frame
- `calls.speaker_pump_failed` — exception in the speaker_source iterator (shouldn't happen in normal flow)
- `calls.teardown_complete`

Plus the framework's `claim.*`, `focus.*`, `coord.*`, `server.rx.*` events fire normally — see [`docs/observability.md`](../observability.md) for the substrate-level vocabulary.

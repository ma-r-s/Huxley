# Huxley I/O plane — spec for skill-extensible streams

> Status: spec. Locks the framework primitives that let skills extend audio I/O
> and the turn loop without the framework knowing what any skill does.
> Implementation plan in `triage.md` T1.2 / T1.3 / T1.4.

## The abstract model

Huxley is an **audio-first agent runtime**. Everything below the skill line
reduces to three streams plus a turn loop:

```
                      ┌─────────────────────────┐
    ┌──────────┐      │  Huxley framework       │      ┌───────────┐
    │          │ mic  │  ┌───────────────────┐  │      │           │
    │  Client  ├─────▶│  │    I/O plane      │  │      │   Voice   │
    │ (browser │      │  │ (mic/spk/events)  │  │◀────▶│ provider  │
    │  or ESP) │◀─────┤  └─────────┬─────────┘  │      │ (OpenAI)  │
    │          │ spk  │            │             │      │           │
    │          │ evts │  ┌─────────▼─────────┐  │      └───────────┘
    └────┬─────┘      │  │    Turn loop      │  │
         │ events     │  │ (user + synthetic) │  │
         └───────────▶│  └─────────┬─────────┘  │
                      │            │             │
                      │  ┌─────────▼─────────┐  │
                      │  │  Skill registry   │  │
                      │  └───────────────────┘  │
                      └─────────────────────────┘
```

**The three streams**:

1. **Mic input** — PCM16 from client microphone
2. **Speaker output** — PCM16 to client speaker
3. **Client events** — control signals from client (PTT, hardware buttons,
   state changes)

**The turn loop** — user speech → voice provider → model response → tool
dispatch → optional synthetic turns. The coordinator sequences these into
strict-ordered interaction units.

**The framework's only job** is owning these four mechanisms. It provides
primitives for skills to claim, route, inject, and subscribe. It never
knows what a skill does with those primitives.

### The guiding principle

> **The framework names mechanisms, not use cases.** Skills name what they
> build. Nothing in `huxley_sdk` or `huxley` core should mention "call,"
> "emergency," "reminder," "message," or any other skill-level concept.

Concrete rule: before adding a type or enum variant to the framework, ask
"would this name still make sense if this skill didn't exist?" If the answer
is no, rename.

### The primitives, grouped by what they operate on

The five primitives aren't peers — they're three tiers by what they
touch. Naming the tiers makes the taxonomy obvious and drops the mental
load on skill authors.

| Tier              | Primitives                                                              | What they operate on                                                                                                          |
| ----------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| **Stream claims** | `AudioStream` / `PlaySound` / `CancelMedia` / `SetVolume`, `InputClaim` | A stream (speaker output or mic input). Skill says "while this is active, reroute the stream."                                |
| **Event fan-out** | `inject_turn`, `ClientEvent` subscription + `server_event` emit         | A named event crosses a boundary (skill → turn loop, client ↔ server). Skill says "when X happens, invoke Y" or "speak this." |
| **Lifecycle**     | `background_task`                                                       | A long-running async unit of work. Skill says "supervise this task for me."                                                   |

**Responsibilities of the framework vs. the persona**:

- **Framework** owns the mechanisms (claim routing, event dispatch, task
  supervision). It never knows _what_ a skill does.
- **Persona** owns the user-facing tone shaping on top of the mechanisms
  (earcon files, TTL defaults per urgency, urgency-modulation rules like
  quiet-hours). These are product decisions that differ between an
  elderly-blind persona and a child-tutor persona; they live in
  `persona.yaml`, not in framework code. When a future requirement says
  "demote all non-CRITICAL urgencies to AMBIENT between 22:00 and
  07:00," that filter lives on the persona, not in the arbitration
  function.

---

## Primitive 1 — `AudioStream` and siblings (speaker output)

**Already shipped.** Skills return `AudioStream`, `PlaySound`, `CancelMedia`,
or `SetVolume` as a `ToolResult.side_effect`. Framework manages ordering,
cancellation, and ducking.

This primitive owns **speaker output stream claims**. Today's use cases:
audiobook playback (`AudioStream`), earcon chimes (`PlaySound`), mid-turn
cancellation (`CancelMedia`), volume control (`SetVolume`).

New in this spec: `AudioStream` gains a `yield_policy` field declaring how
this stream yields when a higher-priority turn wants the speaker. See
Primitive 2.

---

## Primitive 2 — Turn injection (`ctx.inject_turn`)

### What it is

A skill injects a synthetic turn into the turn loop from outside the user's
speech path. The framework picks up the injected prompt, gets a model
response via the voice provider, and delivers it through the speaker output
stream. It's the same mechanism that powers today's end-of-book completion
announcement (`AudioStream.on_complete_prompt`) — now lifted to a public
skill-callable primitive.

### Why the framework needs it

Several distinct use cases need "agent speaks without user speaking first":
reminders, inbound messages, scheduled greetings, memory surface events.
Each skill would otherwise invent its own mechanism. A single primitive
gives the coordinator one code path to manage and one set of invariants to
enforce.

### SDK surface

```python
class Urgency(Enum):
    AMBIENT      = "ambient"       # speak only if idle, else drop
    CHIME_DEFER  = "chime_defer"   # chime now, hold speech for next PTT
    INTERRUPT    = "interrupt"     # preempt media, speak now
    CRITICAL     = "critical"      # preempt all activity, top priority


class YieldPolicy(Enum):
    IMMEDIATE       = "immediate"        # yield to anything above AMBIENT
    YIELD_ABOVE     = "yield_above"      # yield to INTERRUPT and CRITICAL
    YIELD_CRITICAL  = "yield_critical"   # yield only to CRITICAL


class TurnOutcome(Enum):
    """What happened to an injected turn. Surfaced to the skill via
    `InjectedTurnHandle.wait_outcome()` so skills can drive retry, escalation,
    or cleanup on a non-ACKNOWLEDGED outcome."""
    ACKNOWLEDGED = "acknowledged"  # user PTTed within ack window after drain
    DELIVERED    = "delivered"     # spoken to completion, no user reaction
    EXPIRED      = "expired"       # TTL passed before delivery
    PREEMPTED    = "preempted"     # another turn with higher urgency displaced us
    CANCELLED    = "cancelled"     # skill called handle.cancel()


class InjectedTurnHandle:
    """Returned by `inject_turn`. Skill drives retry/escalation off the
    outcome; framework drives delivery + queueing."""

    async def acknowledge(self) -> None:
        """Mark acknowledged (skill has out-of-band confirmation). Causes
        wait_outcome() to resolve to ACKNOWLEDGED if not already resolved."""

    async def cancel(self) -> None:
        """Remove from queue if still pending; terminate in-flight if
        active. wait_outcome() resolves to CANCELLED."""

    async def wait_outcome(self) -> TurnOutcome:
        """Suspend until the turn reaches a terminal state. Idempotent —
        multiple awaiters all receive the same outcome."""


# On SkillContext:
async def inject_turn(
    self,
    prompt: str,
    *,
    urgency: Urgency = Urgency.CHIME_DEFER,
    dedup_key: str | None = None,
    expires_after: timedelta | None = None,
) -> InjectedTurnHandle: ...
```

- **`prompt`**: what the LLM should say, in the persona's language. LLM
  narrates in persona voice. Persona constraints apply (e.g.,
  `never_say_no`).
- **`urgency`**: framework-level enum. Skills pick the tier that matches
  what they're trying to do. Framework does NOT know what generates each
  tier.
- **`dedup_key`**: if a turn with the same key is already pending, replace
  it. Optional. Also serves as the observability tag in logs (`tag` param
  from an earlier draft was dropped — redundant with dedup_key and with
  the skill-name binding on the logger).
- **`expires_after`**: TTL. After this the turn is dropped from the
  queue silently. Defaults from persona config per urgency tier.

### Arbitration (the pure function)

The speaker is a single-claim resource. Arbitration is a pure function
`(urgency, yield_policy) -> Decision` with five possible outcomes:

```python
class Decision(Enum):
    SPEAK_NOW   = "speak_now"     # idle — no preemption needed
    PREEMPT     = "preempt"       # cancel current stream, play earcon + speak
    DUCK_CHIME  = "duck_chime"    # dip current stream -18dB, play tier chime,
                                  # hold speech for next PTT
    HOLD        = "hold"          # queue for next PTT; no earcon now
    DROP        = "drop"          # ambient event dropped while busy
```

The function's decision table:

```
rank: AMBIENT=0, CHIME_DEFER=1, INTERRUPT=2, CRITICAL=3

yield_threshold:
  IMMEDIATE       → 0   (yields above AMBIENT)
  YIELD_ABOVE     → 1   (yields above CHIME_DEFER)
  YIELD_CRITICAL  → 2   (yields above INTERRUPT)

If no media playing AND no user turn active:
    AMBIENT / CHIME_DEFER / INTERRUPT / CRITICAL -> SPEAK_NOW
    (Framework still plays the tier earcon before speech for all non-AMBIENT.)

Otherwise (media playing OR user turn active):
    AMBIENT                                    -> DROP
    CHIME_DEFER & yield_threshold >= 1 (YIELD_ABOVE, YIELD_CRITICAL)
                                               -> DUCK_CHIME
    CHIME_DEFER & yield_threshold == 0 (IMMEDIATE)
                                               -> PREEMPT
    INTERRUPT   & yield_threshold >= 2 (YIELD_CRITICAL)
                                               -> DUCK_CHIME
    INTERRUPT   & yield_threshold < 2
                                               -> PREEMPT
    CRITICAL                                   -> PREEMPT (no yield policy blocks CRITICAL)
```

Why five outcomes instead of "preempt yes/no": an honest spec. `DUCK_CHIME`
is a third behavior distinct from both preempt and silent hold — the tier
earcon plays (user gets auditory signal that SOMETHING happened), the
current stream dips but continues, and the speech payload waits for the
user's next PTT. Saying "preempt iff rank > threshold" hides this; the
five-outcome model names it.

Lives in `huxley.turn.arbitration` as a pure function + exhaustive 16-row
test table. The function has no dependencies on coordinator state; calling
code feeds in the current `yield_policy` of whatever is playing (None
treated as "idle").

### Cross-cutting behavior rules

1. Every injected turn leads with a **persona-owned earcon** — one per
   non-AMBIENT tier. Reduces startle; orients a blind listener. The
   framework plays the earcon before any speech.
2. Every injected turn is **interruptible by PTT** — user PTT always wins,
   same semantics as interrupting any other agent speech.
3. Every event **must have an audible trail** — either speak it, or on next
   engagement narrate a one-line "tuviste N eventos pendientes que
   expiraron." Never drop silently (except AMBIENT, which is by design
   fire-and-forget).
4. **Speech is LLM-narrated from the prompt**, never rendered by the skill.
5. **Preempted media is cancelled, not paused.** Same mental model as a
   PTT interrupt — the position is saved by the media task's `finally`
   block and the user can resume with "sigue con el libro." Pause-resume
   is a later addition if and when a skill demands it.

### Stale/expired handling

TTL defaults live on the persona, per urgency tier:

- `AMBIENT` — no queue; drops immediately if not fireable
- `CHIME_DEFER` — 12 hours
- `INTERRUPT` — 2 hours
- `CRITICAL` — 30 seconds

Expired events drop from the queue; on next engagement the LLM is told via
a one-line system-prompt note (`# Nota: {N} evento(s) pendiente(s)
expiraron sin reproducirse`) so it can briefly acknowledge if appropriate.
Content is not replayed.

### Retry is a skill concern, not a framework concern

Medication reminders famously need "retry until acknowledged." The
framework does not build retry semantics into `inject_turn`. Skills
(`huxley-skill-reminders`) implement their own retry logic by re-calling
`inject_turn` on their own schedule. Keeps the primitive simple; each
skill picks its own retry policy.

---

## Primitive 3 — `InputClaim` (mic stream takeover)

### What it is

A skill claims the mic stream for the duration of a task. While the claim
is active, mic PCM goes to the skill's handler instead of the voice
provider. The skill may optionally supply a speaker audio source to play
in parallel (bidirectional I/O).

When the claim ends (stream done, cancelled, or task failure), mic
routing reverts to the voice provider automatically. Framework handles
the plumbing; skill handles the content.

### Why the framework needs it

Multiple future skills need mic control for reasons that have nothing to
do with each other:

- Voice memo skill: mic → disk writer
- Calls skill (hypothetical): mic → SIP/Twilio peer, peer audio → speaker
- Recording skill: mic → file + speaker passthrough
- Dictation skill: mic → transcription-only pipeline

Each would otherwise either (a) never be buildable because the framework
hard-codes mic → OpenAI, or (b) force a skill-specific fork. The primitive
lets all of them compose on the same mechanism.

### SDK surface

Two entry points — one for tool-dispatched claims (voice memo recorded via
a tool call), one for event-driven claims (call skill latches after a
`client_event` fired, no tool in the causal chain).

```python
class ClaimEndReason(Enum):
    NATURAL     = "natural"      # speaker_source exhausted / handle.cancel()
    USER_PTT    = "user_ptt"     # user PTTed during the claim
    PREEMPTED   = "preempted"    # a CRITICAL injected turn displaced us
    ERROR       = "error"        # handler raised; detail in on_claim_end arg


@dataclass(frozen=True, slots=True)
class InputClaim(SideEffect):
    """Claim the mic stream for the duration of a task.

    While active:
      - mic PCM frames are delivered to `on_mic_frame` instead of the
        voice provider
      - `speaker_source` (if set) streams frames to the speaker, bypassing
        the normal model-audio path
      - the voice provider's session is suspended per `suspend_voice_provider`
    """
    kind: ClassVar[str] = "input_claim"

    on_mic_frame: Callable[[bytes], Awaitable[None]]
    speaker_source: Callable[[], AsyncIterator[bytes]] | None = None
    suspend_voice_provider: bool = True
    on_claim_end: Callable[[ClaimEndReason], Awaitable[None]] | None = None
    yield_policy: YieldPolicy = YieldPolicy.YIELD_CRITICAL


class ClaimHandle:
    """Returned by ctx.start_input_claim. Lets the skill cancel or await
    the claim from code paths that aren't holding a ToolResult."""

    async def cancel(self) -> None: ...
    async def wait_end(self) -> ClaimEndReason: ...


# Two entry points on SkillContext:

# 1. Skill handler called via a tool_call — returns ToolResult with claim
#    as side effect. Framework latches it at the turn's terminal barrier.
return ToolResult(
    output='{"recording": true}',
    side_effect=InputClaim(on_mic_frame=writer.write, ...),
)

# 2. Skill event handler (background task, subscribed client_event) that
#    needs to latch mic/speaker immediately, without a tool call in the
#    causal chain. Critical for panic-button + auto-connect calls —
#    there is no LLM, no tool call, just an inbound event demanding an
#    instant audio plane swap.
async def ctx.start_input_claim(claim: InputClaim) -> ClaimHandle: ...
```

### Latch behavior (either entry point)

Coordinator (specifically the `MicRouter` + `MediaTaskManager`
collaborators — see T1.3):

1. **Suspend before swap.** Voice provider suspended FIRST (drops pending
   assistant audio, blocks further inference), THEN mic routing swapped
   to `on_mic_frame`. This ordering is a framework invariant — it
   prevents a window where mic audio leaks to OpenAI after the claim
   logically began. Stage-4 tests cover this explicitly.
2. **Speaker output**: `speaker_source` frames (if any) go through the
   existing `send_audio` path. Same client-side rendering as `AudioStream`.
3. **PTT during claim**: `on_claim_end(USER_PTT)` fires, cleanup runs,
   voice provider resumes, user's PTT creates a normal turn. Escape
   hatch always available.
4. **Natural end**: speaker_source generator exhausts, or skill calls
   `handle.cancel()`. `on_claim_end(NATURAL)` fires; routing restores.
5. **Preemption**: a `CRITICAL`-urgency `inject_turn` can displace an
   active claim if the claim's `yield_policy < YIELD_CRITICAL` (typical
   calls skill sets `YIELD_CRITICAL` — only another CRITICAL event
   preempts). On preempt: `on_claim_end(PREEMPTED)` fires; the critical
   turn plays after cleanup completes.
6. **Error**: handler raises. `on_claim_end(ERROR)` fires with exception
   info; routing restores; error logged via `coord.claim_error`.

### Interaction with `inject_turn`

The `InputClaim.yield_policy` participates in arbitration the same way
`AudioStream.yield_policy` does — the same pure function decides. Default
`YIELD_CRITICAL` means only a CRITICAL-urgency `inject_turn` can preempt
an active claim. Skills override case-by-case:

- Voice memo skill: `YIELD_ABOVE` (lets INTERRUPT reminders through — a
  medication reminder during a 30-second memo is more important than the
  memo)
- Calls skill: `YIELD_CRITICAL` (only another call-priority event
  preempts an active call)

---

## Primitive 4 — `ClientEvent` subscription

### What it is

Clients can send arbitrary string-keyed control events to the server.
Skills subscribe to specific event keys. When an event arrives, the
framework routes it to all subscribed handlers.

The framework has no list of valid events. Clients and skills agree by
string-key convention.

### Why the framework needs it

The only non-audio client-to-server signals today are PTT start/stop and
a handful of framework-internal messages (client_connected, etc.). Any
new hardware input (panic button, volume knob, hardware mute, etc.) or
any non-audio skill signal (BLE sensor data, watch notifications,
file-system events forwarded from client) forces a wire protocol
amendment today.

Generic `client_event` lets the protocol accept any of these without
framework changes. Framework does not validate event names — it just
routes.

### Namespace convention

String keys MUST use one of:

- `huxley.*` — reserved for framework events (current PTT, volume, etc.).
  Framework-owned; skills cannot claim these.
- `<skill-name>.<event>` — skill-owned. Skill declares its event set
  in its docs. Multiple clients and skills can agree on shared keys.

No framework-side validation. The convention lives in the protocol
doc and is enforced by review, not code.

### Wire protocol

Hybrid design: fixed message types stay fixed (no churn to the existing
browser dev client or protocol doc), plus generic events flowing both
directions:

```json
// Existing fixed types (unchanged)
{"type": "ptt_start"}
{"type": "ptt_stop"}
{"type": "audio", ...}

// Generic client → server
{"type": "client_event", "event": "<namespaced-key>", "payload": {...}}

// Generic server → client (NEW)
{"type": "server_event", "event": "<namespaced-key>", "payload": {...}}
```

**Symmetry matters.** Some skills need to push state to the client that
isn't audio: the calls skill wants to tell the ESP32 "switch LED red,
mute hardware mic mixer" before latching an `InputClaim`; messaging might
flash a tactile LED on `CHIME_DEFER` delivery; a future sensor skill
might request a client-side capture. Shipping only client→server now and
adding server→client later means a wire break. Ship symmetric from day
one.

**Capability handshake.** The `hello` message gains a `capabilities`
array so new clients can declare what event types they understand. Old
clients (no capabilities field) are treated as protocol-1 dumb clients
— framework falls back to fixed types only for them.

```json
// hello becomes:
{"type": "hello", "protocol": 1, "capabilities": ["client_event", "server_event"]}

// Old clients that don't send capabilities are treated as:
{"type": "hello", "protocol": 1, "capabilities": []}
```

### SDK surface

```python
# On SkillContext:

def subscribe_client_event(
    self,
    event_key: str,
    handler: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """Subscribe to client_event messages matching `event_key`. Handler
    receives the message payload. Unsubscribe is automatic at skill
    teardown."""


async def emit_server_event(
    self,
    event_key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Send a server_event to the client. No-op if the client's
    capabilities don't include "server_event". Framework logs a debug
    line when skipped so skill authors can see the degraded path."""
```

No framework-side event type registry. Skills document their own
conventions in their own skill-doc pages.

---

## Primitive 5 — Supervised background tasks

### What it is

Skills that run persistent background work (schedulers for time-based
injected turns, listeners for external webhooks, polling loops) need a
way to hand that work to the framework so it supervises lifecycle:
logs crashes, restarts with backoff, surfaces permanent failures.

### Why the framework needs it

Today skills can spawn `asyncio.create_task(...)` in `setup()` — it works
but the framework is blind to crashes (documented gap in
`docs/extensibility.md`). A reminders scheduler that dies silently means
medication reminders stop firing with no signal. Unacceptable.

### SDK surface

```python
# On SkillContext:
def background_task(
    self,
    name: str,
    coro_factory: Callable[[], Awaitable[None]],
    *,
    restart_on_crash: bool = True,
    max_restarts_per_hour: int = 5,
    on_permanent_failure: Callable[[PermanentFailure], Awaitable[None]] | None = None,
) -> None:
    """Register a supervised background task.

    `coro_factory` is called each time the task needs to start (initial
    run + restarts). Framework logs crashes, restarts up to
    max_restarts_per_hour, and emits a structured event if the task
    dies permanently (exceeded restart budget).

    `on_permanent_failure` is called with failure details when the
    restart budget is exceeded. Life-safety-critical skills (medication
    reminders) can register this to escalate via another channel —
    e.g. the reminders skill's on_permanent_failure can call
    inject_turn(CRITICAL) to tell the user "ya no puedo avisar de las
    pastillas, revisen el aparato." Framework otherwise only logs.
    """
```

Named for log attribution. Per-skill namespaced. The
`on_permanent_failure` callback is itself supervised — if it raises,
the framework logs and stops (doesn't recurse).

---

## How the primitives compose (examples)

### Reminders skill

```python
class RemindersSkill:
    name = "reminders"

    async def setup(self, ctx: SkillContext) -> None:
        self._ctx = ctx
        ctx.background_task("scheduler", self._scheduler_loop)

    async def _scheduler_loop(self) -> None:
        while True:
            due = await self._next_due_reminder()
            if due is None:
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(max(0, (due.when - now()).total_seconds()))
            urgency = Urgency.INTERRUPT if due.kind == "medication" else Urgency.CHIME_DEFER
            await self._ctx.inject_turn(
                due.prompt,
                urgency=urgency,
                dedup_key=f"reminder:{due.id}",
                expires_after=timedelta(hours=2),
                tag=f"reminder:{due.kind}",
            )
```

Uses: `background_task` + `inject_turn`. Zero framework knowledge of
reminders.

### Inbound messaging skill

```python
class MessagingSkill:
    async def setup(self, ctx: SkillContext) -> None:
        self._ctx = ctx
        ctx.background_task("webhook_listener", self._listen)
        ctx.subscribe_client_event("messaging.read_receipt", self._on_read)

    async def _listen(self) -> None:
        async for msg in self._webhook_stream():
            await self._ctx.inject_turn(
                f"Le llegó un mensaje de {msg.from_name}: '{msg.text}'",
                urgency=Urgency.CHIME_DEFER,
                dedup_key=f"msg:{msg.from_id}",
                expires_after=timedelta(hours=12),
            )
```

### Calls skill (future, illustrative — not yet built)

```python
class CallsSkill:
    async def setup(self, ctx: SkillContext) -> None:
        self._ctx = ctx
        ctx.subscribe_client_event("calls.button_pressed", self._on_button)
        ctx.background_task("sip_listener", self._listen_for_inbound)

    async def _on_button(self, _payload: dict) -> None:
        # Panic button or outbound-call button
        peer = self._emergency_contact
        await self._ctx.inject_turn(
            f"Llamando a {peer.name}",
            urgency=Urgency.CRITICAL,
        )
        call = await self._dial(peer)
        # Return as if it were a tool result — coordinator picks it up
        await self._ctx.emit_side_effect(InputClaim(
            on_mic_frame=call.send_audio,
            speaker_source=call.receive_audio,
            on_claim_end=call.hangup,
        ))

    async def _listen_for_inbound(self) -> None:
        async for call in self._sip_stream():
            if self._is_trusted(call.from_id):
                # Auto-connect
                await self._ctx.inject_turn(
                    f"{call.from_name} te está llamando",
                    urgency=Urgency.CRITICAL,
                )
                await self._ctx.emit_side_effect(InputClaim(...))
            else:
                # Loop ring (would need a LoopingAudioStream or similar)
                ...
```

Uses: `ClientEvent`, `background_task`, `inject_turn`, `InputClaim`.
Framework has NO "call" concept.

### Voice memo skill

```python
class VoiceMemoSkill:
    @property
    def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(name="record_memo", description="...", parameters={...})]

    async def handle(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "record_memo":
            duration = args.get("seconds", 30)
            writer = AudioWriter(self._memo_dir / f"{now_iso()}.wav")
            return ToolResult(
                output='{"recording": true, "seconds": ' + str(duration) + '}',
                side_effect=InputClaim(
                    on_mic_frame=writer.write,
                    on_claim_end=writer.close,
                    yield_policy=YieldPolicy.YIELD_ABOVE,
                ),
            )
```

Uses: just `InputClaim`.

### Panic button (hardware)

From the client (ESP32 firmware):

```json
{ "type": "client_event", "event": "calls.button_pressed", "payload": {} }
```

From the calls skill's subscription, same code path as any other
event. Framework routes by string key; never knows what a "panic button" is.

---

## Test surface

Each primitive tests in isolation + integration:

### `inject_turn`

- Pure-function tests for the arbitration table (16 cases).
- Coordinator tests for the full flow (preempt media, play earcon, speak).
- Coordinator tests for queue behavior (hold, drain on PTT).
- Coordinator tests for TTL expiry.
- Coordinator tests for dedup.

### `InputClaim`

- Coordinator tests: claim latches, mic frames route to handler, default
  routing restores on claim end.
- Coordinator tests: PTT during claim ends claim, user turn starts.
- Coordinator tests: `speaker_source` frames reach `send_audio`.
- Coordinator tests: claim interacts with `inject_turn` arbitration per
  its `yield_policy`.

### `ClientEvent`

- Unit test: subscription + dispatch.
- Unit test: multiple subscribers receive the same event.
- Unit test: unsubscription at teardown.
- Wire test: server dispatches `{type: client_event, event, payload}`
  to subscribers.

### `background_task`

- Unit test: task starts, runs, normal completion.
- Unit test: crash + restart.
- Unit test: exceeds restart budget → permanent failure event.

### UX validation (manual smoke)

Each stage ships with a browser-client smoke script (`docs/verifying.md`
addendum) that exercises the primitive end-to-end with a toy skill.

---

## Implementation staging

The full scope is large. Ships in stages, each self-contained and
independently useful.

### Stage 0 — Coordinator refactor (T1.3)

Already queued. Must land first — the other stages build on the
refactored internals.

Deltas the spec adds to the queued T1.3 plan:

- `TurnFactory` accepts `TurnSource` enum (`USER`, `COMPLETION`, `INJECTED`).
  Do not ship T1.3 without `INJECTED` reserved even though Stage 1 is when
  it gets used — otherwise Stage 1 retouches the factory.
- `SpeakingState` uses **named owners** (`"user" | "factory" | "completion" | "injected" | "claim"`), not a boolean.
- `MicRouter` — new collaborator extracted with the refactor. Owns "where
  does mic PCM go?" — default handler: voice provider. Stage 2 (InputClaim)
  swaps the destination via `claim()`.
- `MediaTaskManager` extracted WITHOUT the `DuckingController` (was in an
  earlier draft; move to Stage 1 where it's actually used — no point
  shipping a dead stub on main).

**Provider suspend/resume contract** (tightened explicitly so Stage 2's
`InputClaim` doesn't discover gaps late):

- `provider.suspend()`: drops any pending assistant audio (not replayed
  on resume); blocks further inference until `resume()`; keeps the
  WebSocket session alive so reconnect cost is avoided. Idempotent —
  multiple suspend calls are safe.
- `provider.resume()`: unblocks inference; session ID unchanged;
  no pending audio replayed (if a skill wants resume-with-context, that's
  a skill-layer concern). Idempotent — resume without suspend is a no-op.
- Contract is behavioral, not just API-shape. Stage 0 tests assert the
  behavioral properties with a fake provider; Stage 2 tests exercise
  them against the real OpenAI Realtime provider via T2.3's fixture harness.

Integration tests from T2.3 already shipped cover the refactor.

**Deliverables**:

- Extracted collaborators with expanded shapes above (including MicRouter)
- Provider suspend/resume contract documented + tested with fake provider
- All existing tests still pass
- New unit tests per collaborator
- Manual smoke: existing behavior (audiobook play + interrupt) unchanged

### Stage 1 — `inject_turn` + arbitration + ducking

First user-visible primitive. End of this stage a minimal "proactive
greetings" toy skill can demo.

**Deliverables**:

- `Urgency` + `YieldPolicy` + `Decision` + `TurnOutcome` enums in SDK
- `inject_turn` method on `SkillContext`
- `InjectedTurnHandle` with `acknowledge()`, `cancel()`, `wait_outcome()`
- `AudioStream.yield_policy: YieldPolicy = YIELD_ABOVE` field
- Arbitration pure function (5-outcome) + exhaustive test table
- `DuckingController` wired into `MediaTaskManager` (server-side PCM gain
  multiplier for duck envelopes)
- Coordinator integration: TurnFactory routes `INJECTED` turns,
  SpeakingState acquires/releases as `"injected"`, arbitration drives
  decision
- Tier earcon slots: three new sound roles
  (`notify_chime_defer`, `notify_interrupt`, `notify_critical`) added to
  persona loader; persona provides them or the tier silently plays nothing
- TTL expiry with persona-level defaults; expiry emits `coord.inject_expired`
- Dedup via `dedup_key` (dict in coordinator)
- Multi-item hold queue: FIFO drain on PTT; each deferred turn played as
  a separate proactive turn in arrival order
- Tests per "Test surface" above for this primitive, plus:
  - TTL expiry mid-flight (CHIME_DEFER with 5s TTL during audiobook;
    PTT after 10s must not fire the expired turn)
  - Multi-item FIFO drain (two CHIME_DEFER queued, PTT drains both in order)
- `docs/concepts.md`: turn injection entry
- `docs/skills/README.md`: using `inject_turn` section

**UX validation**: a throwaway `huxley-skill-hello` with a `background_task`
(Stage 3) — or a manual `inject_turn` trigger for Stage 1 alone — that
fires at each urgency tier. Browser smoke confirms: AMBIENT drops when
playing audiobook; CHIME_DEFER ducks audiobook + chime earcon, speech
held for next PTT; INTERRUPT preempts audiobook + earcon + speech + book
cancelled; CRITICAL same but preempts at a higher threshold.

### Stage 2 — `InputClaim` + `MicRouter` wiring

Pulled earlier than originally planned (was Stage 4). Rationale: the
motivating use cases (calls, panic button, voice memo) exercise
`InputClaim` as the lynchpin. Validating the MicRouter seam and the
provider suspend/resume contract early means Stages 3+4 don't pile on
an untested audio-path seam.

**Deliverables**:

- `InputClaim` SideEffect type in SDK (with `on_mic_frame`,
  `speaker_source`, `suspend_voice_provider`, `on_claim_end`,
  `yield_policy`)
- `ClaimEndReason` enum (`NATURAL`, `USER_PTT`, `PREEMPTED`, `ERROR`)
- `ClaimHandle` with `cancel()` and `wait_end()`
- `SkillContext.start_input_claim(claim)` — direct entry point for
  event-driven latching (no tool call in the causal chain)
- `ToolResult.side_effect=InputClaim(...)` remains for tool-dispatched
  claims (voice memo)
- `MicRouter.claim(handler) -> ClaimHandle` / `handle.release()` —
  swaps default handler
- Coordinator latch sequence (framework invariant, test-enforced):
  suspend provider FIRST, THEN swap mic routing. Prevents audio leak.
- `on_claim_end` fires on all termination paths (NATURAL, USER_PTT,
  PREEMPTED, ERROR)
- Interaction with inject_turn arbitration: claim's `yield_policy` is
  consulted same as AudioStream's
- Tests per "Test surface" above for this primitive
- `docs/skills/README.md`: using `InputClaim` section
- `docs/architecture.md`: updated audio routing description

**UX validation**: throwaway voice-memo skill. `record_memo(seconds=10)`
tool returns `InputClaim(on_mic_frame=writer.write, speaker_source=None)`.
Speak for 10 seconds. Confirm: WAV file created; voice provider resumes
cleanly; next PTT works; no leaked tasks.

Also manual smoke for the direct-entry path: a small test harness calls
`ctx.start_input_claim(...)` directly (simulating a panic-button event)
and confirms latch + end behaves identically to the tool-driven path.

### Stage 3 — Supervised `background_task`

**Deliverables**:

- `ctx.background_task(name, coro_factory, *, restart_on_crash, max_restarts_per_hour, on_permanent_failure)`
- Task supervisor module: crash logging via `aexception`, restart with
  exponential backoff, permanent-failure event + optional callback
- `PermanentFailure` dataclass (last exception + restart count + elapsed)
- Teardown: all tasks cancelled on skill teardown
- Tests: normal run, crash-recover, budget-exceeded, permanent-failure
  callback fires, callback-that-raises doesn't recurse
- `docs/skills/README.md`: using `background_task` section
- `docs/observability.md`: new event names documented

**UX validation**: extend the Stage 1 hello skill to deliberately crash
its background task. Confirm it restarts. Bump crash rate past the
budget; confirm `on_permanent_failure` callback fires, dev event
emitted.

### Stage 4 — `ClientEvent` + `server_event` + capabilities

**Deliverables**:

- Wire protocol additions: `{"type": "client_event", "event": <str>, "payload": {...}}` (C→S)
  and `{"type": "server_event", "event": <str>, "payload": {...}}` (S→C)
- `hello` message gains `capabilities: list[str]` array (old clients
  without field treated as `capabilities=[]`)
- Server-side dispatcher for inbound `client_event` (routes to skill
  subscribers)
- `ctx.subscribe_client_event(key, handler)` on `SkillContext`
- `ctx.emit_server_event(key, payload)` on `SkillContext` — no-op when
  client capabilities don't include `server_event`, with debug log
- Unsubscription at `teardown()`
- Namespace convention documented: `huxley.*` reserved; skills use
  `<skill-name>.*`
- Tests: subscription, multi-subscriber dispatch, unsubscription,
  emit_server_event with and without capability, capabilities fallback
- Browser dev client: send-event dev panel or Shift+E shortcut for
  manual event injection + a text log of received `server_event`
- `docs/protocol.md`: updated hybrid protocol spec with symmetric events
  - capabilities handshake
- `docs/skills/README.md`: using `subscribe_client_event` +
  `emit_server_event` sections

**UX validation**: browser dev client fires a custom event; a toy skill
receives it and calls `inject_turn`. Separately, a toy skill emits a
`server_event`; browser dev client logs receipt. Confirms both directions.

### Post-stage: enables new skills

- **T1.8 `huxley-skill-reminders`** — uses `background_task` +
  `inject_turn`. Solo skill; no coordination with other primitives.
- **T1.9 `huxley-skill-messaging`** (inbound) — uses `background_task` +
  `inject_turn` + maybe `ClientEvent`.
- **T1.10 `huxley-skill-calls`** — uses all four primitives. Blocks on
  Stage 4 completion. Requires a voice-call provider integration
  (Twilio/SIP — separate design work).

None of these require framework changes. They're skill work on top of
the I/O plane.

---

## Descope candidates

1. **Pause-resume for preempted media** (discussed in original proactive
   spec). Cancel-and-resume-via-command preserved. Revisit if a skill
   needs it (music with deep state, not audiobook).

2. **Client-side ducking.** Server-side software gain. Revisit when
   ESP32 firmware wants smart audio.

3. **`inject_turn` queue persistence across restart.** Lost on restart;
   skills re-fire from their own schedulers. Revisit if observed to be
   painful.

4. **`InputClaim` stacking / arbitration.** If two skills claim mic at
   once, framework cancels the previous claim and issues the new one
   (last-writer-wins). Same pattern as `current_media_task`. Revisit if
   ever ambiguous.

5. **Retry semantics in `inject_turn`.** Skill concern. Framework stays
   simple.

6. **Quiet hours / do-not-disturb.** Not built. When first needed, adds
   a persona config block `quiet_hours: [22:00-07:00]` that demotes all
   non-CRITICAL urgencies to AMBIENT during the window. Trivial addition.

7. **Multi-tenant event-key collision detection.** No enforcement.
   Namespace convention is documentation, not runtime check. Revisit
   if third-party skill collisions occur.

---

## Open question for Mario

One and only one genuine product-behavior question remains open after
the research and the design:

**Earcon sourcing for the three tier earcons.** The spec locks that
each urgency tier (CHIME_DEFER, INTERRUPT, CRITICAL) gets a distinct,
persona-owned earcon. You chose "real audio, synths are hard." Before
Stage 1 ships, three specific audio files need to exist in the AbuelOS
persona. Either you pick them from a sound library, or we defer the
actual audio selection (framework plays nothing when the earcon is
missing, logs a warning) and curate the audio as a separate task.

**My recommendation**: defer audio selection. Stage 1 ships with empty
earcon slots that play silently. You curate three sounds as a standalone
task (you have good taste on this — you picked the book_start /
book_end / news_start sounds). Keeps Stage 1 from blocking on audio
curation.

No other product/UX questions. Everything else is locked.

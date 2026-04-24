# Extensibility — what fits the framework, what doesn't

Honest answer to the question _"can I build skill X with Huxley today?"_ This doc walks a representative spread of skill ideas through the architecture, separating **what builds with no framework changes**, **what builds but the pattern isn't formalized yet**, and **what hits a real design gap**.

The intent: make it obvious to a prospective skill author what shape their skill needs to take, and make the framework's actual limits visible — not hidden behind "you'd just have to..."

For the building blocks themselves, see [`skills/README.md`](./skills/README.md). For the philosophy, see [`vision.md`](./vision.md).

## The shape every skill takes today

A skill is a Python class implementing the `Skill` protocol from `huxley_sdk`:

- Declares **tools** (OpenAI function schemas) the LLM can call.
- Implements `handle(tool_name, args) -> ToolResult` — synchronous request/response within the model's turn.
- Optionally returns `ToolResult.side_effect` — today `AudioStream(factory=...)` for tools that want to stream PCM (audiobook playback) into the same audio channel as model speech; the framework invokes the factory after the model finishes speaking. Future side-effect kinds (notifications, state updates) reuse the same shape.
- Receives a `SkillContext` at `setup()` carrying a per-skill `logger`, namespaced KV `storage`, the `persona_data_dir`, and a `config` dict from `persona.yaml`.

Everything below is graded against that shape.

## ✅ Builds today, no framework changes

These are pure HTTP-API skills or stateful in-memory flows. Each is a single `huxley-skill-<name>` package with its own deps.

| Skill idea                  | Pattern                                                                                                                                                                                                                                                                                                                 |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Telegram (calls + messages) | Lives in `huxley-skill-telegram`: `call_contact` + `send_message` outbound, p2p voice calls via `InputClaim`, inbound text via per-sender debounce/coalesce buffer + `inject_turn`, bounded backfill on connect. Single Pyrogram session shared across both modes. See [`docs/skills/telegram.md`](skills/telegram.md). |
| Web search                  | `search_web(query)` → call Brave/Bing/Tavily → return top results as text in `output`                                                                                                                                                                                                                                   |
| News headlines              | `read_news(topic)` → hit a news API (cache last 5 minutes in KV) → return headlines text                                                                                                                                                                                                                                |
| Weather                     | `get_weather(location)` → call OpenWeather → return short-form text                                                                                                                                                                                                                                                     |
| Outbound call initiation    | `call_contact(name)` → trigger a Twilio outbound call → return "llamando a Carlos." Just _initiating_ the call, not voice routing through Huxley.                                                                                                                                                                       |
| Trivia game                 | `start_trivia()` / `submit_answer()` — multi-turn flow with state held on `self._current_game`. Skill instance is long-lived; in-memory state is fine                                                                                                                                                                   |

For all of these:

- Add HTTP/SDK deps (`httpx`, `python-telegram-bot`, etc.) to your skill's `pyproject.toml`. Core never knows.
- The model speaks the response naturally based on whatever you put in `ToolResult.output`.
- Per-skill state (contacts list, cached results, game session) lives on the skill instance or in `ctx.storage`.

## ⚠️ Builds today, undocumented pattern

### Long-running background tasks (Bluetooth scale, polling daemon, MQTT listener)

Skills that need to maintain a persistent connection (BLE socket, websocket, MQTT subscription) can spawn an `asyncio` task in `setup(ctx)` and cancel it in `teardown()`. This works, but the SDK doesn't formally bless it. A reference pattern:

```python
class ScaleSkill:
    def __init__(self) -> None:
        self._scanner_task: asyncio.Task[None] | None = None
        self._latest_weight: float | None = None

    async def setup(self, ctx: SkillContext) -> None:
        self._scanner_task = asyncio.create_task(self._scan_loop())

    async def teardown(self) -> None:
        if self._scanner_task:
            self._scanner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scanner_task

    async def _scan_loop(self) -> None:
        # Maintain BLE connection, update self._latest_weight on each reading.
        ...

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        # Tool calls just read self._latest_weight and return it.
        ...
```

**Why this is "undocumented" rather than "supported":** the framework doesn't currently observe whether a skill's background task crashes, restart it, or surface its errors. If your scanner dies silently, the next tool call returns stale data with no audit trail. Add structured logging in your task loop until the SDK formalizes this.

## ✅ Previously-gap, now shipped

### ~~Proactive notifications~~ → `ctx.inject_turn` is live

Resolved (T1.4 Stage 1c.3, 2026-04-18). `ctx.inject_turn(prompt, *, dedup_key=?, priority=?)` is the primitive skills use to speak without a user turn. Three priority tiers (`NORMAL` / `BLOCK_BEHIND_COMMS` / `PREEMPT`) let a skill choose how urgently to barge over content or calls. `inject_turn_and_wait` blocks until the LLM finishes speaking for skills that need to time a follow-up (e.g., announce-then-bridge-audio on inbound call). See [`skills/README.md`](./skills/README.md#priority-optional-priority) for the guide.

### ~~Live phone calls through Huxley~~ → `InputClaim` is live on COMMS

Resolved (T1.4 Stage 2, 2026-04-19; and Stage 2b, 2026-04-24). Skills latch the mic + speaker via `InputClaim` or direct `ctx.start_input_claim(claim)`. The Activity lives on `Channel.COMMS` (priority 150); during an active claim the audiobook backgrounds with patience and auto-resumes on claim-release. Single-slot policy — a second claim raises `ClaimBusyError`. `huxley-skill-telegram` is the live consumer: full-duplex p2p voice calls, inbound + outbound, bridged through the framework's mic/speaker plumbing. The architecture-level warning "one-OpenAI-session-deep" was correct at the time; the fix was the focus-plane pivot (AVS-style channel arbitration) rather than a full-system redesign.

## 🔑 Concerns to address before more skills land

### Per-skill secrets

Telegram bot tokens, Twilio API keys, OpenWeather keys, etc. These can't live in `persona.yaml` (git-tracked) and shouldn't be hard-coded in the skill. Today's only option is `os.environ["MY_SKILL_TOKEN"]` in the skill's `setup()`. That works but is unstructured.

A clean fix during stage 4 (persona loader): support `${ENV_VAR}` interpolation in `persona.yaml` config values, so a persona declares the shape of the secret without storing it:

```yaml
skills:
  telegram:
    bot_token: ${HUXLEY_TELEGRAM_TOKEN}
```

**Status:** small but easy to get wrong. Decide and document before stage 4 ships.

## What this means for the framework's "extensible" claim

Of seven typical skill ideas (messaging, search, news, weather, calls, smart-device, trivia) **every one builds with the primitives shipped today**. Calls are live (`huxley-skill-telegram`). Proactive speech is live (`inject_turn` + three priority tiers). Smart-device background-task pattern is formalized (T1.4 Stage 3). The two "real design gaps" this doc originally called out are both resolved.

If a future skill idea doesn't fit any of the patterns above, that's the signal to revisit this doc and either document a new pattern or formalize a new framework primitive. Likely future asks: LLM-free alert sounds (would land on the currently-reserved `ALERT` channel); TTL on queued `inject_turn` (tracked as D7); claim-stacking / call-waiting (tracked as a revisit trigger in the 2026-04-24 ADR).

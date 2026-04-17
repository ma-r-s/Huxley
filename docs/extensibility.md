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

| Skill idea               | Pattern                                                                                                                                               |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Telegram outbound        | `send_message(contact, body)` → resolve contact via KV (`contact:carlos` → chat_id) → `httpx.post(...)` → return confirmation in `output`             |
| Web search               | `search_web(query)` → call Brave/Bing/Tavily → return top results as text in `output`                                                                 |
| News headlines           | `read_news(topic)` → hit a news API (cache last 5 minutes in KV) → return headlines text                                                              |
| Weather                  | `get_weather(location)` → call OpenWeather → return short-form text                                                                                   |
| Outbound call initiation | `call_contact(name)` → trigger a Twilio outbound call → return "llamando a Carlos." Just _initiating_ the call, not voice routing through Huxley.     |
| Trivia game              | `start_trivia()` / `submit_answer()` — multi-turn flow with state held on `self._current_game`. Skill instance is long-lived; in-memory state is fine |

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

## ❌ Real design gaps

These do not fit the framework as it exists. They require new primitives, not just a new skill.

### 1. Proactive notifications (skill wants to interrupt the user)

Every interaction today is **request → response within the same turn**. Nothing in the framework can:

- Wake the user at a specific time (_"oye, ya es hora de tomar la pastilla"_)
- React to an inbound event (_"te llegó un mensaje de Carlos"_)
- Tell the user something the skill discovered on its own

Why: the `TurnCoordinator` only spawns audio in response to a function call, which is itself a response to the user's mic input. There's no API for "skill wants to start speech now."

What a fix probably looks like: an `ctx.notify(text)` method on the SDK that injects a synthetic system turn through the `SessionManager` ("system: notify the user that X happened") and lets the LLM speak it. Has non-trivial protocol implications — needs a server-initiated `assistant_turn_start` message to the client, careful interaction with PTT-in-progress, and decision logic about whether to interrupt an ongoing audiobook.

**Status:** acknowledged on the roadmap. This is the next major framework beat after persona-loader (stage 4) ships. Should land before any reminders / inbound-message use case is attempted.

### 2. Live phone calls _through_ Huxley (bidirectional voice routing)

"Initiate a Twilio call to Carlos" is fine — that's just an HTTP API call. But "let the user actually talk to Carlos through Huxley" requires:

- Routing the user's mic frames _away_ from OpenAI Realtime to a SIP/Twilio media stream.
- Routing the remote party's audio _back_ through `server.send_audio` (or an entirely separate audio plane).
- A control-plane state machine for connect / hangup / mute that's parallel to the conversation state machine.

The current audio architecture is one-OpenAI-session-deep. Splitting that to support arbitrary audio sources/sinks is a major redesign.

**Status:** out of scope. Use Twilio's call recording or SMS instead. If a real use case ever forces this, it's a separate framework version, not a stage in the current refactor.

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

Of seven typical skill ideas (messaging, search, news, weather, calls, smart-device, trivia):

- **Six** build cleanly with no framework changes.
- **One** (smart-device) needs a documented background-task pattern but no protocol change.
- **One** (live phone calls) is genuinely out of scope and should stay that way.

The single missing primitive that will bite real personas is **proactive notifications** — and that's the gap the roadmap should close before AbuelOS-v∞ (reminders, inbound messages) is attempted.

If a future skill idea doesn't fit any of the patterns above, that's the signal to revisit this doc and either document a new pattern or formalize a new framework primitive.

# Huxley

**An open-source framework for building real-time voice AI agents you can actually own.**

Huxley is a Python server. You give it a **persona** (a YAML file: name, voice, language, personality, behavioral constraints, list of skills) and a set of **skills** (Python packages that extend what the agent can do). It handles the rest: audio I/O, voice provider session, turn sequencing, tool dispatch, side-effect routing.

```bash
git clone <repo> huxley && cd huxley
echo "HUXLEY_OPENAI_API_KEY=sk-..." > server/runtime/.env
uv sync && uv run huxley
# In another terminal:
cd clients/pwa && bun install && bun dev
# Open http://localhost:5173, hold the button, speak.
```

---

## The problem with every other option

| Solution                     | What's wrong                                                                                                                                                                   |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Alexa / Google Home**      | Walled garden. Certification fees. Cloud-only. You don't control the persona, the data, or the skills.                                                                         |
| **OpenAI voice mode**        | One model, one personality. No self-hosting, no custom skills, no behavioral constraints.                                                                                      |
| **Pipecat / LiveKit Agents** | Great pipelines, blank slate. You still have to solve turn sequencing, audio collision, proactive speech, headless deployment, and the skill extensibility model from scratch. |
| **Build it yourself**        | You spend six months building plumbing instead of features.                                                                                                                    |

**Huxley's position:** opinionated enough to solve the hard problems (audio sequencing, interrupt semantics, behavioral constraints, proactive turns), open enough to extend (any skill, any persona, any client device).

---

## What Huxley handles for you

**Turn coordination** — The coordinator sequences everything through one audio channel. Model speech always finishes before tool-produced audio starts. Interrupts are atomic: drop flag → clear queue → flush client buffer → cancel response. You never hear two voices at once or a half-played sentence cut mid-word.

**Skill dispatch** — Skills are Python packages, loaded at startup via `huxley.skills` entry points. A skill declares tools (OpenAI function schemas), handles calls, and returns a result that may include an `AudioStream`, `PlaySound`, `InputClaim`, `CancelMedia`, or `SetVolume` side effect. The framework sequences all of it.

**Proactive speech** — Skills can inject turns without user input: a timer fires at 9am, an inbound call arrives, a news alert fires. `ctx.inject_turn()` queues it; `ctx.inject_turn_and_wait()` blocks until the LLM finishes speaking — useful for announcing events before bridging audio.

**Audio bridging (InputClaim)** — Skills can claim the mic and speaker for full-duplex use — p2p phone calls, voice memos, any external audio source. The framework routes the claim through a FocusManager that prevents collisions with model speech and other content streams.

**Persona-as-config** — The agent's entire identity lives in `persona.yaml`: voice, language, system prompt, behavioral constraints, skill list with per-skill config. Swap the file, get a different agent. No code change.

**Behavioral constraints** — Personas declare constraints (`never_say_no`, `confirm_destructive`, etc.); skills opt in to respecting them. Right for deploying to vulnerable users where "I can't do that" is never an acceptable response.

**Headless server** — Huxley is a WebSocket server. The browser dev client, an ESP32, a phone — anything that speaks the [wire protocol](./docs/protocol.md) owns the mic and speaker. The server owns the intelligence.

**Structured logging** — Every framework decision emits a namespaced, structured log event with turn-level context. If something breaks in production, the log tells you what happened without asking the user.

---

## Skills

Skills are Python packages. Install one, add it to your `persona.yaml`, done. Shipped skills live in this repo; anything else can be published on PyPI under `huxley-skill-*`.

### Shipped

| Skill                     | What it does                                                                                                                                                                                                                                                                                                  |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `huxley-skill-audiobooks` | Play `.m4b`/`.mp3` audiobooks from a local library. Pause, resume, rewind, fast-forward. Persists position across restarts.                                                                                                                                                                                   |
| `huxley-skill-radio`      | Stream HTTP/Icecast radio stations via `ffmpeg`. Buffered playback with proactive reconnect on drop.                                                                                                                                                                                                          |
| `huxley-skill-news`       | Weather (Open-Meteo) + top headlines (Google News RSS). Cached, narrated in persona voice.                                                                                                                                                                                                                    |
| `huxley-skill-search`     | Open-web search via DuckDuckGo (`ddgs`, no API key). For current/live info the LLM doesn't already know — weather right now, sports scores, what just happened. 4 s timeout, circuit breaker, recovery messages built in.                                                                                     |
| `huxley-skill-timers`     | One-shot and recurring reminders. Fires proactively at the scheduled time; persisted in SQLite so they survive restarts.                                                                                                                                                                                      |
| `huxley-skill-system`     | Volume control, current time.                                                                                                                                                                                                                                                                                 |
| `huxley-skill-telegram`   | Full-duplex p2p Telegram voice calls (inbound + outbound) AND text messages: send by voice, hear inbound messages read aloud (per-sender debounce + coalesce so bursts collapse to one announcement), bounded backfill on connect for missed messages. One Pyrogram userbot session shared across both modes. |

### What the skill system can support — and will

The table below is what Huxley is designed to make buildable. Each row is a standalone Python package, no framework changes needed, published on PyPI and enabled with one line in `persona.yaml`.

#### Communication

| Skill                     | What it would do                                                                                                        |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `huxley-skill-sms`        | Read and dictate SMS messages via Twilio. "Read me my messages" / "Reply to Mario: running 10 minutes late."            |
| `huxley-skill-whatsapp`   | WhatsApp message inbox. Read unread messages aloud, compose and send by voice.                                          |
| `huxley-skill-email`      | Gmail/IMAP email. Read unread messages with sender + subject, compose and send by dictation, archive, flag.             |
| `huxley-skill-comms-pstn` | Outbound and inbound phone calls via Twilio SIP. The Telegram skill's architecture, applied to the plain phone network. |
| `huxley-skill-slack`      | Read unread Slack messages by channel, compose replies, set a status.                                                   |
| `huxley-skill-signal`     | Signal messages via signal-cli. End-to-end encrypted voice-driven messaging.                                            |

#### Smart home & IoT

| Skill                        | What it would do                                                                                                                                         |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `huxley-skill-hue`           | Control Philips Hue lights. On/off, dim, colour, scenes, rooms. "Dim the living room to 30% and set it warm."                                            |
| `huxley-skill-homeassistant` | Gateway to a local Home Assistant instance. Exposes every HA entity as a tool — lights, switches, sensors, scenes, scripts. One skill, your entire home. |
| `huxley-skill-thermostat`    | Nest/Ecobee/Honeywell — read temperature, set target, switch mode. "It's cold — turn it up two degrees."                                                 |
| `huxley-skill-doorbell`      | Proactive notification when someone rings the doorbell (Frigate/MQTT event → `inject_turn`). "Someone is at the front door."                             |
| `huxley-skill-tv`            | Control Roku, Apple TV, Chromecast — play, pause, next, open app, search content.                                                                        |
| `huxley-skill-sonos`         | Play, pause, volume, and queue management on Sonos speakers. Ducks under the agent's voice automatically via `InputClaim`.                               |
| `huxley-skill-robot-vacuum`  | Start, stop, dock a Roomba or Roborock. Report cleaning status and surface area covered.                                                                 |
| `huxley-skill-air-quality`   | Read CO₂, VOC, PM2.5 from an Awair/AirGradient sensor. Proactive alert when thresholds are crossed.                                                      |
| `huxley-skill-appliances`    | Washer/dryer/oven status from smart plugs (TP-Link/Shelly). "Laundry is done" — fired as an `inject_turn` when power draw drops.                         |

#### Podcasts & audio content

| Skill                        | What it would do                                                                                                                            |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `huxley-skill-podcasts`      | Subscribe to podcast RSS feeds. "Play the latest episode of Lex Fridman." Streams via `AudioStream`, remembers position.                    |
| `huxley-skill-spotify`       | Spotify playback control. Play artist, album, playlist, liked songs. Requires Spotify Connect device on the same network.                   |
| `huxley-skill-youtube-audio` | Extract and stream audio from YouTube via `yt-dlp`. "Play that interview with Jensen Huang."                                                |
| `huxley-skill-ambient`       | Stream ambient/focus audio (rain, coffee shop, white noise) as a `MIXABLE AudioStream` — ducks under the agent's voice, resumes underneath. |
| `huxley-skill-text-to-audio` | Read any text content aloud as a long-form `AudioStream`: articles (via URL), ebooks (EPUB), PDFs, clipboard.                               |

#### Calendars, tasks & productivity

| Skill                        | What it would do                                                                                                           |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `huxley-skill-calendar`      | Google Calendar / CalDAV. Read today's agenda, next event, upcoming week. Add events and reminders by voice.               |
| `huxley-skill-tasks`         | Todoist / Things / OmniFocus. Add tasks, mark complete, read today's list. "Add 'call the dentist' to my to-do list."      |
| `huxley-skill-notes`         | Obsidian / Notion / Apple Notes. Create voice notes (transcribed immediately), read back the last note, search by keyword. |
| `huxley-skill-shopping-list` | Persistent voice-managed grocery list. Add items, read the list, mark bought. Syncs to AnyList / Google Keep.              |
| `huxley-skill-reminders`     | Apple Reminders / Google Tasks integration. Location-aware reminders ("remind me when I get home"), time-based, recurring. |
| `huxley-skill-focus`         | Start/stop a Pomodoro timer with audio cues. Log sessions to daily storage. "Focus for 45 minutes, then take a break."     |

#### Information & search

| Skill                     | What it would do                                                                                                                  |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `huxley-skill-search`     | Web search via Brave/Tavily. Fetches and summarises top results in the persona's voice and language.                              |
| `huxley-skill-wikipedia`  | Wikipedia article summaries. "Who was Ada Lovelace?" — concise, sourced, narrated.                                                |
| `huxley-skill-translate`  | Real-time translation via DeepL/Google. "How do you say 'where is the pharmacy' in French?"                                       |
| `huxley-skill-dictionary` | Word definitions, synonyms, etymology (Merriam-Webster). "What does 'sanguine' mean?"                                             |
| `huxley-skill-wolfram`    | Wolfram Alpha for calculations, conversions, scientific facts, and equation solving.                                              |
| `huxley-skill-flights`    | Live flight status (AeroAPI). "Is flight AA 245 on time?" Proactive gate-change alerts via `inject_turn`.                         |
| `huxley-skill-packages`   | Package tracking (EasyPost/17track). "Where's my Amazon order?" Proactive delivery notification.                                  |
| `huxley-skill-stocks`     | Real-time and historical quotes (Alpaca / Yahoo Finance). Portfolio value by voice. Price-alert `inject_turn` on threshold cross. |
| `huxley-skill-sports`     | Live scores, standings, next fixtures — any league — via API-Football / ESPN.                                                     |
| `huxley-skill-traffic`    | Commute time (Google Maps / TomTom). "How long to downtown right now?" Morning briefing on the usual route.                       |

#### Health & care

| Skill                      | What it would do                                                                                                                            |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `huxley-skill-medications` | Per-medication schedule, dose log, refill tracker. Scheduled `inject_turn` reminders. "Did you take your blood pressure pill this morning?" |
| `huxley-skill-vitals-log`  | Voice-driven health log: blood pressure, blood glucose, weight, pain level. Stores in SQLite, reads trend summaries back.                   |
| `huxley-skill-hydration`   | Hourly hydration nudges via `inject_turn`. Tracks glass count, adjusts goal based on weather API temperature.                               |
| `huxley-skill-breathing`   | Guided breathing exercises (4-7-8, box, coherent) played as `AudioStream` with synthesized cue tones.                                       |
| `huxley-skill-sleep`       | Bedtime wind-down routine: dim lights (via Hue skill), start ambient sound, set a morning alarm.                                            |
| `huxley-skill-mood`        | Daily mood + symptom check-in. Stored in KV, week summary available on demand. "How have you been feeling this week?"                       |
| `huxley-skill-emergency`   | One-word SOS: calls a preset contact via PSTN, sends GPS coordinates via SMS, speaks a calm reassurance.                                    |

#### Finance

| Skill                      | What it would do                                                                                        |
| -------------------------- | ------------------------------------------------------------------------------------------------------- |
| `huxley-skill-budget`      | Read-only bank balance and recent transactions via Plaid. "What did I spend on groceries this week?"    |
| `huxley-skill-expense-log` | Voice-driven expense entry. "Logged: lunch, $14.50." Exports to CSV, syncs to Google Sheets.            |
| `huxley-skill-bills`       | Bill due-date tracker with `inject_turn` reminders 3 days out. "Your electricity bill is due Friday."   |
| `huxley-skill-crypto`      | Live crypto prices and portfolio value (CoinGecko). Price alert `inject_turn` at configured thresholds. |

#### Vision & documents

| Skill                        | What it would do                                                                                                   |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `huxley-skill-vision`        | Describe what's in front of a connected camera using a vision model. "What does this say?" — OCR + narration.      |
| `huxley-skill-pdf-reader`    | Read and summarise PDF documents. Drop a file in the persona data dir, ask about it by name.                       |
| `huxley-skill-scan`          | Trigger a mobile scan via an iPhone shortcut, read the OCR text aloud. Bridge between paper and voice.             |
| `huxley-skill-face-greeting` | Recognise household members from a camera feed, fire a personalised `inject_turn` greeting when they enter a room. |

#### Developer & infrastructure

| Skill                         | What it would do                                                                                                       |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `huxley-skill-github`         | Read open issues, PRs, and CI status by voice. "What's failing on the main branch?"                                    |
| `huxley-skill-server-monitor` | Ping a list of hosts/services, report uptime. Proactive `inject_turn` alert on outage.                                 |
| `huxley-skill-shell`          | Execute pre-approved shell commands by voice (explicit allowlist, no free-form execution). "Deploy the staging build." |
| `huxley-skill-ntfy`           | Forward any ntfy.sh notification topic as a spoken `inject_turn`. Universal webhook-to-voice bridge.                   |

#### Education & language

| Skill                         | What it would do                                                                                                                         |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `huxley-skill-flashcards`     | Spaced-repetition flashcard drill, entirely by voice. Any deck in CSV/Anki format.                                                       |
| `huxley-skill-language-tutor` | Vocabulary and pronunciation drills. Detects the user's native language from `persona.yaml`, teaches the target language.                |
| `huxley-skill-storytime`      | Generates and narrates original bedtime stories via the LLM. Characters and theme by voice. "Tell me a story about a brave little frog." |
| `huxley-skill-quiz`           | Trivia game — categories, difficulty, score tracking — entirely by voice. Persistent high scores in SQLite.                              |

#### Household & daily life

| Skill                           | What it would do                                                                                                              |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `huxley-skill-recipes`          | Recipe search and step-by-step hands-free cooking guidance. "Read me the next step." Automatically paces through a recipe.    |
| `huxley-skill-grocery-delivery` | Add to and order from an Instacart/Ocado cart by voice. Confirms order total before submitting.                               |
| `huxley-skill-food-log`         | Voice-driven nutrition log (Open Food Facts). "Log a banana and a coffee." Daily calorie and macro summary.                   |
| `huxley-skill-journal`          | Daily voice journaling. Transcribed and stored locally. Weekly prompts generated by the LLM, entries readable back on demand. |
| `huxley-skill-affirmations`     | Personalised motivational messages at configurable times. Generated by the LLM, tone tuned to the persona's personality.      |

---

## Reference persona — AbuelOS

The canonical persona in the repo is **AbuelOS**: a Spanish-language companion for an elderly blind user. It enforces the `never_say_no` constraint (every request gets an attempt or a warm alternative, never a refusal), uses a slow warm voice, and enables audiobooks + radio + news + timers + Telegram calls. It's the worked example for everything in the framework — the hardest UX requirements, on the most constrained hardware.

---

## Architecture

```
┌────────────────────────────┐
│  persona.yaml              │   Identity, constraints, skill list
└────────────┬───────────────┘
             │
             ▼
┌────────────────────────────┐
│  Huxley runtime            │   WebSocket server, session manager,
│  (server/runtime)          │   turn coordinator, focus manager,
│                            │   skill registry, storage
└────┬──────────┬────────────┘
     │          │          │
     ▼          ▼          ▼
┌─────────┐ ┌────────┐ ┌──────────────┐
│ Voice   │ │ Skills │ │ Client       │
│ provider│ │ (SDK)  │ │ (browser /   │
│ (OpenAI │ │        │ │  ESP32 / any)│
│  RT)    │ │        │ │              │
└─────────┘ └────────┘ └──────────────┘
```

The framework never imports skill code directly — skills register via Python entry points. The SDK gives skills a typed context (logger, namespaced storage, persona data dir, config, framework hooks) with no framework internals leaking through.

---

## Writing a skill

```python
# my_package/skill.py
from huxley_sdk import Skill, ToolDefinition, ToolResult, SkillContext, AudioStream

class LightsSkill:
    @property
    def name(self) -> str: return "lights"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(
            name="set_lights",
            description="Turn the lights on or off.",
            parameters={"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]},
        )]

    async def setup(self, ctx: SkillContext) -> None:
        self._api_key = ctx.config["api_key"]

    async def handle(self, tool_name: str, args: dict) -> ToolResult:
        # call your smart-home API here
        return ToolResult(output='{"ok": true}')

    async def teardown(self) -> None: ...
```

```toml
# pyproject.toml
[project.entry-points."huxley.skills"]
lights = "my_package.skill:LightsSkill"
```

Enable it in any persona:

```yaml
skills:
  lights:
    api_key: "..."
```

Full guide: [`docs/skills/README.md`](./docs/skills/README.md)

---

## Writing a persona

```yaml
# server/personas/myagent/persona.yaml
version: 1
name: MyAgent
voice: alloy
language_code: en
transcription_language: en
timezone: America/New_York
system_prompt: |
  You are a concise home assistant. Answer in English.
  You control lights, timers, and can read the weather.
constraints: []
skills:
  lights:
    api_key: "..."
  system: {}
  timers: {}
```

```bash
HUXLEY_PERSONA=myagent uv run huxley
```

Full guide: [`docs/personas/README.md`](./docs/personas/README.md)

---

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (`pip install uv`)
- [bun](https://bun.sh) (dev client only)
- `ffmpeg` + `ffprobe` on PATH (radio and Telegram skills)
- OpenAI API key with Realtime API access

---

## Development

```bash
uv sync --all-packages             # install workspace

# Lint + typecheck
uv run ruff check server/
uv run mypy server/sdk/src server/runtime/src

# Tests (594 total)
uv run --package huxley-sdk pytest server/sdk/tests/                            # 72
uv run --package huxley pytest server/runtime/tests/                                # 370
uv run --package huxley-skill-audiobooks pytest server/skills/audiobooks/tests/  # 61
uv run --package huxley-skill-timers pytest server/skills/timers/tests/          # 30
uv run --package huxley-skill-news pytest server/skills/news/tests/              # 18
uv run --package huxley-skill-radio pytest server/skills/radio/tests/            # 19
uv run --package huxley-skill-telegram pytest server/skills/telegram/tests/      # 90

# Dev client
cd clients/pwa && bun run check
```

---

## Run as a background service

```bash
# macOS (launchd — starts at login, restarts on crash)
./scripts/launchd/install.sh
tail -f ~/Library/Logs/Huxley/huxley.log

# Linux — write a systemd unit, same shape
```

---

## Documentation

| Doc                                                    | What it covers                                     |
| ------------------------------------------------------ | -------------------------------------------------- |
| [`docs/vision.md`](./docs/vision.md)                   | What Huxley is and who it's for                    |
| [`docs/concepts.md`](./docs/concepts.md)               | Core vocabulary: persona, skill, turn, side effect |
| [`docs/architecture.md`](./docs/architecture.md)       | Framework internals                                |
| [`docs/protocol.md`](./docs/protocol.md)               | WebSocket wire protocol for clients                |
| [`docs/turns.md`](./docs/turns.md)                     | Turn coordinator spec                              |
| [`docs/skills/README.md`](./docs/skills/README.md)     | Skill authoring guide (full SDK surface)           |
| [`docs/personas/README.md`](./docs/personas/README.md) | Persona authoring guide                            |
| [`docs/extensibility.md`](./docs/extensibility.md)     | What the framework can and can't do today          |
| [`docs/observability.md`](./docs/observability.md)     | Logging conventions + debugging workflow           |
| [`docs/decisions.md`](./docs/decisions.md)             | Architectural decision log                         |
| [`docs/roadmap.md`](./docs/roadmap.md)                 | What's next                                        |

---

**Status:** pre-1.0 — framework runs end-to-end, AbuelOS persona is in daily use. Contributions welcome.

**License:** MIT

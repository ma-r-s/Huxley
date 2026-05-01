# huxley-skill-telegram

Full-duplex Telegram voice + text for [Huxley](https://github.com/ma-r-s/Huxley). Single Pyrogram userbot, p2p calls via `py-tgcalls` + `ntgcalls`.

> **Status**: bundled with the Huxley repo as a workspace member. The most surface-area first-party skill — heavy native dependency, full-duplex audio bridging.

## What it does

- **`call_contact`** — "call mom" — dials a Telegram contact. Live PCM bridges through Huxley's `InputClaim` mic/speaker plumbing.
- **`send_message`** — "tell mom I'll be late" — sends a text DM to the named contact via the userbot.
- **`answer_incoming_call`** / **`reject_incoming_call`** — when `inbound.enabled`, the skill auto-answers (accepts immediately to preserve WebRTC audio quality) and announces via `inject_turn`. The two tools let the LLM accept/reject from the user's voice command.

Inbound text messages debounce-coalesce per sender: a chatty sender's "hola/papá/¿estás?" lands as one announcement, not three. Bounded backfill on connect: last N hours of unread messages from whitelisted contacts surface as a single coalesced announcement at session start.

## Configure

Credentials live in `<persona>/data/secrets/telegram/values.json` (per-persona, gitignored, perms `0700/0600`):

```json
{
  "api_id": "12345678",
  "api_hash": "abcdef0123456789abcdef0123456789",
  "userbot_phone": "+57..."
}
```

Get `api_id` + `api_hash` from [my.telegram.org/apps](https://my.telegram.org/apps). `userbot_phone` is the spare SIM the userbot signs in as — only consulted on first-run SMS auth, then the Pyrogram session file in `<persona.data_dir>/` authenticates silently.

Resolution priority: `ctx.secrets` (preferred) → `HUXLEY_TELEGRAM_*` env vars (fallback) → `persona.yaml` (dev/test only). See [`docs/skills/telegram.md`](../../docs/skills/telegram.md) § Credentials for the full priority spec.

The rest of the config lives in `persona.yaml`:

```yaml
skills:
  telegram:
    contacts:
      mama: "+57..."
      papa: "+57..."
    inbound:
      enabled: true
      auto_answer: contacts_only # contacts_only | all | false
      unknown_messages: drop # drop | announce
      debounce_seconds: 2.5
      backfill_hours: 6
      backfill_max: 50
```

`config_schema = None` — contacts is a dict of user-defined keys, `inbound` is a nested object, first-time auth is an SMS-code flow. Doesn't fit JSON Schema. v2's PWA falls back to "edit YAML directly." See [`docs/skills/telegram.md`](../../docs/skills/telegram.md) for the full design.

## Requirements

- **Native dependency**: `ntgcalls` is a compiled C++ extension. First install on a Pi can take 60–90s; subsequent installs hit the wheel cache.
- A spare Telegram-registered SIM for the userbot (NOT the user's own Telegram account — the userbot logs into its own account).
- Network access to Telegram's MTProto + p2p WebRTC peers.

## Development

```bash
uv run --package huxley-skill-telegram pytest server/skills/telegram/tests
uv run ruff check server/skills/telegram
uv run mypy server/skills/telegram/src
```

Tests use a `StubTransport` so no `pyrogram` / `py-tgcalls` import is needed; the entire test suite runs offline.

## License

MIT — see [`LICENSE`](LICENSE).

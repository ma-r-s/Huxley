# Changelog

## 0.1.0 — 2026-05-01

Initial release. Bundled with Huxley as a workspace member. The most surface-area first-party skill — full-duplex voice + text comms over a single Pyrogram userbot session.

### Added

- `TelegramSkill` with four voice tools: `call_contact`, `send_message`, `answer_incoming_call`, `reject_incoming_call`.
- p2p voice calls via `py-tgcalls` + `ntgcalls` (native C++ backend). Inbound + outbound, full-duplex live PCM bridged through Huxley's `InputClaim` mic/speaker plumbing.
- Inbound text messages with per-sender debounce/coalesce: a chatty sender's "hola/papá/¿estás?" lands as one announcement, not three competing `inject_turn`s.
- Bounded backfill on connect: last N hours of unread messages from whitelisted contacts surface as a single coalesced announcement at session start.
- T2.9: creds (api_id, api_hash, userbot_phone) read from `ctx.secrets` (the framework's official API), with env-var + persona.yaml fallbacks for transition.
- `config_schema = None` declared (contacts dict + nested inbound + SMS first-time-auth flow don't fit JSON Schema).
- `data_schema_version = 1`.

### Notes

- Native dependency: `ntgcalls` is a compiled C++ extension. Install on a Pi can take 60–90s the first time; subsequent installs use the wheel cache.
- Pyrogram session file persists in `<persona.data_dir>/`; first-time SMS auth is the only time `userbot_phone` is consulted.
- Contacts (name → phone) live in `persona.yaml` — they're per-family reference data, not secrets.

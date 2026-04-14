# Roadmap

The product evolves as a ladder. Each rung adds exactly one layer of capability; nothing is built speculatively.

## Legend

- **P0** — v0 / MVP: without this, there is no product
- **P1** — v1: the "ship to grandpa" bar
- **P2** — v2: next wave of skills once v1 is stable
- **P3** — later: worth considering once v2 lands
- **Excluded** — explicitly out of scope

## v0 — what exists today

The scaffolding: server + browser dev client + the audiobooks skill (partial).

| Component                                        | Status                                                                                        |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| Python WebSocket audio server                    | ✅ built                                                                                      |
| OpenAI Realtime API relay                        | ✅ built                                                                                      |
| Skill registry + protocol                        | ✅ built                                                                                      |
| State machine                                    | ✅ built (3 states; media is owned by the turn coordinator, not the session)                  |
| Turn coordinator (audio sequencing + interrupts) | ✅ built — see [`turns.md`](./turns.md)                                                       |
| ffmpeg-based audiobook streamer                  | ✅ built (`AudiobookPlayer.stream()` async generator)                                         |
| SQLite storage                                   | ✅ built                                                                                      |
| Audiobooks skill (search + play + basic control) | 🟡 partial — see [`skills/audiobooks.md#current-state`](./skills/audiobooks.md#current-state) |
| Browser dev client (SvelteKit)                   | ✅ end-to-end audio path; one-button UX; thinking-tone gap filler                             |

## v1 — the MVP (Mario's bar)

**Done when**: grandpa can search, play, navigate, pause, and resume audiobooks by voice alone, with no dead-end "no" responses, without Mario's help.

- [P0] Audiobooks skill **complete** — full checklist in [`skills/audiobooks.md#gaps--todo-for-v1`](./skills/audiobooks.md#gaps--todo-for-v1)
- [P0] M4B support with embedded chapter metadata
- [P0] Cross-session resume (save on pause + seek + periodic + shutdown)
- [P0] `resume_last` tool — _"sigue con el libro"_ just works
- [P0] Chapter navigation via natural language (_"el siguiente capítulo"_, _"retrocede un minuto"_)
- [P0] System prompt tuned for "nunca decir no" at the LLM layer
- [P0] Browser client: full audio streaming end-to-end (mic → server → OpenAI → server → speaker)
- [P0] End-to-end smoke test with a real M4B and the browser client
- [P0] First session with grandpa, real-world

## v2 — next skills

Once v1 is stable. Each skill is one focused PR with one spec doc under [`skills/`](./skills/).

1. **News** — read headlines from a configurable source. `skills/news.md`.
2. **Music / radio** — streaming radio and local music. `skills/music.md`.
3. **Messaging relay** — outbound text to Mario / family via WhatsApp or voice memo. `skills/messaging.md`. **This is the concrete escape hatch that makes "nunca decir no" more than a verbal promise.**
4. **Contacts** — small hand-edited contact list that messaging depends on. Not a skill; a config file.

## v3 — later

- **Reminders** — meds, appointments. Requires proactive speech (v∞).
- **Memory / recall** — _"¿de qué hablamos ayer?"_
- **Companionship mode** — open-ended chat without a specific skill behind it

## v∞ — when firmware lands

- **Proactive speech** — the system initiates audio without a button press. Needed for reminders and inbound messages.
- **ESP32 walky-talky client** — replaces browser as the production client, same WebSocket protocol, same server.
- **Physical always-findable button** — the one UI element grandpa touches.

## Explicitly excluded

| Feature                      | Why                                                                |
| ---------------------------- | ------------------------------------------------------------------ |
| Wake word                    | Fragile for elderly users; PTT button is more reliable             |
| Religious content            | Mario confirmed out of scope                                       |
| Privacy / no-log mode        | Not a concern for this user                                        |
| Offline operation            | Not worth the complexity for v1; revisit if reliability demands it |
| Languages other than Spanish | Single user, single language                                       |
| Multi-user sessions          | One grandpa, one device                                            |
| Windows / mobile clients     | Browser dev client + ESP32 prod client is the full target set      |

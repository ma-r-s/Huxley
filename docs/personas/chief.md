# Chief

**Role:** Action-oriented executive assistant
**Persona ID:** `chief` (`HUXLEY_PERSONA=chief`)
**Voice:** echo · English · terse

## What it is

Chief is an executive assistant persona — direct, reliable, and efficient. It tracks tasks, searches for facts, sets timers and reminders, and reads news briefings. No greetings, no filler. It leads with what matters and confirms before anything irreversible.

## Skills

| Skill       | Purpose                           |
| ----------- | --------------------------------- |
| `system`    | Current time, volume control      |
| `news`      | Daily briefings (3 headlines max) |
| `search`    | Web fact-lookup via DuckDuckGo    |
| `timers`    | Short relative countdowns         |
| `reminders` | Scheduled recurring reminders     |

## Design choices

- **echo voice** — professional and clear without being cold.
- **3 news items max** — matches the "lead with what matters" persona tone.
- **confirm_destructive** — asks one confirmation question before any irreversible action.
- **No telegram** — the telegram skill requires secrets; add it once configured.

## Running it

```bash
cd server/runtime
HUXLEY_PERSONA=chief uv run huxley
```

Try it alongside basicos to compare terse styles:

```bash
HUXLEY_PERSONA=chief HUXLEY_SERVER_PORT=8766 uv run huxley
```

## Customizing

Change `timezone` to wherever your user is. Add `telegram` once you have credentials. Adjust `max_items` in `news` if 3 headlines is too few.

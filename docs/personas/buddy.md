# Buddy

**Role:** Friendly kids companion
**Persona ID:** `buddy` (`HUXLEY_PERSONA=buddy`)
**Voice:** shimmer · English · cheerful

## What it is

Buddy is a kids-first persona — enthusiastic, patient, and age-appropriate. It uses simple vocabulary, celebrates curiosity, and never refuses a request. When the user's audio is too short to parse, it echoes back what it heard and asks for confirmation before acting.

## Skills

| Skill    | Purpose                                 |
| -------- | --------------------------------------- |
| `system` | Current time, volume control            |
| `news`   | 3 headlines, explained simply           |
| `search` | Safe web search (strict safe-search)    |
| `timers` | Homework, game, and activity countdowns |

## Design choices

- **shimmer voice** — warm and friendly; the right register for a child.
- **child_safe constraint** — filters adult content at the prompt level.
- **never_say_no constraint** — always offers an alternative; kids shouldn't get stuck.
- **echo_short_input constraint** — repeats back short utterances for confirmation before acting.
- **search safesearch: strict** — the strictest DuckDuckGo filter.
- **No reminders** — kids don't usually set their own scheduled reminders; add it for a parent-managed use case.

## Constraint note

The `never_say_no`, `child_safe`, and `echo_short_input` constraints ship with Spanish prompt snippets injected into the system prompt. For Buddy (English), the system prompt also encodes these intents directly in English — so the behavior is enforced regardless of the injection language. See [Constraints](/docs/concepts/constraints) for context.

## Running it

```bash
cd server/runtime
HUXLEY_PERSONA=buddy uv run huxley
```

## Customizing

Add `reminders` if a parent wants to manage the child's schedule. Add `audiobooks` with a library of children's books. Change `timezone` to the child's location so `get_current_time` returns the right time.

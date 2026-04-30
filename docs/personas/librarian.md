# Librarian

**Role:** Quiet research authority
**Persona ID:** `librarian` (`HUXLEY_PERSONA=librarian`)
**Voice:** sage · English · measured

## What it is

Librarian is a research-oriented persona — precise, authoritative, and unhurried. It retrieves from audiobooks, web search, and news. It attributes sources when it knows them. It never invents facts or fills silences with fabrication.

## Skills

| Skill        | Purpose                         |
| ------------ | ------------------------------- |
| `system`     | Current time, volume control    |
| `news`       | Current events (5 items, cited) |
| `search`     | Web research via DuckDuckGo     |
| `audiobooks` | Read books from a local library |

## Design choices

- **sage voice** — calm and measured; fits a reference authority.
- **Complete sentences** — matches the persona's register. No bullet lists unless asked.
- **Audiobooks enabled** — Librarian is the natural home for a reading collection.
- **No timers / reminders** — out of scope for a research persona; add them if your use case warrants it.

## Running it

```bash
cd server/runtime
HUXLEY_PERSONA=librarian uv run huxley
```

## Audiobook library

Drop `.mp3` / `.m4b` files into `server/personas/librarian/data/audiobooks/`. The framework creates the `data/` directory on first run. Metadata is extracted automatically on first access.

## Customizing

If your users don't need audiobooks, remove the `audiobooks` skill from `persona.yaml`. If they search frequently, add `max_results` to the search config to get more results per query.

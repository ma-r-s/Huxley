# AbuelOS persona

Canonical Huxley persona: a Spanish-language voice assistant targeting an elderly blind user. Defines the voice, language, timezone, system prompt, named constraints, and enabled skills with their config.

- `persona.yaml` — the full spec. See [`../../docs/personas/README.md`](../../docs/personas/README.md) for the schema.
- `data/` — persona-owned data (gitignored). Audiobook library under `data/audiobooks/` and the SQLite DB (`abuelos.db`) live here. Paths in `persona.yaml`'s `skills.*` blocks are resolved relative to this directory.
- Run with `HUXLEY_PERSONA=abuelos uv run --package huxley huxley` from the repo root, or with the default (this is the default persona when `HUXLEY_PERSONA` is unset).

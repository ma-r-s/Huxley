# AbuelOS persona

Canonical Huxley persona: a Spanish-language voice assistant targeting an elderly blind user. Defines the voice, language, timezone, system prompt, named constraints, and enabled skills with their config.

- `persona.yaml` — the full spec. See [`../../docs/personas/README.md`](../../docs/personas/README.md) for the schema.
- `data/` — persona-owned data (gitignored). Audiobook library under `data/audiobooks/` and the SQLite DB (`abuelos.db`) live here. Paths in `persona.yaml`'s `skills.*` blocks are resolved relative to this directory.
- Earcons are loaded from the framework's shared palette at [`../_shared/sounds/`](../_shared/sounds/) (configured via `sounds_path: ../../_shared/sounds` in each skill block of `persona.yaml`). The palette is rendered by [`../../../scripts/synth_sounds.py`](../../../scripts/synth_sounds.py) — original FM/Risset bell synthesis, no third-party samples. To use a per-persona override, set `sounds_path` to a local directory (e.g. `sounds`) and drop your own `<role>.wav` files (PCM16 / 24kHz / mono) there. Architecture: [`../../../docs/sounds.md`](../../../docs/sounds.md).
- Run with `HUXLEY_PERSONA=abuelos uv run --package huxley huxley` from the repo root, or with the default (this is the default persona when `HUXLEY_PERSONA` is unset).

# huxley-skill-system

System utilities for [Huxley](https://github.com/ma-r-s/Huxley). Current time + volume control. **The simplest first-party skill** — a clean template for a stateless utility.

> **Status**: bundled with the Huxley repo as a workspace member.

## What it does

- **`get_time`** — "what time is it" — formats the current time + date in the configured timezone, in the persona's UI language.
- **`set_volume`** — "louder" / "lower the volume" / "set volume to 70" — emits a `PlaySound` side effect with a target-volume hint. The framework's audio plane applies it.

Both tools are stateless; nothing persists.

## Configure

```yaml
skills:
  system:
    timezone: "America/Bogota" # IANA TZ Database name
```

This skill declares the simplest possible `config_schema` — exactly one user-tunable field. v2's PWA renders it as a plain text input with the IANA-zone help text:

```python
config_schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "timezone": {
            "type": "string",
            "title": "Time zone",
            "default": "America/Bogota",
            "x-huxley:help": "IANA Time Zone Database name (e.g. America/Bogota, Europe/Madrid)...",
        }
    },
}
```

If you're writing your first Huxley skill and want a minimum-viable template, `huxley-skill-system` is the smallest example to copy.

## Development

```bash
uv run --package huxley-skill-system pytest server/skills/system/tests
uv run ruff check server/skills/system
uv run mypy server/skills/system/src
```

## License

MIT — see [`LICENSE`](LICENSE).

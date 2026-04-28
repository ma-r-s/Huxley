# Writing a Persona

A persona declares **who your agent is**. It's a YAML file in `server/personas/<name>/persona.yaml`. Huxley loads it at startup, builds the system prompt, registers the listed skills with their config, opens the voice session — and you have an agent.

For the conceptual model, see [`../concepts.md`](../concepts.md). For a full worked example, see [`abuelos.md`](./abuelos.md) — the canonical Spanish-language persona for an elderly blind user.

## Minimal example

```yaml
version: 1
name: My Assistant
voice: alloy
language_code: en
transcription_language: en
timezone: America/New_York
system_prompt: |
  You are a helpful and friendly assistant. Respond in English.
skills:
  system: {}
```

Run Huxley with `HUXLEY_PERSONA=my_assistant` (the directory name under `./personas/`). You have a voice agent that speaks English in the alloy voice and can tell the time / set volume.

## Fields

| Field                    | Required | Description                                                                                                                                               |
| ------------------------ | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `version`                | no       | Schema version (default `1`). The loader fails loudly on mismatch.                                                                                        |
| `name`                   | yes      | The agent's name. Used in logs and (optionally) by the model to refer to itself. Lowercased for the SQLite filename (`<name>.db`).                        |
| `voice`                  | yes      | Voice provider voice ID. For OpenAI Realtime: `alloy`, `coral`, `echo`, `shimmer`, etc. Overridable per-run via `HUXLEY_OPENAI_VOICE`.                    |
| `language_code`          | yes      | ISO 639-1 language code (`es`, `en`, `fr`). Drives the DEFAULT language the persona serves when no client override is selected.                           |
| `transcription_language` | yes      | Whisper language hint for the default language (`es`, `en`, `fr`). Sent in `input_audio_transcription.language` to eliminate auto-detection drift.        |
| `timezone`               | yes      | IANA timezone (`America/Bogota`, `Europe/Madrid`). Consumed by skills that report dates/times.                                                            |
| `system_prompt`          | yes      | Multi-line string. The DEFAULT-language prompt. Write it in `language_code`. Describe tone, register, who's talking.                                      |
| `ui_strings`             | no       | Map of 5 UI status keys (`listening`, `too_short`, `sent`, `responding`, `ready`) → localized labels. Sent to clients for their status line.              |
| `constraints`            | no       | List of named behavioral constraints (see below). Snippets are composed and appended to `system_prompt` at connect time.                                  |
| `i18n`                   | no       | Map of `<lang>` → `{transcription_language?, system_prompt?, ui_strings?}` for additional languages. See [Multilingual personas](#multilingual-personas). |
| `skills`                 | yes      | Map of skill name → config dict. Skills named here are discovered via `huxley.skills` entry points; missing names fail fast at startup.                   |

## Multilingual personas

A persona can serve multiple languages from a single YAML file. The top-level `language_code`, `system_prompt`, `transcription_language`, and `ui_strings` define the **default** language. Additional languages live under an `i18n:` block, keyed by ISO 639-1 code:

```yaml
language_code: es
transcription_language: es
system_prompt: |
  Eres un asistente...
ui_strings:
  listening: "Escuchando…"
  # ...

i18n:
  en:
    transcription_language: en
    system_prompt: |
      You are an assistant...
    ui_strings:
      listening: "Listening…"
      # ...
  fr:
    transcription_language: fr
    system_prompt: |
      Tu es un assistant...
```

Skills that produce user-facing strings accept per-language overrides inside the skill's own config block:

```yaml
skills:
  timers:
    fire_prompt: |
      Ha sonado un temporizador... {message}
    i18n:
      en:
        fire_prompt: |
          A timer has gone off... {message}
      fr:
        fire_prompt: |
          Une minuterie a sonné... {message}
```

At session-connect time the framework calls `PersonaSpec.resolve(<lang>)`, which collapses overrides into a `ResolvedPersona`. Clients select the language by opening the WebSocket with `?lang=<code>` (the web UI's DeviceSheet has a language picker that does this automatically). Unsupported codes fall back to `language_code` silently.

## Constraints

Constraints are reusable behavioral rules that get composed into the system prompt. Add the named constraint to your persona; the framework injects the matching language. Skill authors can also opt their skill into constraint-aware behavior (see [`../skills/README.md#persona-constraints`](../skills/README.md#persona-constraints--what-your-skill-should-respect)).

| Constraint             | Effect                                                                                                                                    |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `never_say_no`         | The agent never returns a bare "no" or "I can't." Every negative response includes an alternative or escalation. Skills must follow suit. |
| `confirm_destructive`  | The agent confirms before any irreversible action (delete, send, transfer).                                                               |
| `child_safe`           | Filters profanity and adult topics from skill outputs.                                                                                    |
| `no_religious_content` | Avoids initiating or engaging deeply with religious topics.                                                                               |

Constraint definitions live in `server/runtime/src/huxley/constraints/`. Adding a new one is a one-file PR.

## Skills

The `skills` field is a map: skill name → config dict. Order matters only for log/dispatch determinism (the LLM picks tools by description).

```yaml
skills:
  audiobooks:
    library: audiobooks
  system: {}
  weather:
    location: "Madrid"
    units: metric
```

The skill name (`audiobooks`, `system`, `weather`) matches the entry-point key registered by `huxley-skill-<name>`'s `pyproject.toml`. The config dict is whatever the skill's docs say it accepts. Relative paths in config values resolve against `server/personas/<name>/data/`.

If a listed skill isn't installed, Huxley fails fast at startup with a clear error pointing you at `pip install huxley-skill-<name>`.

## Where personas live

Personas are first-class citizens of the repo. They live under `server/personas/<name>/` and may include:

```
server/personas/
└── abuelos/
    ├── persona.yaml          # the config
    ├── data/                 # persona-owned data (audiobook library, etc.)
    │   └── audiobooks/
    └── README.md             # optional: notes for whoever maintains this persona
```

Data inside `server/personas/<name>/data/` is referenced by skill configs using paths relative to the persona file. The framework doesn't care what's in there; it's whatever the listed skills need.

## Selecting which persona to run

Set the `HUXLEY_PERSONA` environment variable to the persona's directory name (under `./personas/`):

```bash
HUXLEY_PERSONA=abuelos uv run python -m huxley
```

Unset defaults to `server/personas/abuelos`. Framework fails fast if the directory has no `persona.yaml`.

## Sharing a persona

A persona is a YAML file plus optional data assets. To share one:

1. Make sure every skill listed exists on PyPI (or share the skill packages alongside).
2. Put your `persona.yaml` in a gist / repo / wherever.
3. Anyone who clones it, sets `HUXLEY_PERSONA`, and `pip install`s the listed skills has the same agent.

The persona is the unit of reproducibility. Two people running the same persona with the same skill versions get identical agents.

## Worked example

[`abuelos.md`](./abuelos.md) is the canonical persona — a Spanish-language assistant for an elderly blind user. It's the most complete example of:

- A constraint applied end-to-end (`never_say_no` → matching skill behavior → matching client behavior)
- A non-English persona (Spanish, with regional register)
- Persona-owned data (audiobook library)
- Real-world success criteria

Read it as both a spec and a template.

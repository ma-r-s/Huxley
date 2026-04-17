# Writing a Persona

A persona declares **who your agent is**. It's a YAML file in `personas/<name>/persona.yaml`. Huxley loads it at startup, builds the system prompt, registers the listed skills with their config, opens the voice session — and you have an agent.

For the conceptual model, see [`../concepts.md`](../concepts.md). For a full worked example, see [`abuelos.md`](./abuelos.md) — the canonical Spanish-language persona for an elderly blind user.

## Minimal example

```yaml
name: My Assistant
language: en-US
voice: alloy
personality: |
  You are a helpful and friendly assistant.
skills:
  - system: {}
```

Run Huxley with `HUXLEY_PERSONA=my_assistant`, you have a voice agent that speaks English in the alloy voice and can tell the time / set volume.

## Fields

| Field         | Required | Description                                                                                                                                        |
| ------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`        | yes      | The agent's name. Used in logs and (optionally) by the model to refer to itself.                                                                   |
| `language`    | yes      | BCP-47 language tag (`es-CO`, `en-US`, `pt-BR`). Sets the transcription hint and informs which language tool descriptions are loaded in.           |
| `voice`       | yes      | Voice provider voice ID. For OpenAI Realtime: `alloy`, `echo`, `shimmer`, etc.                                                                     |
| `personality` | yes      | Multi-line string. Goes into the system prompt verbatim. Write it in the persona's language. Describe tone, register, who the agent is talking to. |
| `constraints` | no       | List of named behavioral constraints to layer onto the system prompt. See below.                                                                   |
| `skills`      | yes      | List of skills the agent has access to, each with optional config. Order doesn't matter for tool dispatch (the LLM picks tools by description).    |

## Constraints

Constraints are reusable behavioral rules that get composed into the system prompt. Add the named constraint to your persona; the framework injects the matching language. Skill authors can also opt their skill into constraint-aware behavior (see [`../skills/README.md#persona-constraints`](../skills/README.md#persona-constraints--what-your-skill-should-respect)).

| Constraint             | Effect                                                                                                                                    |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `never_say_no`         | The agent never returns a bare "no" or "I can't." Every negative response includes an alternative or escalation. Skills must follow suit. |
| `confirm_destructive`  | The agent confirms before any irreversible action (delete, send, transfer).                                                               |
| `child_safe`           | Filters profanity and adult topics from skill outputs.                                                                                    |
| `no_religious_content` | Avoids initiating or engaging deeply with religious topics.                                                                               |

Constraint definitions live in `packages/core/src/huxley/constraints/`. Adding a new one is a one-file PR.

## Skills

The `skills` field is a list. Each item is a single-key map: skill name → config dict.

```yaml
skills:
  - audiobooks:
      library: ./data/audiobooks
  - system: {}
  - weather:
      location: "Bogotá, Colombia"
      units: metric
```

The skill name (`audiobooks`, `system`, `weather`) matches the package name (minus the `huxley-skill-` prefix). The config dict is whatever the skill's docs say it accepts.

If a listed skill isn't installed, Huxley fails fast at startup with a clear error pointing you at `pip install huxley-skill-<name>`.

## Where personas live

Personas are first-class citizens of the repo. They live under `personas/<name>/` and may include:

```
personas/
└── abuelos/
    ├── persona.yaml          # the config
    ├── data/                 # persona-owned data (audiobook library, etc.)
    │   └── audiobooks/
    └── README.md             # optional: notes for whoever maintains this persona
```

Data inside `personas/<name>/data/` is referenced by skill configs using paths relative to the persona file. The framework doesn't care what's in there; it's whatever the listed skills need.

## Selecting which persona to run

Set the `HUXLEY_PERSONA` environment variable to the persona's directory name:

```bash
HUXLEY_PERSONA=abuelos uv run python -m huxley
```

If unset, Huxley picks the persona from a default in config (or fails fast if nothing's configured).

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

# Persona: Basic

The proof-of-architecture counterweight to [Abuelo](./abuelos.md). Same
framework, same skills, same Spanish language — totally different
consumption shape because the persona's `system_prompt` says different
things.

## Why this persona exists

Huxley's central architectural claim: **skills are persona-agnostic**. A
skill returns structured data; the persona shapes how the LLM speaks it.

That claim is testable. If the news skill bakes in "warm tone" or "always
plays a chime," you can't write a persona that consumes it tersely without
chimes. Basic is the counter-test: a deliberately plain, terse persona
sharing the same machinery as Abuelo. Anything in `server/skills/*/`
that fights it has leaked persona assumptions where they don't belong.

## Target user (notional)

A developer or general adult user who wants a competent voice assistant.
Sighted, not particularly accessibility-constrained, prefers terse output.

This persona is mostly a development testbed today, not a polished
deployment target. It's what you'd run when you're checking that a new
skill works for "an average person" and not just for Abuelo's specific
audio-only constraints.

## Persona attributes

| Attribute   | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| Voice       | `alloy` (different from Abuelo's `coral` — easier to A/B audibly)    |
| Language    | Spanish                                                               |
| Tono        | directo, sin rodeos                                                   |
| Estilo      | viñetas habladas, frases cortas, cero adornos                         |
| Constraints | only `confirm_destructive` — no `never_say_no`, no `echo_short_input` |
| Chimes      | none — `start_sound` omitted from skill configs                       |

## persona.yaml (the actual file)

Lives at [`server/personas/basicos/persona.yaml`](../../personas/basicos/persona.yaml).
Notable bits versus Abuelo:

```yaml
voice: alloy # vs coral
system_prompt: |
  Asistente personal directo. Sin saludos, sin rodeos, sin títulos.
  Responde con la mínima palabra necesaria.
  ...máximo cinco puntos. Cada uno una sola frase.

constraints:
  - confirm_destructive # not never_say_no — fine to say "no sé"

skills:
  news:
    location: "Villavicencio" # same data as Abuelo
    interests: [] # vs [politica, local] — no weighting
    max_items: 5 # vs 8 — terser
    # no start_sound — no chime, no PlaySound side effect ever fires
  system: {} # no audiobooks — different use case
```

## Same skill, totally different audio

Both personas, asking _"qué hay de noticias"_:

| Step            | Abuelo                                          | Basic                                |
| --------------- | ------------------------------------------------ | -------------------------------------- |
| Pre-narration   | _"a ver, le cuento las noticias"_                | _"un momento"_                         |
| Chime           | `news_start.wav` plays (~1.4s)                   | _none_                                 |
| Narration style | "El clima en Villavicencio: 28 grados, soleado…" | "Clima: 28°C soleado, lluvia tarde."   |
| Length          | ~60–90 seconds                                   | ~15–25 seconds                         |
| Items covered   | Up to 8, narrative                               | Up to 5, bullet-style                  |
| Closing         | _"¿quiere que le cuente más de algo?"_           | (period — terse persona, no follow-up) |

Same `get_news` tool. Same JSON byte-for-byte. The audio diverges entirely
because of the persona's `system_prompt` + the absence of `start_sound`.

## Run Basic

```bash
cd server/runtime
HUXLEY_PERSONA=basicos HUXLEY_SERVER_PORT=8766 uv run huxley
```

Or run both servers side-by-side and switch with the [web UI persona
dropdown](../../web/.env.local.example):

```bash
# Terminal 1 — Abuelo on default port
cd server/runtime && uv run huxley

# Terminal 2 — Basic on port 8766
cd server/runtime && HUXLEY_PERSONA=basicos HUXLEY_SERVER_PORT=8766 uv run huxley

# Terminal 3 — web client; the dropdown reads VITE_HUXLEY_PERSONAS
cd clients/pwa && cp .env.local.example .env.local && bun dev
```

Open `http://localhost:5173` — the header shows a `personas:` dropdown
that switches the WebSocket connection cleanly (closes the old socket,
opens the new one, no auto-reconnect to the old URL during the switch).

## Skills not enabled

- **audiobooks** — distinct use case; Basic doesn't manage an audiobook
  library. Re-enable per-deployment if you want it.
- Future skills (messaging, music, reminders) will be enable-by-default for
  the persona that needs them; Basic opts in only when the use case fits
  its tone.

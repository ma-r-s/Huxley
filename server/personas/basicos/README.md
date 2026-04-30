# Basic persona

A plain, terse Spanish-speaking assistant. Built as the proof-of-architecture
counterweight to Abuelo — same skills, totally different consumption.

The point: if a skill in `packages/skills/*/` is genuinely persona-agnostic,
it should serve both Abuelo (slow, warm, audio-only, every gap padded with
cushioning) AND Basic (terse, direct, no chimes, no over-explanation)
without code changes. The differences live entirely in `persona.yaml`.

- `persona.yaml` — the spec. Same Spanish language as Abuelo but a much
  more direct `system_prompt`, no `never_say_no` constraint, no chimes.
- No `data/` directory yet — Basic doesn't enable the audiobooks skill,
  so there's no library to manage.
- No `sounds/` directory — no earcons by design.

## Run

```bash
HUXLEY_PERSONA=basicos HUXLEY_SERVER_PORT=8766 uv run --package huxley huxley
```

The web client (`web/`) defaults to `ws://localhost:8765` (Abuelo). To talk
to Basic, either change the WS URL in the web client or use the dropdown
once stage E lands. Both servers can run side-by-side on different ports.

## Why two personas

The dropdown lets you A/B the same input across the two personas:

- "qué hay de noticias" on Abuelo → ~60s narrated digest, chime, "¿quiere
  más de algo?"
- "noticias" on Basic → ~15s, five terse bullets, no chime, no follow-up
  question

Same skill, same JSON, same backend round-trip — different audio because the
persona shapes how the LLM speaks.

If something in `packages/skills/news/` ever assumes "warm tone" or "always
plays a chime," Basic will surface it. That's the test.

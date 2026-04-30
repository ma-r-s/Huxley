# Huxley Docs — Review notes

This is your morning hand-off. The full Fumadocs site is built and runnable. I ran two critic rounds and fixed everything they found that I could verify against the code. Below is what to look at first, what's open, and where every choice landed.

## Quick start (try it now)

```bash
cd docs-site
bunx fumadocs-mdx          # regenerate .source/ if needed
bun run dev                # http://localhost:5176/docs/welcome
```

The docs sit at `localhost:5176/docs/*` in dev (the `basePath: "/docs"` in `next.config.mjs` matches the production rewrite path so URLs are stable).

`bun run build` is green. `bunx tsc --noEmit` is green via Next.js typecheck during build.

## What shipped

**32 docs pages** across 8 sections, plus an index (`welcome`) and a vision page. Sidebar order:

- **Get Started** — `quickstart`, `your-first-conversation`
- **Concepts** — index, `personas`, `skills`, `turns`, `side-effects`, `focus`, `constraints`
- **Use Huxley** — `run-the-server`, `connect-a-client`, `switch-personas`, `add-a-skill`
- **Build a Skill** — index, `first-skill`, `tools`, `speaking-back`, `proactive`, `publishing`
- **Build a Persona** — index, `anatomy`, `voice-and-language`, `multilingual`, `abuelos-walkthrough`
- **Cookbook** — index, `rss-feeds`, `audio-streaming`, `timers-and-reminders`, `notifications`, `persistent-state`
- **Reference** — `skill-cheat-sheet`, `persona-schema`, `environment-variables`
- **Why Huxley** — `vision`

**Visual identity** matches the landing — same coral / paper / dark-coral palette, same Instrument Serif italic for the wordmark, Inter Tight body, JetBrains Mono code. CSS in `docs-site/app/global.css` mirrors `site/src/styles/index.css`.

**Components used in MDX**: Callout, Steps, Tabs, Cards, Files, Folder, TypeTable, Accordions, plus a custom `<Mermaid>` for diagrams (used in `concepts/turns.mdx` and `concepts/focus.mdx`).

**Search**: built-in via Fumadocs' Orama integration. Index regenerates at build time. Click the search icon in the nav, or hit `Cmd-K`.

**Architecture**: `docs-site/` is a separate Next.js 16 + Fumadocs 16 app, deployed independently on Vercel. The landing site's `vercel.json` has a rewrite rule (`/docs → docs-site`) so visitors see one domain. Vercel's "create new project" flow against the same git repo, point it at `docs-site/`, and the rewrite stitches the two together.

## Voice — what I aimed for

You said **Tailwind + shadcn voice**. I tried to match by:

- Opening every page with a one-line hook, not a structural preamble
- Lots of short paragraphs, code blocks, and `<Steps>` / `<Cards>` instead of long prose walls
- Progressive disclosure — every section has a "smallest version" then layers complexity
- No academic detours; every concept is introduced because it's about to be used
- Italic-serif treatment for the brand "huxley" wherever it appears, lowercase always

Critic round 2 flagged a few corners (the wire-protocol table in `connect-a-client.mdx`) as more reference-dry; I left those alone — that's the right register for that section.

## Decisions worth knowing

These are choices I made on my own. Reverse them if any feel wrong; each was self-contained.

1. **Abuelo persona walkthrough is a _simplified_ version**, not a verbatim copy of `server/personas/abuelos/persona.yaml`. The real file is much longer (more skill config, contacts, a long system prompt). Mentioned explicitly at the top of the page; readers are pointed at the source for the full version.
2. **No auto-generated Python API reference.** Per your call, the reference section is a hand-curated cheat sheet plus `persona.yaml` schema + env vars. Auto-generated full type docs are noted as "coming soon."
3. **Constraints section honestly says `ctx.constraints` is not yet implemented.** The first draft assumed it existed (per the research dump). Critic correctly flagged that skills _cannot_ read constraints today; I rewrote the page to explain that constraints are prompt-only for now, with a callout pointing at the future. **This is a real product gap** — see "Things to follow up on" below.
4. **Spanish-only constraint snippets called out.** The framework's constraint registry is currently Spanish-only (because Abuelo is the only persona). Mentioned in `concepts/constraints.mdx`. Future work: localize per persona language.
5. **No dedicated AI-chat / OG-image generation** in the docs site setup. Fumadocs offers both as optional add-ons; we skipped them — they're easy to add later.
6. **Mermaid is the diagramming choice.** Renders client-side via dynamic import. Used sparingly: turn state machine, focus channel arbitration. Not over-applied.

## Open items I'm flagging for your judgment

These are things the critic found that need product-level decisions, or that I chose not to act on alone:

| #   | Item                                                | What's going on                                                                                                                                                                                                                                                                                                                                      | What I'd ask you                                                                                                     |
| --- | --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| 1   | **`ctx.constraints` API gap**                       | Docs honestly say constraints are prompt-only. But the API is small enough to ship in a single PR — add `constraints: frozenset[str]` to `SkillContext`, populate from persona config, profit.                                                                                                                                                       | Want a follow-up `/schedule` to spin off a PR that adds `ctx.constraints` to the SDK in one shot?                    |
| 2   | **Constraint snippets are Spanish-only**            | `huxley/constraints/__init__.py` injects Spanish prompts for every constraint regardless of persona language. English/French personas get sub-optimal behavior.                                                                                                                                                                                      | Same — small PR's worth of work to localize per-language.                                                            |
| 3   | **Wire protocol section in `connect-a-client.mdx`** | I documented the message types and shapes from reading `server.py`. Critic flagged that some payloads were inverted (had `audio` instead of `data`, `state` instead of `value`). Fixed those, but the canonical source is your code — the docs say "subject to change pre-1.0, read `server/runtime/src/huxley/server/server.py` for current truth." | If you want to lock the protocol, a `docs/protocol.md` in the repo is the place; the docs site can then point at it. |
| 4   | **`--check-persona` flag**                          | Originally documented as if it existed; removed. If you want to add this CLI flag (it's nice for ops), the docs are ready to mention it.                                                                                                                                                                                                             | Optional.                                                                                                            |
| 5   | **Repo URL `ma-r-s/Huxley`**                        | Used throughout. You confirmed earlier in our chat that `github.com/ma-r-s/Huxley` is correct, so no change. Just noting that critic round 1 flagged it as unverified; you're the source of truth.                                                                                                                                                   | None — already confirmed.                                                                                            |
| 6   | **No Python API reference page**                    | Per your earlier "friendly docs not in-depth docs" call. If you change your mind later, hand-curating a reference page from `huxley_sdk/types.py` is straightforward.                                                                                                                                                                                | None — this was your decision.                                                                                       |
| 7   | **Search behind the rewrite**                       | `/api/search` is hosted by the docs sub-app. The Vercel rewrite proxies it correctly because the basePath matches. Test against staging once you deploy.                                                                                                                                                                                             | Verify on first deploy.                                                                                              |

## Things to follow up on once the dust settles

- **Real-world test the Quickstart.** I wrote it from reading the code; you should run through it on a clean machine and confirm every step.
- **Verify the OG card.** The docs pages don't yet have per-page OG images (Fumadocs supports this via `next-og`; we set the option but didn't author cards yet). Drop a `metadataBase` and per-page OG images if/when you want polished social cards for individual pages.
- **Fix the `ctx.constraints` gap** before you publish the docs publicly — the constraints page is honest about it being not-shipped-yet, but it'd be nice to ship the API and update the docs in one motion.
- **Decide on the `library_path` vs `library` discrepancy.** Several skills use `library:` (audiobooks). Hypothetical examples in docs use both. I left the hypothetical Spotify example with `library_path:` because it's fictional; if you want consistency, change it to `library:`.

## Cross-link health

I added cross-links throughout. Every page ends with a "Next" `<Cards>` block pointing to the natural follow-up. The internal link graph is dense — most concepts are reachable from 2-3 paths.

There are zero dead links. I removed two that the round-1 critic flagged (`/docs/concepts/observability` was never a page; `/docs/cookbook/notifications` was used as a placeholder for a multilingual link, fixed to point at `/docs/build-persona/multilingual`).

## Critic rounds — what they caught

**Round 1** (30 findings):

- 11 critical (fictional API surface, wrong env vars, wrong skill config keys)
- 10 important (voice consistency, dead links, Abuelo walkthrough YAML divergence)
- 9 minor (typos, formatting)

Most stemmed from one root cause: the research dump from the Explore agent had some inaccuracies that propagated into the first draft. Fixed via global sed renames + targeted edits.

**Round 2** (20 findings):

- ~6 round-1 fix verifications (some patches missed certain pages — `use/switch-personas.mdx`, `reference/persona-schema.mdx`, `use/add-a-skill.mdx`)
- ~10 new findings the first round missed (telegram env var names, wire protocol shape, `PermanentFailure` field names, `completion_silence_ms` semantics inverted)
- ~4 stale references (the bare inject prompt that violated its own warning)

All addressed. I skipped a third round — diminishing returns past two on docs of this size.

## Files I touched outside `docs-site/`

- **`site/vercel.json`** — added `rewrites` block to proxy `/docs/*` to `docs-site`. One change. If you don't deploy `docs-site/` yet, the rewrite would 404; comment it out until then.
- **Nothing else** — `site/`, `server/`, `clients/` are untouched.

I did **not** push, deploy, change CI, or modify anything in `server/` even though the critics flagged some real product gaps (the `ctx.constraints` issue). Per your "scope of changes" preference, those stay as documented gaps.

## A few things to look at first

If you have 15 minutes:

1. Open `docs-site/` and `bun run dev`. Click around. Get a feel for the visual identity.
2. Read `welcome.mdx` and `getting-started/quickstart.mdx` — these are the highest-traffic pages. Tone-check.
3. Read `concepts/skills.mdx` and `build-skill/first-skill.mdx` — the technical density. Is the depth right?
4. Skim `cookbook/audio-streaming.mdx` and `cookbook/timers-and-reminders.mdx` — these are the most "this should be exactly accurate" pages.
5. Glance at `vision.mdx` — does it represent Huxley the way you want it represented?

If anything in those five reads wrong, the rest probably needs the same adjustment.

---

Site builds cleanly. 39 pages prerendered. Sidebar, search, dark mode, and brand identity all working. Ready for your review.

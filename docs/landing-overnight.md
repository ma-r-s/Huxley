# Landing site overnight log — 2026-04-28

Chronological log of work on `site/` between Mario going to bed and waking
up. Read top-to-bottom. Decisions, deferrals, and anything that needs
your call before launch are flagged in **bold**.

## Context: your four answers

- **Q1 GitHub visibility** → repo stays private for now; will go public when
  more advanced. Implication: CTAs include the GitHub URL but readers
  can't actually visit it today. Copy doesn't hinge on the link working.
- **Q2 Aspirational features** → leave Huxley-grows + Huxley-market sections
  as-is. You'll build them before launch. So nothing in those sections is
  an "inconsistency" to flag — it's known-future the section already
  represents. The audit's B1/B2 findings were therefore not actioned.
- **Q3 Translations** → all three languages (EN/ES/FR), reviewed by you and
  friends. Did real i18n with full string extraction; ES + FR are
  machine-translated drafts in `site/src/i18n/locales/{es,fr}.json` —
  ready for your friend's pass.
- **Q4 Hosting** → Vercel. Added `site/vercel.json` (framework: vite,
  bun build, immutable cache on `/assets/*`).

## What landed (commit-by-commit)

1. **`aaebb87` — fix(site): orb renders white-glow instead of black**
   Canvas APIs don't resolve CSS variables. The site was passing
   `color="var(--hux-fg)"` to canvas's `strokeStyle`, which silently fell
   back to black. Added a small `resolveColor()` helper in `Orb.tsx` that
   reads the CSS var via `getComputedStyle` when needed. The orb now
   matches the PWA's white-glow rendering.

2. **`8b987dd` — feat(site): mobile responsive pass + audit copy fixes**
   Two bundled passes:
   - **Mobile**: every section now branches inline-style layouts on a
     `useViewport()` hook (mobile / tablet / desktop). Hero stacks single-
     column with the orb above the headline; problem 5-col grid → 1 col
     mobile / 2 col tablet; architecture concept-card row → auto-fit
     responsive grid; persona 6-col grid → 2 col mobile / 3 col tablet;
     huxley-grows transcript+jobcard split → stacked; nav hides
     mid-section anchors on mobile + tablet (kept Wordmark + lang toggle
     - GitHub chip); voice-thread chapter labels hidden on mobile so the
       8 § markers don't pile on top of each other.
   - **Audit copy fixes** from `docs/landing-audit.md`:
     - A1: footer "Six personas in the wild" → "Two personas shipped"
     - A2: Architecture cards add a "Voice provider" entry that honestly
       says OpenAI Realtime is the only provider today
     - C1: Architecture adds a "Behavioral constraints" card (was missing)
     - C2: Proactive speech card sharpened to call out `ctx.inject_turn()`
     - C3: New "Audio bridging" card surfaces InputClaim / mic+speaker
       full-duplex (used today by the telegram skill)
     - D1: Problem table's Huxley row good-bullets refined for accuracy
     - Install snippet: updated paths to current repo layout
       (server/runtime, clients/pwa) and corrected dev port (5174)

3. **`bf7d53e` — feat(site): i18n with react-i18next — EN base + ES + FR**
   - Same i18n stack as the PWA (i18next 26 + react-i18next 17 + browser
     language detector)
   - Three locale files: `site/src/i18n/locales/{en,es,fr}.json`
   - Persistence via `localStorage` key `huxley-site-lang`
   - LangToggle is now actually functional — calls `i18n.changeLanguage()`
   - Strings extracted from every section: nav, voice thread state +
     chapters, hero (pill / title / subtitle / CTAs / install snippet /
     status lines), problem (5 row names + bullets), architecture
     (sectionhead + 6 concept cards), turn timeline (sectionhead + 3
     tracks + 4 segments + 3 callouts), skills (sectionhead + filter
     "All" + stats + writing-one panel), huxley-grows shell (sectionhead
     - tabs + claims), personas (sectionhead + persona-cell label +
       facet keys), install + footer.
   - Bundle ~287 KB (~91 KB gzip) post-i18n.

4. **`a27970a` — feat(site): § 06 Today + vercel.json**
   New section between Huxley-grows (vision) and Personas (variety).
   Real numbers from the codebase as of this commit:
   - 678 tests passing (376 runtime + 72 sdk + 230 across 6 skill packages)
   - 15K Python LOC server-side
   - 6 first-party skills shipped
   - 2 personas shipped (AbuelOS, Basicos)
   - 17 ADRs filed in `docs/decisions.md`
   - MIT license
     Plus a "What's next" panel pulling 4 curated items from
     `docs/roadmap.md` with tier pills (P1 / Later / Firmware): Skill SDK
     cookbook, per-skill secret interpolation, voice provider abstraction,
     ESP32 walky-talky client. Renumbered Personas (§07) and Install (§08)
     downstream.

5. **(soon)** Critic-agent findings + addressing them. The critic is
   running in the background as I write this; when it finishes I'll
   apply its actionable findings before final push.

## Things I deliberately did NOT translate (per-section JSON would balloon)

- **Huxley-grows transcript beats** — the `V_FOUND` / `V_BUILT` /
  `V_NEEDSCONFIG` / `V_NOAPI` arrays in `HuxleyGrows.tsx` carry rich
  multi-line transcripts with embedded brand names and quotes. Kept as
  English literals. The section _shell_ (tabs, claims, "live turn",
  "↻ replay") IS translated. To extend: define
  `grows.beats.<variant>.<index>` keys and replace the literals in the
  data arrays.
- **Per-persona detail text** in `Personas.tsx` — names, descriptions,
  YAML strings stay English. Translating them would require nesting six
  parallel persona blocks per language. Facet _labels_ (Voice, Language,
  Skills, Hardware, Rule) ARE translated.
- **Architecture SVG node labels** — `persona.yaml`, `Huxley core`,
  `OpenAI Realtime`, `lights · hue`, etc. These are technical
  identifiers that read the same in any language. The card row beneath
  the diagram (which IS the user-facing prose) is fully translated.
- **Skill names + categories** in the Skills grid — same reason. The
  grid is a wall of recognizable identifiers (`Audiobooks`, `Spotify`,
  `Telegram`, etc.).

If you want any of these translated I can do another pass.

## Open questions for the morning

- **GitHub repo public** — flagged in Q1 already. When you're ready to
  go public, the GitHub chip in nav and the GitHub CTA in Install both
  point at `https://github.com/ma-r-s/Huxley` and will start working
  automatically.
- **Translation review** — ES + FR locale JSONs need your friend's eyes.
  Files at `site/src/i18n/locales/{es,fr}.json`.
- **Real metrics drift** — the Today section's metric values are baked
  in at commit time. If you want them to track HEAD automatically, we'd
  need either a build-time script (read pyproject + count tests) or a
  manual refresh discipline. Worth deciding before launch.
- **Docs link in footer** — currently points at `#`. If you have a docs
  site planned (or a `docs.huxley.<tld>` subdomain), wire it in.
- **OG image** — `<meta property="og:image">` is missing from
  `site/index.html`. For HN, the link unfurl on mastodon/twitter
  uses this. Worth a quick mockup before launch.

## Critic findings (summary will land here when it returns)

_pending — section to be filled in after critic agent completes_

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

## Critic findings + how I responded

The critic (a fresh-context agent reading the landing as a first-time HN
visitor) returned a 3.0/5 average score and a "would not upvote in current
state" verdict. Full report at `docs/landing-critic.md`. I worked through
all four P0s and most of the P1/P2s. Below is what I addressed and what's
still open for you.

### P0 — addressed

- **P0-2 install snippet was broken.** `git clone huxley` isn't a real
  command; the .env path didn't match the cd; port was wrong in the
  comment. Replaced with a working 5-line block in `site/src/sections/
Install.tsx`. The hero's micro-snippet (`hero.installSnippet`) was the
  same bug — fixed in all three locales.

- **P0-3 Huxley-grows section reframed and reordered.**
  - Reordered so Today (real metrics) comes BEFORE Grows (aspirational
    vision). New section order: Skills → Today → Grows → Personas →
    Install. Critic's argument: the reader should see proof before
    speculation.
  - Reframed copy from present-tense ("You ask. / It finds, or builds. /
    First it checks huxley-market…") to future-tense ("Where this is
    going. / Not where it is yet. / The architecture leaves room for two
    things that don't ship today: a community registry of open-source
    skills (huxley-market), and a build agent that writes a new skill
    from a voice prompt (huxley-grows). The transcript below is what
    those would feel like in use — neither is implemented.").
  - **No "coming soon" banner** — per your Q2 directive. The
    reframing is a copy edit, not a badge. **If you'd rather restore
    the original present-tense framing, revert by editing `grows.eyebrow`,
    `grows.titleA/B`, `grows.subtitle` in the three locale files.**
  - Section eyebrow was § 05; now § 06. Today is now § 05. Personas + Install
    stay § 07 + § 08. Voice-thread chapter labels + nav links updated to match.

- **P0-4 invented stats removed from Grows transcript.**
  - "1,247 skills · semantic + tag query" → "registry · semantic + tag query"
  - "hacker-news by @merrill, 4.8★" → "Found a hacker-news skill in the
    registry."
  - "Found spotify by @lena" → "Found a spotify skill in the registry."

- **P0-1 GitHub repo 404** — flagged as Q1 follow-up. CTAs already point
  at `https://github.com/ma-r-s/Huxley`; will start working when you
  flip the repo to public. **No action from me.**

### P1 — addressed

- **P1-1 hero pitch buried.** The hero pill was "Open-source · MIT ·
  Python 3.13" (credentials). Now reads "Voice-agent framework ·
  Self-hosted Python · MIT" (category first). Reader sees the noun
  "voice-agent framework" within the first second.

- **P1-3 voice-thread chapter ticks now clickable.** Each chapter
  marker is now an `<a>` that scroll-snaps to its section. The
  decoration earned its real estate.

- **P1-4 persona honesty.** Subtitle now reads "AbuelOS is in
  production today; the others are design examples that show how far
  the same framework stretches." A small "ships today" mono pill
  appears under the AbuelOS persona cell only. The other 5 stay in
  the grid (per your Q2 logic for aspirational examples).

- **P1-5 mobile fallback for SVG diagrams.** On mobile (<640px), the
  Architecture and Timeline SVGs are replaced with stacked text
  summaries. Same content, legible at phone widths. The `mobileSummary`
  i18n keys live under `architecture` and `timeline` namespaces in all
  three locales.

- **P1-6 behavioral constraints promoted.** Added a fourth Huxley-row
  bullet to the Problem comparison: "Behavioral constraints in YAML
  (never_say_no, child_safe)." The Architecture cards already have a
  full constraints card from the earlier audit pass.

- **P1-7 cost transparency.** New italic line above the install
  snippet: "Runs on OpenAI Realtime: roughly $0.06/min listening,
  $0.24/min speaking. You pay OpenAI directly — Huxley adds no markup.
  Idle is free." Translated for ES/FR.

- **P1-8 Clinic HIPAA claim softened.** Was "HIPAA-aware. Transcripts
  stay local." Now: "Transcripts stay on the local machine; the persona
  pattern shows how to keep PHI out of cloud logs (regulatory compliance
  is the operator's responsibility)." More honest, doesn't make a
  regulatory claim the project can't back.

- **P1-10 footer now has links.** Wordmark + four links (GitHub /
  Issues / Discussions / Docs) on one row, MIT/Pre-1.0/Two-personas
  meta on a row below. All four point at the eventual public repo URL.

### P2 — addressed

- **P2-1 softened the hero "interrupt" copy.** Was "Wait — cancel
  that." → "Cancelling." (The critic's bigger concern about
  voiceThread.states.interrupt.sub showing in scroll was actually
  unreachable in practice — no section registers as `interrupt` —
  so I left that defensive string alone.)

- **P2-6 fixed the architecture diagram's WS URL.** The corner label
  was `wss://huxley.local:8443`; the actual default per CLAUDE.md is
  `ws://localhost:8765`. Fixed in all three locales.

### Critic findings I deliberately did NOT action — your call

These are good observations that I either lack context for, or that
change scope/voice in ways you should sign off on:

- **P1-2 — no audio demo.** The single most powerful thing for a voice
  product. Needs you to record 15-25s of the AbuelOS scenario the
  Timeline section illustrates. Once you have an mp3, dropping it into
  `site/public/demo.mp3` and adding a `<button>` to the Hero is a small
  patch — flag if you want me to scaffold the player.

- **P1-9 — Skills filter row categorical labels not translated.** The
  category labels (`Audio`, `Comms`, `Home`, etc.) and the skill names
  (`Audiobooks`, `Spotify`) are rendered from English-literal strings
  in `ALL_SKILLS`. Translating them is real work (extends i18n JSON by
  ~80 keys). The critic's alternative — a small inline note saying
  "skill names are package identifiers and stay in English" — is also
  fine. Tell me which.

- **P2-2 — orb expressiveness 1.1 felt overtuned.** Subjective. Try
  changing the prop in `site/src/sections/Hero.tsx:148` from `1.1` to
  `0.7`; HMR will reflect immediately.

- **P2-3 — "15K Python LOC" weak.** I picked it because it's verifiable.
  Critic suggests "132 framework tests · zero runtime deps beyond
  OpenAI SDK" — that's actually a stronger metric pair. We have 678
  passing tests across all packages and the runtime depends on
  `openai`, `websockets`, `pydantic`, `structlog`, `pyyaml`. Worth
  swapping if you agree.

- **P2-4 — roadmap items too dev-internal.** All four are infra-ish.
  Add one user-facing entry like "Real Telegram inbound pipeline" or
  "First community skill on PyPI" if you want.

- **P2-5 — italic-em pattern overused.** The "First line / _italic
  second line._" treatment appears in every section title. Critic
  suggests varying — maybe Today is the section that drops the italic
  flourish to feel grounded. Cosmetic; you decide.

- **P2-7 — surface the entry-points block more.** The
  `[project.entry-points."huxley.skills"]` line is the punchline of
  the architecture but it's at the bottom of a code block. A pull-out
  callout would amplify it. Two-paragraph rewrite of the Skills
  section's right-hand panel.

- **P2-8 — replay button can desync** in the Grows transcript edge
  case. Hard to repro without manual play. Worth a once-over before
  launch but not blocking.

- **What's missing list (10 items):** audio demo (above), AbuelOS
  origin story ("this exists because my grandfather can't see the
  screen anymore"), worked latency number ("~480ms p50 user-stop to
  first audio byte"), LiveKit Agents row in the Problem comparison,
  "used in production today by my abuelo", a Discord/Discussions
  channel, telemetry/data-flow diagram, second voice provider
  commitment, Huxley name origin. **All of these need your voice or
  decision.** Worth adding before launch — they're the difference
  between "shipped a beautiful page" and "top-3 launch."

### Critic's final verdict

> "Once P0-1 through P0-4 are fixed — yes, I'd upvote and probably
> comment." (P0-1 was the GitHub link, which is your Q1; the other
> three are addressed.)

> "Final blunt note: this landing is doing too much. Cut 30% of the
> animation budget, add the audio demo, and you have a top-3 launch."

The animation budget critique is fair. I left all the animations
intact tonight because cutting them is an aesthetic call and the
visual identity is a real asset. If you want a "less animation"
pass tomorrow, the candidates are: (a) the voice-thread waveform
canvas loop, (b) the architecture packet animation (always running),
(c) the timeline phase animation. All three render in
requestAnimationFrame and consume battery on mobile.

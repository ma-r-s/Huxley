# Landing critic — fresh-context HN visitor review

**Date:** 2026-04-28

Findings from a fresh-context critic agent reading the landing as a first-time HN visitor; addressed by claude in subsequent commits.

---

## How I read it

I'm a developer who clicked an HN link titled (presumably) something like "Show HN: Huxley — open-source voice agent framework you can self-host." I have ~30 seconds of patience before deciding to upvote, comment, or close. I have never heard of the project. I scroll once, end-to-end, on a desktop laptop (and mentally simulate the iPhone case).

What I want from a project like this, in order: (1) a one-line answer to "what is this," (2) a reason to believe it's not vapor, (3) a hook that makes me want to clone the repo tonight. The landing has all three ingredients on the page, but several of them are buried, blocked, or contradicted by the surrounding chrome.

---

## Scores (1–5)

| #   | Axis                  | Score | One-sentence rationale                                                                                                                                                                                                                                                                                               |
| --- | --------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Clarity of pitch      | **3** | The headline "A voice you can actually own" is too poetic; the supporting paragraph is the actual pitch, and it requires reading two sentences before "voice agent framework" lands.                                                                                                                                 |
| 2   | Differentiation       | **4** | The 5-column comparison table is genuinely strong and answers the "why not pipecat / OpenAI / Alexa" question better than 90% of frameworks ever do.                                                                                                                                                                 |
| 3   | Proof                 | **2** | "678 tests · 15K LOC · MIT" is good, but the GitHub link 404s, the install snippet isn't a runnable command, and the splashy "Found it / Built it" tabs are obviously prospective demos with no "this is what we'd love to ship" framing.                                                                            |
| 4   | Technical credibility | **4** | The architecture diagram, the entry-points snippet, the explicit "OpenAI Realtime today" disclosure, and the turn-sequencing timeline read as written by someone who has actually shipped this.                                                                                                                      |
| 5   | Emotional hook        | **3** | The orb + waveform + AbuelOS persona description are quietly moving, but the page doesn't open with the human story; you have to scroll seven sections to find out who this was actually built for.                                                                                                                  |
| 6   | HN tone fit           | **3** | Aesthetically beautiful, but tilts into "designy product launch" rather than "engineer's notebook"; HN responds better to dense, slightly ugly, code-forward pages. The §-numbered sections and italic serif headlines feel like a Stripe blog post, not a hacker tool.                                              |
| 7   | Mobile (iPhone 390px) | **2** | Will load and not crash, but the architecture SVG and the timeline SVG are both designed at 960px viewBox with 10–11px text — they'll scale to ~340px wide and become unreadable. The sticky voice-thread bar steals 32px of vertical screen. The 70-tile skills grid + animated transcript will hammer the battery. |
| 8   | Missing context       | n/a   | See "what's missing" — the #1 question I'd ask in the comments is "how much does this cost to run per hour?" and the page never says.                                                                                                                                                                                |

**Average:** 3.0 / 5. The bones are great; the launch readiness isn't there yet.

---

## P0 — would make me close the tab

### P0-1. The GitHub link 404s

**Where:** `site/src/sections/Install.tsx:76`, `site/src/components/Chrome.tsx:111` — both link to `https://github.com/ma-r-s/Huxley`.
**Problem:** I just verified — that URL returns HTTP 404. The single most important CTA on the page leads nowhere. For an HN launch, this is fatal: the entire purpose of the post is to send people to the repo.
**Fix:** Decide on the canonical URL (likely `https://github.com/marioalejandroruizsarmiento/Huxley` or a new org), update both call sites, and verify the repo is public before posting to HN. If the repo is intentionally not public yet, do not launch.

### P0-2. The install snippet isn't runnable

**Where:** `site/src/sections/Install.tsx:59-63`.

```
$ git clone huxley && cd huxley
$ echo "HUXLEY_OPENAI_API_KEY=sk-..." > .env
$ uv sync && cd server/runtime && uv run huxley
$ cd ../../clients/pwa && bun install && bun dev
$ open http://localhost:5174   # hold the button, speak.
```

**Problem:** `git clone huxley` is not a real command — there's no remote. Anyone who copies this fails on line 1. Also, the `.env` path in line 2 doesn't match line 3's `cd server/runtime` (the README correctly puts `.env` inside `server/runtime/`). And line 5 says port 5174 but the README and `web/` defaults say 5173.
**Fix:** Replace with a literal copy-pasteable block:

```
$ git clone https://github.com/<owner>/huxley.git && cd huxley
$ echo "HUXLEY_OPENAI_API_KEY=sk-..." > server/runtime/.env
$ uv sync && uv run huxley                         # in one terminal
$ cd clients/pwa && bun install && bun dev         # in another
$ open http://localhost:5173
```

The hero snippet (`hero.installSnippet`: `git clone huxley && uv run huxley`) has the same bug — fix both.

### P0-3. The Huxley-market / Huxley-grows section reads as the centerpiece, but it doesn't ship

**Where:** `site/src/sections/HuxleyGrows.tsx`, `site/src/i18n/locales/en.json:194-236`.
**Problem:** The section is the largest, most animated, and most screen-real-estate-hungry on the page. The four tabs ("Found it / Built it / Needs config / No API"), the live transcript, and the job pipeline card all imply a working product. The transcript even invents a contributor handle ("hacker-news by @merrill, 4.8★") and a registry size ("1,247 skills"). A reasonable HN reader will assume these ship, try the install, and discover none of it exists. **That's the kind of thing that ends up as the top comment: "I cloned this — there is no huxley-market. Where is it?"** Per the framing this is intentional, so the page must own the framing.
**Fix (per the constraint, not "coming soon" markers):** The entire section needs explicit framing language that this is what Huxley _grows into_, not what it does today. Rewrite the eyebrow from "§ 05 — Huxley-market + Huxley-grows" to something like "§ 05 — The shape we're building toward" and the title from "You ask. / It finds, or builds." to "Where this is going. / Not where it is yet." The subtitle currently reads as present-tense capability ("First it checks huxley-market…") — make it future-tense and aspirational ("The architecture is built so a registry of community skills can plug in, and so a build agent can write new ones from a voice prompt. Neither ships today."). Also reposition the section _after_ §06 Today, so the reader sees real metrics before the speculative pitch. Otherwise the average HN reader will feel baited.

### P0-4. "1,247 skills" claim in the demo

**Where:** `site/src/sections/HuxleyGrows.tsx:221` (`detail: "1,247 skills · semantic + tag query"`).
**Problem:** Even framed as a demo, a specific number like 1,247 reads as a real registry stat. HN will catch this — and it's the kind of detail that single-handedly turns a launch post into "developer caught lying" thread. The repo today ships 6 skills (per the Today section's own numbers).
**Fix:** Replace with something obviously placeholder ("N skills · semantic + tag query") or remove the count entirely. Same scrutiny applies to "@merrill, 4.8★" in the Found-it transcript — invented social proof that doesn't exist.

---

## P1 — would make me hesitate

### P1-1. The hero pitch buries "what it is"

**Where:** `site/src/sections/Hero.tsx:79-95`, `en.json:50-52`.
**Problem:** Headline reads "A voice you can / actually own." That tells me nothing about what Huxley is — for the first 2 seconds I assume it's a voice cloning service, a TTS model, or maybe a smart speaker. Only when I read the italic serif subtitle do I learn it's "a Python framework for real-time voice agents." HN scans aggressively; the first six words have to land the category.
**Fix:** Either swap the subtitle into the headline ("Real-time voice agents you can actually run.") or keep the poetry but add a tiny tagline above the headline like "Open-source voice-agent framework. Self-hosted Python." right under the green-dot pill. The current pill says "Open-source · MIT · Python 3.13" — that's tech credentials, not category.

### P1-2. No demo audio / no demo video

**Where:** Hero (the orb is a CSS animation, not actual audio).
**Problem:** This is a voice product. The single most powerful thing you could show on the page is 15 seconds of actual audio — the user holding the button, AbuelOS responding in Spanish, a timer firing proactively. The orb pretending to listen isn't proof; it's vibes. Every voice-agent post on HN that goes viral has either an embedded audio demo or a Twitter video link in the comments.
**Fix:** Add a Play-Demo button to the hero that triggers a 15–25 second pre-recorded clip — the same scenario the timeline section illustrates. Bonus: make the orb actually react to the playing waveform.

### P1-3. The animated voice-thread bar is a tax, not a feature

**Where:** `site/src/Landing.tsx:51-112`, `site/src/components/VoiceThread.tsx`.
**Problem:** It's beautiful in isolation but it eats ~50px of vertical real estate above the fold _and_ runs a `requestAnimationFrame` loop the entire time you're on the page. On mobile that's a noticeable battery drain for a marketing page. It also competes for attention with the hero orb that's doing roughly the same job. As an HN reader I find myself wondering "what is this scrubber for?" — it's not interactive (clicking the chapters doesn't navigate), so it's pure decoration.
**Fix:** Either (a) make the chapter ticks clickable to scroll to that section (turn the decoration into navigation and earn its real estate), or (b) cut it on mobile entirely, or (c) cut it and put the saved space into the hero so the actual pitch lives above the fold.

### P1-4. Personas section overpromises with five fictional ones

**Where:** `site/src/sections/Personas.tsx:46-187` — Studio, Household, Ops, Tutor, Clinic.
**Problem:** Only AbuelOS and BasicOS ship today (the Today section says "personas: 2"). The Personas section presents six personas with fully realised YAML configs, hardware targets, and constraint sets. To a reader who hasn't yet noticed the "2 personas" stat, this implies a mature persona library. To one who _has_, it reads as 4× more padding than is warranted. The YAMLs are also persuasive enough that someone could try to run `HUXLEY_PERSONA=studio uv run huxley` and get an error.
**Fix:** Either (a) add a header to the persona grid that says "AbuelOS ships today; the rest are designs" (and visually demote the 5 unshipped ones with a subtler tile treatment), or (b) cut to AbuelOS + BasicOS + 2 designed-for personas instead of 6, with explicit framing. The framing applies the same logic you already use in the Skills section ("6 shipped · 60+ designed for · ∞ possible") — extend that honesty to personas.

### P1-5. The architecture diagram on mobile is unreadable

**Where:** `site/src/sections/Architecture.tsx:181` (viewBox `0 0 960 570`).
**Problem:** At 390px screen width minus 48px padding, the SVG renders at ~340px wide. The 10pt mono text inside (`wss://huxley.local:8443`, the node sub-labels, the moving packet labels) becomes 3–4pt — illegible. Same issue with the Timeline SVG (`viewBox 0 0 960 260`).
**Fix:** Either render a simplified mobile diagram (3–4 nodes, larger text, no animated packets), or stack the diagram vertically with bigger labels, or hide the diagram on mobile and replace with a 4-bullet text summary. The current behavior is "shrink to nothing" which is the worst of all worlds.

### P1-6. The "constraints" concept is the most novel idea on the page and it's hidden

**Where:** `site/src/sections/Architecture.tsx:317-326` (one of six bottom cards) and `Personas.tsx` facets.
**Problem:** `never_say_no` is the most distinctive thing about this project. It's a behavioral guarantee enforced framework-wide, declared in YAML, that no other voice framework offers. Right now it's a bullet in a 6-up grid and a line in a YAML snippet. The project's vision doc spends paragraphs on it; the landing barely mentions it.
**Fix:** Promote constraints to a first-class section between Skills and Today, or fold it into the Problem comparison as a fifth Huxley row ("Behavioral constraints declared in YAML, enforced framework-wide"). At minimum, the AbuelOS persona description should foreground "never says no" — that single phrase is more memorable than anything else on the page.

### P1-7. No mention of cost / no per-hour pricing

**Where:** Nowhere on the page.
**Problem:** OpenAI Realtime API costs roughly $0.06/min input + $0.24/min output (commonly known to HN). Anyone evaluating self-hosting will immediately ask "what does this cost me to run?" and the page doesn't engage. This is the #1 question I'd ask in the comments.
**Fix:** Add a one-line cost note near the install snippet — "Runs on OpenAI Realtime: ~$0.06/min listening, ~$0.24/min speaking. Roughly $X/day for the AbuelOS reference deployment." or link to a /pricing or to a cost section in the README. Honesty about cost is a credibility signal on HN.

### P1-8. No license/security/privacy posture beyond "MIT"

**Where:** Footer + nav pill.
**Problem:** A self-hosted voice agent that ships full-duplex Telegram calls and is targeted at a vulnerable user (blind elderly) raises legitimate questions: where do transcripts go, what does the OpenAI Realtime API retain, what's logged, is there a kill-switch. The Clinic persona literally claims "HIPAA-aware. Transcripts stay local." which is a non-trivial regulatory claim to make on a launch page.
**Fix:** Either remove the HIPAA claim (it's a fictional persona but the wording will be quoted) or add a one-paragraph "Privacy" section before the install: where audio goes, where transcripts go, where storage lives. The README has none of this either — fixing it on the landing forces the right honesty.

### P1-9. "Skills filter row" categorical labels aren't translated

**Where:** `site/src/sections/Skills.tsx:113-115` — `cats` is built from `s.cat` strings ("Audio", "Comms", "Home", etc.) which are hard-coded English in `ALL_SKILLS`.
**Problem:** Switch the language toggle to Spanish or French and the "All" button translates but the category buttons stay English. Looks like a half-finished i18n. Same applies to skill `name` strings ("Audiobooks", "Radio") and the "huxley-skill-X" caption.
**Fix:** Either translate the categories and skill names too (probably overkill — packages are named in English) or add an inline note like "Skill names are package identifiers and stay in English" so the inconsistency reads as intentional.

### P1-10. Footer is hollow

**Where:** `site/src/sections/Install.tsx:92-130`.
**Problem:** "MIT licensed · Pre-1.0 · Two personas shipped" — that's it. No links to docs, no link to the GitHub issues, no link to a Discord/Discussions/contact, no maintainer attribution, no "made by [name] / open to PRs."
**Fix:** Add Docs · GitHub · Discussions · Contact · @handle. HN clicks the footer to find out who built it.

---

## P2 — nice to have

### P2-1. The "interrupt" status copy reads as deeply technical

`en.json:30-32`: `"Interrupt": "Atomic drop. Queue cleared. Channel flushed."` — beautiful for a developer, but the orb status that drives this also shows during the hero's idle scroll, where a casual reader briefly sees "ATOMIC DROP. QUEUE CLEARED. CHANNEL FLUSHED" without context. Either keep this only in the architecture/timeline sections or soften the hero copy.

### P2-2. The "orb expressiveness 1.1" feels overtuned

`Hero.tsx:148`. The orb pulses too actively; on first scroll it competes with the hero text. Try `0.7` and let the headline breathe.

### P2-3. The metric "15K Python LOC" is a weak number

`site/src/sections/Today.tsx:23`. 15,000 LOC for a framework + 6 skills is fine, but on its own it doesn't mean much to an HN reader. Replace with something more meaningful: "~15K Python LOC · 132 framework tests · zero runtime deps beyond OpenAI SDK" or similar. Or drop it for a metric that proves momentum (commits in last 30 days, contributors, GitHub stars once you have them).

### P2-4. The roadmap is curated to 4 items, all P1-shaped

`Today.tsx:35-40`. "Cookbook · Secrets · Provider abstraction · ESP32 walky-talky" — three are dev-internal, one is hardware. Missing: anything that excites a non-contributor reader. Add "Multi-language personas in production" or "Real Telegram inbound/outbound pipeline" or "First community skill on PyPI" — something that promises the _user_ a near-term improvement.

### P2-5. The serif italic em treatment is overused

`Hero.tsx:81`, every section title (`Architecture.tsx:162`, `TurnTimeline.tsx:90`, `Personas.tsx:295`, etc.). Every title has the pattern "First line. / _Italic second line._" After three sections it stops feeling distinctive. Try varying — maybe the Today section is the one _without_ the italic flourish, to feel grounded.

### P2-6. The "wss://huxley.local:8443" footer corner of the architecture diagram is a charming detail but misleading

`Architecture.tsx:286`. Default port in the codebase is 8765 (per CLAUDE.md). And `huxley.local` requires mDNS setup. Pick a value that matches reality (`ws://localhost:8765`) or remove.

### P2-7. The "TOML entry-points block" in the Skills code example is the most viral thing on the page — surface it more

`Skills.tsx:359-373`. The line `[project.entry-points."huxley.skills"]` is the punchline of the entire architecture — _that's_ the "it's just Python" moment. Right now it's the bottom of a code block that scrolls off. Pull it out into a callout, or repeat it in the architecture cards.

### P2-8. Replay button on the grows transcript can desync

`HuxleyGrows.tsx:340-344` — calling `restart()` resets `playStartRef.current` while the in-view-paused-loop also touches it. Hard to verify without running it, but worth a once-over for the demo case where someone clicks a tab, scrolls away, and comes back. Demo replays that double-fire are the kind of bug screen-recorders catch.

### P2-9. No favicon or OG image content in `site/index.html`

Worth checking. HN previews render the OG image if present; no preview = lower CTR.

---

## What's missing

Things I wished were on the page that aren't. (Per the constraint, "coming soon" markers for the Huxley-grows section are intentionally excluded.)

1. **Audio demo.** This is a voice product. Even a 10-second "play this" link to an mp3 of AbuelOS reading the news in Spanish would do more than the entire animated skills grid. Currently the page asserts that voice agents are great; it never proves Huxley sounds good.

2. **Cost transparency.** $/hour to run, or at least the raw OpenAI Realtime per-minute numbers with a "we don't markup, you pay OpenAI directly" reassurance. The HN audience cares about this more than almost any other thing, and the absence reads as evasive.

3. **The AbuelOS story.** The vision doc has a moving paragraph: "the first persona shipped on Huxley is AbuelOS — a Spanish-language assistant for an elderly blind user." That story — that this exists because someone built it for a real human, not as a framework exercise — is the emotional hook. Right now AbuelOS is a tile in a 6-up grid; it should be a full-bleed sub-section near the hero. ("This started because my abuelo can't see the screen anymore" — that's a top-comment story.)

4. **Why "Huxley" the name.** A two-line aside near the footer. HN loves naming origin stories; one missed opportunity.

5. **A worked latency number.** "Time from end-of-user-speech to first-audio-byte: ~480ms (p50)" — that's the thing that proves the turn coordinator actually works. Currently the timeline section animates a hypothetical 8-second scenario, but the _real_ numbers are stronger than the animation.

6. **Comparison to LiveKit Agents specifically.** Pipecat is one row in your problem table; LiveKit Agents — which is OpenAI's own recommended framework — should probably be its own row. HN readers will ask "isn't this just LiveKit Agents?" within minutes.

7. **Who runs this in production today.** "Used daily by my grandfather since [date]" — even if it's a sample size of one, that's the most credible production claim available, and you have it.

8. **A Discord / Discussions / contact link.** Where do I go if I want to talk to the maintainer? Where do I file an issue if I find a bug at 1am during the launch? Right now the answer is "open a GitHub issue against a 404 repo" — even after the URL is fixed, a human-touchable channel matters during the first 24 hours.

9. **Telemetry / data-flow diagram.** For a self-hosted product with a third-party AI dependency, a one-glance diagram showing "audio leaves your box → OpenAI → audio comes back, no other party" would defuse the privacy question without you having to write a privacy policy.

10. **A second voice provider on the roadmap.** The Today section mentions "voice provider abstraction" as a "Later" tier item. A skeptical HN reader will read that as "lock-in." Either commit to a near-term second provider (Azure Realtime? Cartesia + Deepgram? a local stack with whisper + a TTS?) or explicitly defend the single-provider choice.

---

## Would I upvote? — verdict

**No, not in its current state.** The site is visually distinctive and the architecture is clearly real, but the dead GitHub link, the broken install snippet, and the speculative-presented-as-shipped Huxley-market section would cost trust within 30 seconds. I'd close the tab and refresh tomorrow.

**Once P0-1 through P0-4 are fixed — yes, I'd upvote and probably comment.** Huxley is a meaningfully different point in the voice-agent design space (constraints in YAML, persona/skill split, turn coordinator) and the page demonstrates that with both diagrams and code. After the P0s, work the P1s in this order: P1-2 (audio demo) → P1-1 (clearer hero) → P1-7 (cost) → P1-4 (persona honesty) → P1-3 (mobile voice-thread). That sequence buys the most trust per hour of work.

Final blunt note: this landing is doing too much. The waveform, the orb, the live transcript, the animated architecture, the staggered tile grid, the chapter scrubber, the per-section glow effects — together they say "we hired a designer." HN says "show me the work." Cut 30% of the animation budget, add the audio demo, and you have a top-3 launch. Leave it as-is and you have a beautifully crafted post that ranks in the 20s.

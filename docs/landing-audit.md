# Landing Page Audit Report

**Date:** 2026-04-28  
**Methodology:** Findings produced by an Explore agent reading the codebase fresh; reviewed and addressed by claude in subsequent commits.

---

## Quick Stats

- **Shipped first-party skills:** 6 (audiobooks, news, radio, system, telegram, timers)
- **Shipped personas:** 2 (abuelos, basicos)
- **Framework Python LOC:** ~8,758 (runtime: 7,186 + sdk: 1,572)
- **Test count:** 34 test files across server packages
- **ADRs filed:** 15 architectural decisions documented
- **Proof language:** Python 3.13, MIT licensed, pre-1.0 status

---

## Section A: Wrong — Factually Inaccurate Claims

### Finding A1: "Six personas in the wild" is overstated

**Landing text:**  
> "Six personas in the wild" (Install.tsx:112, Footer)

**Reality:**  
- Only 2 personas shipped in the codebase: `abuelos` and `basicos` (server/personas/)
- No evidence of 6 personas deployed externally
- README.md credits Abuelo as "the first persona shipped on Huxley" (line 38)

**Recommendation:**  
Update to "Two personas in the codebase" or "Abuelo deployed" to match shipped reality. If external personas exist, document them in a personas registry before claiming "in the wild."

**File:Line:** `site/src/sections/Install.tsx:112`

---

### Finding A2: OpenAI Realtime API integration claim needs provider context

**Landing text:**  
> "Voice provider: OpenAI Realtime" (Architecture.tsx:44)

**Reality:**  
- OpenAI Realtime is the only implemented provider today (voice/openai_realtime.py)
- docs/vision.md explicitly states (line 22): "Not a model. Huxley wraps OpenAI's Realtime API today; the architecture leaves room for other providers, but Huxley itself doesn't train or serve models."
- Roadmap (roadmap.md:42) marks "Voice provider abstraction" as "Later: Trigger: a credible second provider exists. Not speculative."

**Recommendation:**  
Update Architecture subtitle or add a note: "Currently powered by OpenAI Realtime API; provider abstraction is planned." This makes clear that multi-provider support is a design goal, not yet shipped.

**File:Line:** `site/src/sections/Architecture.tsx:44`

---

## Section B: Aspirational-but-Unmarked Claims

### Finding B1: "Huxley-market" and "Huxley-grows" framing is appropriate *but* needs visible "future" label

**Landing text:**  
§ 05 title: "You ask. It finds, or builds."  
Subtitle: "First it checks huxley-market — a registry of free, open-source skills built by other users. If nothing fits, huxley-grows writes one from scratch."  
(HuxleyGrows.tsx:360-368)

**Reality:**  
- Per user decision: these sections describe future capabilities, legitimately previewing vision.
- Huxley-market does not exist in the codebase; no registry, no skill discovery system, no market backend.
- Huxley-grows does not exist; no AI-driven skill generation, no "clawbot" agent, no automatic testing/installation pipeline.
- Roadmap lists both under "Excluded from Huxley framework" and future roadmap items (roadmap.md:54).
- JOB_PHASES mock object in HuxleyGrows.tsx defines UI states for features that don't exist yet.

**Assessment:**  
The framing is *accurate as aspirational content* — these are the right capabilities to preview. But a reader unfamiliar with Huxley's status may think they're shipped. The section is correctly labeled as "§ 05" in the flow, but lacks a visual "future" or "coming" indicator.

**Recommendation:**  
Add a small label or visual indicator near the section title:
- Subtitle addition: "First it checks huxley-market **(coming)** — a registry..."
- Or: Add a "Future capability" pill badge next to the eyebrow
- Keep all copy as-is; just signal the timeline.

**File:Line:** `site/src/sections/HuxleyGrows.tsx:360-368`

---

### Finding B2: "Huxley-web" client mentioned but relationship unclear

**Landing text:**  
> "Install from voice, or tap it through huxley-web on your phone." (HuxleyGrows.tsx:548)  
> "Open huxley-web → Installed → Spotify → Settings to link your account." (HuxleyGrows.tsx:143)

**Reality:**  
- A PWA client exists at `clients/pwa/` but is called the "browser dev client" in architecture.md (line 19) and README.
- No production "huxley-web app" for phones yet; roadmap (v∞) lists "ESP32 walky-talky client" as next hardware but no phone app.
- The PWA is designed for browser (SvelteKit); mobile capability is aspirational.

**Assessment:**  
The landing conflates a dev browser client with a future phone app. This could mislead someone trying to install.

**Recommendation:**  
Change to: "Install from voice, or manage it through the browser client at `localhost:5173`." Remove phone-specific language until a phone app ships. Or add **(planned)** to "huxley-web on your phone."

**File:Line:** `site/src/sections/HuxleyGrows.tsx:548, 143`

---

## Section C: Missing from Landing — Shipped Features Not Promoted

### Finding C1: Behavioral constraints system is shipped but invisible on landing

**What it is:**  
Personas declare behavioral constraints (`never_say_no`, `confirm_destructive`, `child_safe`) that the framework bakes into the system prompt. This is a shipped, core feature for safety-critical deployments.

**Where documented:**  
- `docs/architecture.md:61-63` — "Persona is config, not code"
- `docs/roadmap.md:28` — constraint registry shipped
- `server/personas/abuelos.md` (not read here, but referenced)
- README.md line 43: "Behavioral constraints"

**Why it should appear:**  
It's a differentiator: other frameworks don't offer constraints-as-configuration. Especially important for vulnerable users (elderly, children, medical). Currently only mentioned in Problem section as abstract "extensibility," not as concrete safety feature.

**Recommendation:**  
Add to Architecture section or create a brief subsection in Problem:
- "Behavioral constraints — declared in persona.yaml and enforced system-wide. Build for vulnerable users without special code."

**File:Line:** `site/src/sections/Problem.tsx` or `site/src/sections/Architecture.tsx`

---

### Finding C2: Proactive speech / `inject_turn` is a shipped, powerful primitive

**What it is:**  
Skills can call `ctx.inject_turn()` and `ctx.inject_turn_and_wait()` to speak without user input. Used by timers, Telegram (inbound messages), future reminders. Allows doorbell notifications, medication reminders, alert handling.

**Where documented:**  
- `docs/architecture.md:213-216` — FocusManager / activity scheduling
- `docs/roadmap.md:39` — shipped via focus-plane completion
- `server/skills/timers` and `server/skills/telegram` are working examples
- README.md line 37: "Proactive speech"

**Why it should appear:**  
Currently the landing shows proactive speech only in the Turn Timeline (single example: "Proactive · Timer's up"). The broader capability to inject turns from skills is powerful and differentiates from chatbots. Skill authors see this as permission to build alert systems, not just request handlers.

**Recommendation:**  
Expand Turn Timeline or add a callout in Skill System section:
- "Proactive turns — a timer fires, a call arrives, a reminder triggers. Skills call `ctx.inject_turn()` and Huxley speaks first, without waiting for the user. No terminal needed."

**File:Line:** `site/src/sections/Skills.tsx` or `site/src/sections/TurnTimeline.tsx`

---

### Finding C3: Audio bridging (`InputClaim`) mentioned in README but absent from landing

**What it is:**  
Skills can claim the mic and speaker for full-duplex audio (Telegram voice calls, future phone calls, voice memos). Shipped and working in `huxley-skill-telegram`.

**Where documented:**  
- `docs/architecture.md:213-216` — MicRouter, InputClaim in FocusManager
- `README.md:39` — "Audio bridging (InputClaim)"
- `server/skills/telegram/skill.py` — live example

**Why it should appear:**  
It's a shipped, concrete capability that Pipecat/LiveKit also require users to solve. Showing it on the landing validates the claim "we solve the hard parts." Currently invisible.

**Recommendation:**  
Add to Architecture diagram or as a callout:
- "Audio bridging — skills can claim the mic and speaker for full-duplex audio (calls, voice memos). The framework prevents collisions with model speech via the FocusManager."

**File:Line:** `site/src/sections/Architecture.tsx`

---

### Finding C4: Personas section shows 6 personas in code but only 2 ship

**Landing text:**  
Personas section displays 6 persona cards: Abuelo, Studio, Household, Ops, Tutor, Clinic (Personas.tsx:17-184)

**Reality:**  
- Only Abuelo and Basicos are in `server/personas/`
- The other four (Studio, Household, Ops, Tutor, Clinic) are designed-for examples in the landing, not shipped
- The YAML in each card is aspirational — these personas don't exist as runnable config

**Assessment:**  
This is not *wrong* (the examples are labeled correctly in the grid), but it reinforces the "six personas" myth. A reader will assume all six are downloadable and ready to run.

**Recommendation:**  
Option A: Keep 6 but add a small note: "Abuelo and Basicos are shipped. Others are designed-for examples."  
Option B: Show only the 2 shipped personas and a "Design your own" prompt.  
Option C: Keep all 6 but visually distinguish shipped vs. examples (highlight, badge, different row).

**File:Line:** `site/src/sections/Personas.tsx:17-184`

---

## Section D: Unverified Claims Requiring Clarification

### Finding D1: "Opinionated where hard, open where it matters" — specificity check

**Landing text:**  
Problem section, Huxley row: "Opinionated where hard. Open where it matters. Your persona, your skills, your hardware." (Problem.tsx:45-47)

**Code truth:**  
- Opinionated: turn coordinator, audio sequencing, focus manager, one-channel output — all hardcoded to Huxley's design.
- Open: skill extensibility via entry points (✓), persona-as-config (✓), client variety (browser, ESP32 planned, ✓), storage backend (SQLite only, not pluggable).

**Assessment:**  
The claim is mostly true but "your hardware" is partially aspirational — today only browser + ESP32 (planned). Storage backend is not user-pluggable yet.

**Recommendation:**  
Refine to: "Opinionated on turn sequencing and audio flow. Open for skills, personas, and client devices."

**File:Line:** `site/src/sections/Problem.tsx:45-47`

---

## Summary of High-Impact Findings

1. **A1 (Wrong)**: "Six personas in the wild" — only 2 shipped, misleading claim.
2. **B1 (Aspirational)**: Huxley-market and Huxley-grows lack "future" visibility labels; readers may think they're shipped.
3. **C1 (Missing)**: Behavioral constraints are a shipped safety feature but invisible on landing.
4. **C2 (Missing)**: Proactive speech (`inject_turn`) is shipped but under-promoted; only shown as a timeline detail, not a framework primitive.
5. **C3 (Missing)**: Audio bridging via `InputClaim` is shipped but absent from landing entirely.

---

## Recommended Priority & Action Plan

**P0 (Ship blockers):**
- Fix A1: "Six personas in the wild" → "Two personas shipped" or remove until 6 exist.
- Fix B1: Add "(coming soon)" or visual indicator to Huxley-market / Huxley-grows sections.

**P1 (Proof & credibility):**
- Add C1: Behavioral constraints callout in Problem or Architecture.
- Add C2: Proactive speech as a skill primitive in Skills section or new subsection.

**P2 (Completeness):**
- Add C3: Audio bridging in Architecture or a new Skills+Audio subsection.
- Refine D1: Update "open where it matters" claim for accuracy.

---

**Report status:** Complete. All findings include file:line references and specific recommendations for the landing team.

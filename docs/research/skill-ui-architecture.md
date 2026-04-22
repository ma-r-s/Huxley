# Skill UI architecture — design exploration

> **Status**: exploratory / deferred. Nothing here is implemented. This doc
> records a design conversation (2026-04-22) so the thinking isn't lost.
> Revisit when huxley-web is mature enough to need real skill UI extension.
> Triage entry: **D5** in `docs/triage.md`.

---

## The problem

Skills and huxley-web evolved in parallel without a formal UI contract.
Today the server signals session state (`IDLE/CONNECTING/CONVERSING`),
`model_speaking`, `input_mode`, and raw audio. huxley-web derives its
visual states from those signals. This works for the core PTT loop, but:

1. **The orb has richer states than the server knows how to signal.** The
   design exploration (2026-04-22) produced 8 orb states (Idle / Listening /
   Thinking / Speaking / Live / Paused / Wake / Error). Six derive from
   existing signals. `Wake` and `Paused` have no current server signal.

2. **Skills have no UI channel.** A timer skill fires a reminder but the orb
   just returns to idle — no countdown. An audiobook skill plays audio but
   the UI has no progress. A Telegram call is live but the contact name is
   invisible. There is no mechanism for a skill to push visual data.

3. **"Install a skill" should optionally include its UI.** Right now installing
   a skill (`uv add huxley-skill-timer`) adds voice behavior only. The vision
   is: installing a skill could also deliver a UI component that renders in
   huxley-web without rebuilding the app.

---

## What was rejected: a widget vocabulary

First instinct was a closed vocabulary of widget types (`countdown`,
`media_progress`, `active_call`, `status_badge`, `notification`). Skills
emit `server_event` with typed payloads; huxley-web renders the matching
widget. Adding a new widget type requires updating huxley-web.

**Rejected because**: caps what skills can express. A git-contribution-map,
a personalized diagram, a novel counter design — none of these fit a closed
vocabulary. The vocabulary becomes a constant maintenance burden as each new
skill invents a new visualization. The right abstraction is the container
system, not the content.

---

## The right model: Apple WidgetKit on the web

Apple's WidgetKit is the correct reference:

- **Apple (OS) owns**: container shape, sizing contracts, transition animations,
  where things appear on screen, how they enter and exit.
- **Developer (skill) owns**: everything drawn inside the container. Arbitrary
  rendering — diagrams, maps, counters, whatever.

The OS provides the "envelope." The developer provides the content. Neither
knows the other's internals.

### On the web, concretely

**huxley-web owns:**

- CSS custom properties (design tokens): colors, typography, radius, spacing.
  E.g. `--hx-color-primary`, `--hx-font-display`, `--hx-radius-orb`.
- Entry / exit animation system: CSS transition classes applied to the
  container element. Skill components never touch transitions.
- Container sizing slots: `small` (live action strip) / `full` (skill view panel).
- Where containers live on screen and when they are shown.

**The skill owns:**

- The component rendered inside the container.
- Consumes the design tokens via CSS custom properties.
- Draws whatever it wants — no content restriction.

---

## Delivery mechanism

Skills are Python packages. UI is an optional `ui/` directory inside the
package:

```
huxley-skill-timer/
  src/huxley_skill_timers/skill.py    ← Python, unchanged
  ui/
    manifest.json                     ← container declarations
    live.js                           ← compiled Svelte → vanilla JS
    full.js                           ← compiled Svelte → vanilla JS (optional)
```

Svelte compiles to **vanilla JavaScript** — no Svelte runtime dependency
at the consuming end. This sidesteps framework version compatibility: the
skill bundles its own rendered output, not framework code.

The Huxley Python server serves `GET /skills/{name}/ui/{file}` as static
files from the installed package directory.

huxley-web loads them at connect time:

```js
// after receiving hello.skills manifest
const mod = await import("http://localhost:8765/skills/timer/ui/live.js");
// mount mod.default into the live-action container
// huxley-web has already injected CSS custom properties into the container
```

The manifest declares what the skill provides and which `server_event` types
activate each container:

```json
{
  "name": "timers",
  "design_tokens_version": 1,
  "live": { "entry": "live.js", "trigger": "timer.tick" },
  "full": { "entry": "full.js", "trigger": "timer.tick" }
}
```

huxley-web reads `hello.skills` on connect, dynamically imports present UIs,
and mounts them when the triggering `server_event` arrives.

---

## Two container types

### Live action (small, non-blocking)

Always visible when a skill is active. Floats alongside the orb — does not
replace it. Multiple can coexist. Not interactive beyond tap-to-expand.

Reference: Apple's Dynamic Island.

Examples:

- Timer countdown ring with label ("Pastilla · 0:43")
- Active call duration + contact name ("Hija · 2:14")
- Audiobook chapter + elapsed ("Cap. 3 · 12:30")

### Skill view (full panel, navigable)

User navigates here from a live action or via voice. Full creative control.
This is where diagrams, contribution maps, large visualizations live.

Examples:

- Timer: large visual clock, remaining time prominent
- Audiobooks: cover art, chapter list, progress bar, speed control
- Comms: contact list, call history

---

## The orb state machine gaps

Six of the eight design-explored states already map to existing signals:

| State     | Source                                                |
| --------- | ----------------------------------------------------- |
| Idle      | `state: IDLE`                                         |
| Listening | PTT held + `inputMode=assistant_ptt` (client-derived) |
| Thinking  | After `ptt_stop`, before first audio (client-derived) |
| Speaking  | `model_speaking: true`                                |
| Live      | `inputMode: skill_continuous`                         |
| Error     | Disconnect without user action (client-derived)       |

Two are missing:

| State  | Gap                     | Fix needed                                                       |
| ------ | ----------------------- | ---------------------------------------------------------------- |
| Wake   | No server→client signal | Server should mirror the `wake_word` event back to client        |
| Paused | No signal               | Skills (audiobooks, radio) emit `server_event("content.paused")` |

These are small additions independent of the larger skill UI architecture.
They can land before the full architecture ships.

---

## Honest pushbacks / open problems

### 1. Skills become Python + JavaScript packages

Writing a skill today means writing Python. With skill UI, it means Python
_and_ a JS build pipeline (Vite + Svelte). Higher authorship bar. Fine for
Mario writing family skills. Real barrier for a community ecosystem.

**Mitigation**: a skill scaffolding template (`uv run huxley new-skill --with-ui`)
that generates both sides with the build pipeline pre-configured. Must exist
alongside the architecture for it to be usable.

### 2. Design token versioning is a contract, not a courtesy

When huxley-web renames `--hx-color-primary` to `--hx-accent`, skills
compiled against the old name silently get no color. The manifest's
`design_tokens_version` field is how skills declare what they were built
against. huxley-web must maintain backwards compatibility until a major
version bump, with explicit deprecation cycles. This needs to be an
intentional decision before any skill ships UI, not retrofitted.

### 3. Non-web clients are excluded from custom rendering

A hardware device with an e-ink display, a native mobile app, or any
non-JS client cannot run Svelte bundles. These clients get `server_event`
data but no component to render.

**Framing**: skill UI is **progressive enhancement**. The voice behavior
works without it. The JS component is an additional layer for clients that
can render it. A non-web client implements its own rendering (or nothing)
using the same `server_event` data stream. This must be explicit in the
architecture — skill _behavior_ must never depend on its UI component existing.

### 4. Dynamic import from localhost has a CORS/CSP story

If huxley-web is ever served from a CDN, dynamically importing from
`http://localhost:8765` is blocked by CORS and CSP. This design assumes
huxley-web and the Huxley server are **always co-located** (confirmed: yes,
always local). If that assumption ever changes, the delivery mechanism needs
rethinking (skills publish to npm, huxley-web installs at build time, etc.).

---

## What to implement first (when this becomes real)

Before the full architecture, two small things can land independently:

1. **`Wake` and `Paused` orb states** — tiny server-side additions, no
   skill UI architecture needed. Wire the `wake_word` event back to the
   client; audiobooks/radio emit `server_event("content.paused")`.

2. **`hello.skills` manifest in the `hello` message** — extend the hello
   handshake with installed skill metadata. Doesn't require serving JS
   bundles yet; just makes the contract explicit and gives huxley-web
   something to read.

The full dynamic-import + container system is the third step, and only
makes sense once at least one skill has UI worth showing.

---

## Revisit triggers

- huxley-web reaches a stable design language worth committing to as a token
  contract
- A skill with an obvious UI need ships (timer countdown, audiobook progress)
  and the voice-only experience feels clearly incomplete
- A second developer wants to write a skill with custom UI

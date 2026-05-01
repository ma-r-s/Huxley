# Skill Marketplace

> **Status**: design — feature in flight as **T1.14**. See [`triage.md` § T1.14](./triage.md) for the work tracker.
>
> This is the architectural specification — the contract between user, runtime, PWA, registry, and skill author. Read this to know **what we're building and why**. Read the triage entry to know **what's done and what's next**.

## Vision

Huxley's load-bearing thesis is **skill extensibility** — a voice agent framework whose differentiator is "the LLM understands rough natural-language intent and dispatches to user-installable custom tools." The marketplace is the sociotechnical layer that turns "you _can_ extend it" into "people _do_ extend it."

The shape is **VS Code-like**:

- A **curated registry** lists known-good skills. End-users browse + install via the PWA.
- A **sideload escape hatch** lets developers `pip install` any package and enable it locally.
- **Install is global to the runtime; enable is per-persona.** Pip-install once; persona's YAML decides which to load.
- **Configuration is in the PWA.** Each skill declares a config schema; the PWA renders a form. API keys + OAuth tokens live in a per-persona secrets directory.
- **Trust = registry curation.** Mario gatekeeps what enters the registry. No sandboxing — the user owns their device, just like VS Code.

End-users (the elderly, blind AbuelOS user; their caregiver) never touch a terminal. Developers can.

## Glossary

- **Registry** — a JSON file listing known skills, hosted in a public GitHub repo. Source of truth for the PWA's "Marketplace" tab.
- **Sideload** — install a skill by package name without going through the registry. Developer-mode only.
- **Install** — pip-install a `huxley-skill-<name>` package into the runtime's Python environment. Global; survives across personas.
- **Enable** — list a skill in a persona's `persona.yaml` `skills:` block so it loads when that persona is active. Per-persona.
- **Configure** — set per-skill fields (API keys, preferences, etc.) declared by the skill's `config_schema`. Per-persona (each persona's instance of a skill has its own config).
- **Secrets dir** — `<persona.data_dir>/secrets/<skill>/` — where API keys, OAuth tokens, etc. live for one persona's instance of one skill.
- **Config schema** — JSON Schema published by a skill class declaring what fields the user must provide. Drives the PWA form.

## Locked product decisions (2026-05-01)

| #   | Question          | Decision                                                                          | Rationale                                                                                                                                                       |
| --- | ----------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Audience          | **Both** — non-technical end user via PWA; developer via terminal escape hatches. | Abuelo's user can't use a terminal; contributors and self-hosters can.                                                                                          |
| 2   | Registry shape    | **Curated** with sideload escape hatch.                                           | Mario controls what's in. Developers bypass for their own packages. Same model as VS Code (official Marketplace + VSIX sideload).                               |
| 3   | Install mechanism | **Restart-on-install.**                                                           | Python module hot-reload is fragile; ~3 second outage with audible "be right back" + earcon is acceptable for non-realtime moments.                             |
| 4   | Scope             | **Install global; enable per-persona.**                                           | Maps cleanly onto the existing persona-as-different-person model (T1.13). Each persona = its own skill set, but the underlying packages are pip-installed once. |
| 5   | Config UX         | **Entirely in the PWA.**                                                          | Skills declare a config schema; PWA renders a form. Secrets edited via PWA, stored in per-persona secrets dir. No YAML hand-editing for end-users.              |
| 6   | Trust model       | **User-owned device, registry-curated.**                                          | No sandboxing. Mario is responsible for keeping the registry clean. User is responsible for what they install (incl. sideloads). Same as VS Code Marketplace.   |

## Architecture overview

```
┌──────────────────────────────────────────────────────────────────────┐
│ REGISTRY (huxley/skills GitHub repo)                                 │
│ https://raw.githubusercontent.com/huxley/skills/main/index.json      │
│                                                                      │
│ {                                                                    │
│   "schema_version": 1,                                               │
│   "skills": [                                                        │
│     { "name": "spotify",                                             │
│       "pypi": "huxley-skill-spotify",                                │
│       "version": "0.3.0",                                            │
│       "description": "Voice-control Spotify",                        │
│       "tags": ["music", "official"],                                 │
│       "author": "huxley team",                                       │
│       "repo": "https://github.com/huxley/huxley-skill-spotify",      │
│       "config_schema_url": ".../spotify.schema.json",                │
│       "screenshots": [...] }                                         │
│   ]                                                                  │
│ }                                                                    │
│                                                                      │
│ Submissions = pull requests to this repo.                            │
│ Mario reviews, merges = "in the registry."                           │
│ CI validates entries against schema/index.schema.json.               │
└──────────────────────────────────────────────────────────────────────┘
              ▲ PWA fetches at startup + on demand
              │
              │ (read-only, anonymous, no backend)
              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ PWA (clients/pwa)                                                    │
│                                                                      │
│  Settings → Skills                                                   │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │ [Installed]  [Marketplace]  [Sideload (dev)]               │      │
│  │                                                            │      │
│  │ Active persona: abuelos                                    │      │
│  │   ☑ audiobooks    [config] [logs]                          │      │
│  │   ☑ news          [config]                                 │      │
│  │   ☐ spotify       [enable]   ⚙ install required            │      │
│  │   ☑ telegram      [config]   ⚠ missing api_id              │      │
│  │                                                            │      │
│  │ Spotify › Configure                                        │      │
│  │ ┌──────────────────────────────────────────────────┐       │      │
│  │ │ Client ID  [________________________]  required  │       │      │
│  │ │ Token      [✱✱✱✱✱✱✱]   [Re-authenticate]         │       │      │
│  │ │ [Save]                                           │       │      │
│  │ └──────────────────────────────────────────────────┘       │      │
│  └────────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────────┘
              │ WS messages
              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ RUNTIME (Python, server/runtime)                                     │
│                                                                      │
│  New endpoints (T1.14 wire surface):                                 │
│   list_installed_skills           → SkillRegistry contents           │
│   get_skill_config_schema(name)   → from skill.config_schema         │
│   get_skill_config(persona, name) → YAML + secrets                   │
│   set_skill_config(persona, name, cfg, secrets)  → write + reload    │
│   enable_skill / disable_skill    → edit persona.yaml + reload       │
│   install_skill(pypi_name)        → pip install + self-restart       │
│   uninstall_skill(name)           → pip uninstall + restart          │
│                                                                      │
│  New SDK surface:                                                    │
│   Skill.config_schema: ClassVar[dict | None]                         │
│       JSON Schema describing the skill's per-persona config.         │
│       None ⇒ no PWA form (legacy / config-less skills).              │
│                                                                      │
│   ctx.secrets: SkillSecrets                                          │
│       .get(key) → str | None                                         │
│       .set(key, value) → None                                        │
│       Backed by <persona.data_dir>/secrets/<skill>/values.json       │
│                                                                      │
│  Existing substrate (already shipped):                               │
│   ✓ huxley.skills entry-point loading                                │
│   ✓ Per-persona DB + namespaced storage (T1.12)                      │
│   ✓ Hot persona swap (T1.13) — drives reload-on-config-change        │
└──────────────────────────────────────────────────────────────────────┘
```

## Components

### The registry

**Location**: a public GitHub repo (`huxley/skills` or similar). The PWA fetches `https://raw.githubusercontent.com/<org>/skills/main/index.json` directly. No backend.

**Why GitHub-hosted**:

- Free hosting via raw.githubusercontent (CDN-cached, fast).
- Submissions = pull requests (review trail is git history).
- Versioned; rollbacks via `git revert`.
- Open and auditable — anyone can see the registry contents and history.
- Sets the precedent that "the registry is community infrastructure, not a black box."

**Submission workflow**:

1. Skill author publishes their package to PyPI as `huxley-skill-<name>`.
2. Author opens a PR adding an entry to `index.json`.
3. CI validates the entry against `schema/index.schema.json` (e.g. required fields, valid PyPI name, version is real).
4. Mario reviews — checks the PyPI package source, the linked repo, the config schema.
5. Mario merges = entry is live; PWA users see it on next fetch.

**Maintainer responsibilities** (Mario):

- Review every submission for malicious code, broken builds, deceptive metadata.
- De-list (revert) a skill that's discovered to be malicious post-merge.
- Maintain `CONTRIBUTING.md` with the submission rules + review criteria.

### Runtime additions

#### SDK changes (`huxley_sdk`)

```python
class Skill(Protocol):
    name: ClassVar[str]

    # NEW (T1.14 Phase 1):
    # JSON Schema describing this skill's config — drives the PWA form.
    # None means "no user-configurable fields" (most legacy skills).
    config_schema: ClassVar[dict | None] = None

    # ... existing methods (setup, tools, dispatch, prompt_context, teardown, reconfigure)
```

```python
@dataclass
class SkillContext:
    # ... existing fields (storage, persona_data_dir, config, language, ...)

    # NEW (T1.14 Phase 1):
    # Per-skill secrets store. Backed by a JSON file at
    # <persona_data_dir>/secrets/<skill>/values.json. Skills SHOULD use
    # this for API keys, OAuth tokens, anything that must NOT land in
    # persona.yaml. The PWA's config form writes here for any field
    # the schema marks `"format": "secret"`.
    secrets: SkillSecrets


class SkillSecrets(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def keys(self) -> list[str]: ...
```

#### Server endpoints

New WebSocket message types (additive — existing protocol stays at v2):

| Direction | Type                      | Payload                                                    | Description                                                                                 |
| --------- | ------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| C→S       | `list_installed_skills`   | —                                                          | Request the runtime's skill registry.                                                       |
| C→S       | `get_skill_config_schema` | `{ name: string }`                                         | Request the JSON Schema for one skill's config.                                             |
| C→S       | `get_skill_config`        | `{ persona: string, name: string }`                        | Read current config + secrets-keys (NOT values) for a skill in a persona.                   |
| C→S       | `set_skill_config`        | `{ persona, name, config: object, secrets: object }`       | Write config (to YAML) + secrets (to data dir). Triggers persona reload.                    |
| C→S       | `enable_skill`            | `{ persona: string, name: string }`                        | Add skill to persona's `skills:` block. Triggers persona reload.                            |
| C→S       | `disable_skill`           | `{ persona: string, name: string }`                        | Remove skill from persona's `skills:` block. Triggers persona reload.                       |
| C→S       | `install_skill`           | `{ pypi_name: string, version?: string }`                  | Pip-install + self-restart.                                                                 |
| C→S       | `uninstall_skill`         | `{ name: string }`                                         | Pip-uninstall + self-restart.                                                               |
| S→C       | `installed_skills_list`   | `{ skills: [{name, version, has_config_schema, ...}] }`    | Reply to `list_installed_skills`.                                                           |
| S→C       | `skill_config_schema`     | `{ name: string, schema: object }`                         | Reply to `get_skill_config_schema`.                                                         |
| S→C       | `skill_config`            | `{ persona, name, config: object, secret_keys: string[] }` | Reply to `get_skill_config`. Note: secret VALUES never leave the server; only KEY names.    |
| S→C       | `skill_install_started`   | `{ pypi_name }`                                            | Server is about to pip-install + restart. PWA shows spinner + plays "be right back" earcon. |
| S→C       | `skill_install_failed`    | `{ pypi_name, error }`                                     | Pip install failed. PWA surfaces the error.                                                 |

#### Self-restart machinery

The runtime must restart itself after `install_skill` / `uninstall_skill` so the new entry points are picked up. Two paths:

- **Production** (launchd): server runs `os._exit(0)`; launchd revives it. ~3 seconds.
- **Dev** (`uv run huxley`): server uses `os.execv(sys.executable, [...])` to replace itself with a fresh Python process. ~1 second.

The runtime auto-detects which (presence of `LAUNCH_AGENT_*` env vars) and chooses. Both paths first complete the in-flight skill teardowns + storage flush before exiting.

#### Concurrent install protection

Only one install can be in flight at a time. Subsequent `install_skill` requests during an in-flight install return `skill_install_failed` immediately. (Once we restart, queued requests are dropped on the floor — that's acceptable; user retries.)

### PWA additions

#### Skills panel

A new sheet (alongside Sessions, Logs, Device): `Skills`.

Three tabs:

1. **Installed** — what's pip-installed in the runtime. Each row shows enable/disable toggle + per-persona-config button. Lists per active persona. ⚠ icons for missing required config.
2. **Marketplace** — the registry. Browseable list of available skills with descriptions, tags, screenshots. "Install" buttons.
3. **Sideload** — a developer-mode panel (gated by a toggle). Free-text input "PyPI package name" + Install button.

#### Config form

JSON Schema → form. Use `@rjsf/core` or similar. Renders:

- String fields with help text.
- Boolean checkboxes.
- Number inputs (with min/max).
- Enum dropdowns.
- Secret fields (`"format": "secret"`) as password inputs; PWA never displays secret values, just shows whether one is set.
- Required-vs-optional indicators.
- Validation against the schema.

On Save: PWA sends `set_skill_config` with `{config, secrets}`. Server writes YAML + secrets dir, fires a persona-reload (which does a soft swap to the same persona — same machinery as T1.13).

#### Install UX

When user taps Install:

1. PWA sends `install_skill`.
2. Server replies `skill_install_started` and starts pip install.
3. PWA shows status: "Installing Spotify…" (in the active persona's language).
4. Server pip-installs + plays a "be right back" status to the WS.
5. Server self-exits.
6. PWA's WS closes. Auto-reconnect kicks in (~3s).
7. New WS opens. Server lists installed skills now including Spotify.
8. PWA shows Spotify in Installed; offers to enable + configure.

Failure path: pip install errors → server replies `skill_install_failed` with the error → PWA shows the error message and stays on the marketplace screen.

### Persona changes

#### YAML edit + reload

Today persona.yaml's `skills:` block is the source of truth. The PWA writes to it via `enable_skill` / `disable_skill` / `set_skill_config`.

After write, the runtime triggers a "soft reload" of the active persona — same machinery as the T1.13 swap, but to the same persona. Application is rebuilt with the new YAML; old is torn down in the background. The user hears the swap earcon + "Updating skills…" status. ~1 second outage.

#### Config-vs-secrets split in the YAML

```yaml
skills:
  spotify:
    client_id: 8a3c... # plain config, lives in YAML
    # (no token here — secrets are in <data_dir>/secrets/spotify/values.json)
```

The skill's `config_schema` flags secret fields with `"format": "secret"`. The PWA hides those values; the server stores them in the per-persona secrets dir, not the YAML. Skills read via `ctx.secrets.get("token")`.

## Contracts

### Registry index schema (`schema/index.schema.json`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["schema_version", "skills"],
  "properties": {
    "schema_version": { "type": "integer", "const": 1 },
    "skills": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "pypi", "version", "description", "tags"],
        "properties": {
          "name": { "type": "string", "pattern": "^[a-z][a-z0-9_-]*$" },
          "pypi": { "type": "string", "pattern": "^huxley-skill-[a-z0-9-]+$" },
          "version": { "type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+" },
          "description": { "type": "string", "maxLength": 280 },
          "tags": { "type": "array", "items": { "type": "string" } },
          "author": { "type": "string" },
          "repo": { "type": "string", "format": "uri" },
          "config_schema_url": { "type": "string", "format": "uri" },
          "screenshots": {
            "type": "array",
            "items": { "type": "string", "format": "uri" }
          }
        }
      }
    }
  }
}
```

### Config schema convention

Each skill's `config_schema` is a JSON Schema object describing its YAML config. The PWA uses the schema to render a form.

Conventions on top of vanilla JSON Schema:

- `"format": "secret"` on a string field → PWA renders password input + stores in secrets dir.
- `"format": "oauth"` on a string field → PWA renders an "Authenticate with X" button instead of a text input. The skill provides an `oauth_provider` extension.
- `"x-huxley:help"` → markdown help text rendered alongside the field.
- `"x-huxley:placeholder"` → input placeholder text.

Minimal example:

```json
{
  "type": "object",
  "required": ["client_id"],
  "properties": {
    "client_id": {
      "type": "string",
      "title": "Client ID",
      "description": "From your Spotify Developer Dashboard.",
      "x-huxley:help": "Create an app at developer.spotify.com → Dashboard → Create App. Copy the Client ID here."
    },
    "token": {
      "type": "string",
      "title": "Access token",
      "format": "oauth",
      "description": "Authorize Huxley to control your Spotify."
    }
  }
}
```

### Secrets storage layout

```
<persona.data_dir>/
└── secrets/
    └── <skill_name>/
        ├── values.json     # {key: value}
        └── README.md       # auto-generated, explains contents
```

Permissions: `0700` on the secrets dir, `0600` on values.json. (Filesystem-level only; no encryption-at-rest in v1.)

### Wire protocol additions

See "Server endpoints" table above. All additive; protocol version stays at 2.

## User flows

### End-user: install Spotify and use it

```mermaid
sequenceDiagram
  actor U as User
  participant P as PWA
  participant S as Server
  participant R as Registry

  U->>P: Open Skills panel → Marketplace tab
  P->>R: GET index.json
  R-->>P: { skills: [..., spotify] }
  U->>P: Tap "Spotify" → tap Install
  P->>S: install_skill(huxley-skill-spotify)
  S-->>P: skill_install_started
  P->>U: 🔊 "Be right back…" + earcon
  S->>S: pip install + os.execv
  P->>S: (WS reconnects after ~3s)
  S-->>P: hello + installed_skills_list (now includes spotify)
  U->>P: Tap Spotify → Enable
  P->>S: enable_skill(abuelos, spotify)
  S->>S: edit persona.yaml + soft-reload
  S-->>P: persona_changed (still abuelos, but rebuilt)
  U->>P: Tap Spotify → Configure
  P->>S: get_skill_config_schema(spotify)
  S-->>P: { schema: {...} }
  P->>U: render form (Client ID, OAuth)
  U->>P: paste Client ID, click "Authenticate with Spotify"
  P->>U: open OAuth URL in new tab; user grants
  P->>S: set_skill_config(abuelos, spotify, {config, secrets})
  S->>S: write yaml + secrets dir, soft reload
  U->>P: PTT: "play my workout playlist"
  S->>U: 🎵
```

### Developer: sideload an in-development skill

1. Toggle Developer Mode in PWA settings.
2. Skills → Sideload → enter `huxley-skill-foo` (or `git+https://...` for unreleased).
3. Tap Install. Same flow as above, just bypasses the registry.
4. Configure + enable per persona normally.

### Maintainer: review a registry submission

1. Author opens PR to `huxley/skills` adding their entry to `index.json`.
2. CI runs:
   - JSON Schema validation against `schema/index.schema.json`.
   - PyPI existence check (the package + version are real).
   - Repo URL reachability check.
3. Mario reviews:
   - Read the linked PyPI package source (or the linked repo).
   - Skim the config schema + skill code for obviously-malicious patterns.
   - Verify it does what it says.
4. Merge → live in the registry.

## Open questions (need resolution before each phase ships)

| #   | Question                                                                    | Phase | Notes                                                                                                                                                                         |
| --- | --------------------------------------------------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OQ1 | Self-restart mechanism in dev — `os.execv` vs ask user to restart manually? | 2     | `os.execv` is universal but tricky (file handles, async loop). Manual restart is friendlier in dev. Probably: launchd for prod, `os.execv` for any "running attached" mode.   |
| OQ2 | Pip install isolation — pollute the system venv vs use a per-runtime venv?  | 2     | Today the runtime has one venv (managed by uv). Polluting it works fine for solo deployments. Multi-user same-machine setups would want isolation. Defer until pain shows up. |
| OQ3 | OAuth helper — does the SDK ship one, or do skills roll their own?          | 4     | Real OAuth needs a redirect URL handler. Cheaper v1: skill provides instructions for the user to generate a token elsewhere + paste it. Real OAuth is its own design.         |
| OQ4 | Registry schema versioning — how do we evolve `index.json`?                 | 3     | Schema has `schema_version: 1`. Plan: PWA refuses to render unknown schema versions; tells user to update. Prefer additive evolution.                                         |
| OQ5 | Concurrent skill operations — install/configure/enable arriving in parallel | 2     | Server serializes via a single asyncio Lock. Subsequent requests wait or get rejected. Document the contract.                                                                 |
| OQ6 | Uninstall when a persona has the skill enabled                              | 2     | Auto-disable from all personas before uninstall? Or refuse and tell user to disable first? Probably the former with a confirmation dialog.                                    |
| OQ7 | Skill version pinning per persona                                           | later | A persona might want spotify@0.3.0 while another wants 0.4.0. Today: not supported (one venv = one version). Document as known limitation; revisit if it bites.               |
| OQ8 | Telemetry — does the registry collect install counts?                       | later | Privacy-respecting opt-in. Defer; no infrastructure for it today.                                                                                                             |

## Risks

- **Pip-install of malicious package crashes runtime.** The runtime trusts what's installed. If a sideloaded package's `setup.py` runs `os.system("rm -rf ~")`, the user's data is gone. Mitigation: registry is curated. Sideload requires explicit dev mode. Document that sideload is at-your-own-risk.
- **Registry skill reviewer (Mario) is a single point of failure.** Mario could be slow or unavailable; submissions stall. Mitigation: enable trusted community reviewers as the ecosystem grows. Not a v1 concern.
- **OAuth complexity creep.** Real OAuth flows need redirect URL handling, token refresh, scope management. Cheap v1 (paste token) buys time but every OAuth-needing skill will want the real thing eventually.
- **Skill state migration when updating.** A skill at 0.3.0 stores data in some shape; 0.4.0 changes that shape. Today: each skill is responsible for its own migrations (per the existing T2.1 storage versioning pattern). Document the convention; provide examples.
- **PWA + runtime version skew.** A new PWA expecting `set_skill_config` against an old runtime would see protocol mismatches. Mitigation: feature-detect via `installed_skills_list` (if it doesn't reply, the runtime is old) and fall back to read-only mode.

## Build order

Each phase is independently shippable. The early phases are valuable even if the later ones never ship.

| Phase | Scope                                                                                                                                                                 | Effort    | Status          |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- | --------------- |
| 0     | T2.8 — move telegram creds to per-persona secrets dir                                                                                                                 | ~1 hour   | queued (prereq) |
| 1     | SDK additions (`config_schema`, `ctx.secrets`) + PWA Skills panel for installed skills (read/edit/enable/disable/configure) — no install or marketplace UI yet        | ~1 week   | not started     |
| 2     | Sideload install path — server `install_skill` endpoint + self-restart + PWA "Install by name" UI (developer mode)                                                    | ~1 week   | not started     |
| 3     | Curated registry — `huxley/skills` GitHub repo with `index.json` + schema + CI + PWA Marketplace tab + "Install from registry" buttons                                | ~1 week   | not started     |
| 4     | Spotify reference skill (`huxley-skill-spotify`) — canonical third-party-shaped skill, OAuth helper in the SDK if needed, end-to-end install + config + voice-control | ~3-5 days | not started     |

**Total**: ~4-5 weeks of focused work. Each phase gets its own triage entry (T1.14.1 through T1.14.4) once it begins, walking the five gates.

Phase 0 (T2.8) ships independently before T1.14 Phase 1 starts. Phases 1–4 happen in order; later phases assume the earlier ones shipped.

## Definition of Done (for the umbrella feature)

The marketplace is "shipped" when:

- [ ] T2.8 prerequisite landed.
- [ ] All four phases shipped (each closes its own DoD).
- [ ] An end-user can: open the PWA, browse Marketplace, install Spotify, configure it (paste credentials or OAuth), enable for their persona, and voice-control Spotify — without touching a terminal.
- [ ] A developer can: write a `huxley-skill-foo` package, publish to PyPI, sideload it via PWA developer mode, see + configure it, enable per persona — and optionally submit it to the registry via PR to `huxley/skills`.
- [ ] Mario can: review a registry PR, see the schema-validation CI pass, audit the linked package, merge with confidence.
- [ ] `docs/skill-marketplace.md` (this doc) reflects the shipped reality.
- [ ] Authoring guide added at `docs/skills/authoring.md` with the SDK additions + PyPI publish flow + registry submission instructions.

## References

- [`docs/triage.md` § T1.14](./triage.md) — work tracker, phase status, lessons.
- [`docs/concepts.md`](./concepts.md) — persona-as-different-person + framework vocabulary.
- [`docs/architecture.md`](./architecture.md) — runtime topology after T1.13.
- [`docs/protocol.md`](./protocol.md) — current wire contract; T1.14 phases extend it additively.
- [`docs/skills/README.md`](./skills/README.md) — how skills work today (will need updates as Phase 1 lands).
- VS Code Marketplace — model and prior art. Our curated-registry-plus-sideload shape mirrors theirs.

# Skill Marketplace

> **Status**: design phase locked. v1 (developer-primary) is feature **T1.14**. v2 (caregiver expansion) is deferred until caregiver-shipping is decided. See [`triage.md` § T1.14](./triage.md) for the work tracker.
>
> This doc is the architectural contract between developer, runtime, PWA (later), registry (later), and skill author. The v1-v2 split is structural: **SDK primitives + storage layout + persona model are shared.** v2's UI surface layers on top of v1's primitives without rewriting them.

## Vision

Huxley's load-bearing thesis is **skill extensibility** — a voice agent framework whose differentiator is "the LLM understands rough natural-language intent and dispatches to user-installable custom tools, including for personal content" (`docs/vision.md`). The marketplace is the sociotechnical layer that turns "you _can_ extend it" into "people _do_ extend it."

The shape we're building toward is **VS Code-like**: a curated registry for end-users, a sideload escape hatch for developers, in-PWA configuration. But shipping the full picture in one push commits to ~4-5 weeks of work for users (caregivers installing skills via PWA) who don't exist yet — extensibility's load-bearing-ness is proved by skills _existing_, not by their install UX.

So we ship in two cleanly-staged steps:

- **v1 — developer-primary** (~1-1.5 weeks). The SDK additions + authoring conventions that let third-party authors write `huxley-skill-*` packages. Distribution via PyPI; install via `uv add`; configuration via `persona.yaml` + a per-persona secrets dir; "registry" is a static markdown directory page in the docs site. **Earns the optionality.** If no third-party skills emerge from this, v2 was never going to land anyway.
- **v2 — caregiver expansion** (deferred). Layers a PWA Skills panel + install machinery + browseable registry on top of v1's primitives. Purely additive: new server endpoints, new PWA tabs. **No v1 rewrites.**

The v1-v2 contract — the primitives that don't change between them — is the most important architectural decision in this doc. § Cross-version contracts.

## Glossary

- **Skill author** — a developer who writes a `huxley-skill-<name>` Python package. v1 audience.
- **Caregiver / installer** — a non-technical user who'd browse + install skills via PWA. v2 audience. Distinct from the **end user** (e.g. the elderly AbuelOS user) who consumes installed skills via voice but doesn't install or configure them.
- **Install** — pip-install a `huxley-skill-<name>` package into the runtime's Python environment. Global; survives across personas.
- **Enable** — list a skill in a persona's `persona.yaml` `skills:` block so it loads when that persona is active. Per-persona.
- **Configure** — set per-skill fields (API keys, preferences, etc.) declared by the skill's `config_schema`. Per-persona (each persona's instance of a skill has its own config).
- **Secrets dir** — `<persona.data_dir>/secrets/<skill>/` — where API keys, OAuth tokens, etc. live for one persona's instance of one skill. Established by T2.8; generalized for all skills in T1.14.
- **Config schema** — JSON Schema published by a skill class declaring what fields the user must provide. **Optional**: skills with simple configs declare; skills with complex configs (i18n maps, list-of-records) leave it `None` and v2's PWA falls back to "edit YAML directly" for those.
- **Sideload** — install a skill by package name without going through a curated registry. v2 concept; v1 is sideload-only by definition (registry is just docs).

## Locked product decisions

| #   | Question                   | v1 (developer-primary)                                                              | v2 (caregiver, deferred)                                                                    |
| --- | -------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| 1   | Audience                   | Developer / contributor / self-hosting installer.                                   | Caregiver-installer (the elderly end user is not the installer either way).                 |
| 2   | Distribution               | PyPI public packages (`huxley-skill-*`). Optionally `git+https://...` for private.  | Same, plus a curated registry that points at PyPI entries.                                  |
| 3   | Discovery                  | Static markdown page (`docs/skills/`) listing known packages.                       | Browseable Marketplace tab in PWA, fetches from `huxley/skills` GitHub repo's `index.json`. |
| 4   | Install                    | `uv add huxley-skill-foo` + manual restart.                                         | PWA "Install" button → server `pip install` + self-restart machinery.                       |
| 5   | Configure                  | Edit `persona.yaml` + drop secrets in `<persona>/data/secrets/<skill>/values.json`. | PWA renders form from `config_schema`, writes config to YAML + secrets to dir.              |
| 6   | Per-persona enable/disable | Edit `persona.yaml` `skills:` list.                                                 | PWA toggle.                                                                                 |
| 7   | Trust                      | Author-trusted (read the source).                                                   | Registry-curated (Mario gatekeeps via PR). User owns their installs (incl. sideloads).      |

**Decisions deferred to v2 design phase**:

- The full PWA Skills panel UX shape.
- The `os.execv`-vs-launchd self-restart mechanism (and its handling of in-flight SQLite WAL, OpenAI sessions, partial pip install brick, C-extension compile time).
- Curated registry tier system (official vs community), submission CI, delisting mechanism, maintainer-load mitigations.
- Real OAuth flow vs paste-token-from-elsewhere.
- Dep-conflict resolution strategy.
- Schema-evolution UX for skills (currently: `data_schema_version` declared, runtime logs on mismatch).

These are not punted because they don't matter — they matter a lot. They're punted because committing to specific shapes for them in v1 forecloses better answers v2 can find with the SDK primitives in production hands.

## Architecture overview

```
┌──────────────────────────────────────────────────────────────────────┐
│ V1 — DEVELOPER-PRIMARY                                               │
│                                                                      │
│  Skill author flow:                                                  │
│  1. Write huxley-skill-foo using huxley_sdk                          │
│  2. Declare config_schema (or leave None) + use ctx.secrets API      │
│  3. pip-publish to PyPI (or git+https:// for private)                │
│  4. Optional: PR to docs/skills/ index page                          │
│                                                                      │
│  Installer (developer / self-hosting caregiver) flow:                │
│  1. uv add huxley-skill-foo                                          │
│  2. Edit personas/<name>/persona.yaml: skills.foo: { ... }           │
│  3. mkdir -p personas/<name>/data/secrets/foo/                       │
│  4. Drop API keys / tokens into values.json                          │
│  5. Restart: cd server/runtime && uv run huxley                      │
│                                                                      │
│  Runtime:                                                            │
│  • Existing entry-point loading (no changes)                         │
│  • New SDK: Skill.config_schema, ctx.secrets, data_schema_version    │
│  • Existing per-persona DB + namespaced storage                      │
│  • Existing T1.13 hot persona swap                                   │
└──────────────────────────────────────────────────────────────────────┘

                                   │
                                   │  v2 layers on top of v1 primitives
                                   ▼

┌──────────────────────────────────────────────────────────────────────┐
│ V2 — CAREGIVER EXPANSION (deferred — purely additive)                │
│                                                                      │
│   PWA Skills panel                                                   │
│     ↕ new WS endpoints (list_installed / get_config / set_config /   │
│       enable / disable / install / uninstall)                        │
│   Self-restart machinery (the hard part — its own design pass)       │
│   Curated registry (huxley/skills GitHub repo, JSON index, CI)       │
│   PWA Marketplace tab                                                │
│                                                                      │
│   None of this requires SDK changes, persona-model changes, or       │
│   storage-layout changes from v1.                                    │
└──────────────────────────────────────────────────────────────────────┘
```

## Cross-version contracts

These are the v1 deliverables that v2 _must not_ rewrite. Get them right now.

### SDK additions

```python
class Skill(Protocol):
    name: ClassVar[str]

    # NEW (T1.14): JSON Schema describing this skill's per-persona config.
    # OPTIONAL — None means "no PWA form will render; YAML/manual config only."
    # Skills with simple string-or-bool configs declare; skills with
    # nested i18n maps, list-of-records, or skill-specific UX (Telegram
    # SMS auth flow) leave it None. v2's PWA only renders forms for
    # opt-in skills; complex skills get a "manual config required" UI
    # with a link to the skill's docs.
    config_schema: ClassVar[dict | None] = None

    # NEW (T1.14): integer version of this skill's persisted data layout
    # (the storage namespace + the secrets values). Bump on incompatible
    # change. Runtime logs a loud warning when an installed skill's
    # version doesn't match what's on disk; v2 will gate cross-major
    # upgrades behind explicit user confirmation.
    data_schema_version: ClassVar[int] = 1

    # ... existing methods unchanged (setup, tools, dispatch, prompt_context,
    #     teardown, reconfigure)
```

```python
@dataclass
class SkillContext:
    # ... existing fields unchanged

    # NEW (T1.14): per-skill secrets store. Backed by a JSON file at
    # <persona_data_dir>/secrets/<skill>/values.json. Skills SHOULD use
    # this for API keys, OAuth tokens, anything that must NOT land in
    # persona.yaml.
    secrets: SkillSecrets


class SkillSecrets(Protocol):
    """Per-skill secrets store. v1: string values only. v2 may add
    `set_json` / `get_json` for nested OAuth state (refresh tokens,
    expires_at) — additive, doesn't break v1 callers.

    Today's escape hatch for OAuth: skills JSON-encode the dict
    themselves and pass the string in. Documented in authoring.md."""

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def keys(self) -> list[str]: ...
```

### Secrets storage layout

```
<persona.data_dir>/
└── secrets/
    └── <skill_name>/
        ├── values.json     # {"key": "value"}, all strings
        └── README.md       # auto-generated, explains contents
```

**Permissions**: `0700` on the secrets dir, `0600` on `values.json`. Filesystem-level only — no encryption-at-rest in v1. For self-hosted home Pi deployments this is defensible. v2 can layer encryption when shared-machine or backup-sync deployments emerge.

**Sync warning**: secrets travel with the persona's data dir. If users back up `~/huxley-grandpa/` to iCloud/Dropbox/etc., the cleartext secrets travel too. The authoring docs and the persona-bootstrap docs will say "don't sync the secrets dir" and the persona-init scaffolder (when we ship one) will drop a `.gitignore` / `.icloud-noopt` in there.

### Config schema convention (when a skill opts in)

JSON Schema 2020-12 with two custom extensions:

- `"format": "secret"` on a string field → routes the value to the secrets dir, never to YAML. PWA (v2) renders password input.
- `"x-huxley:help"` → markdown help text. PWA (v2) renders alongside the field.

Minimal example:

```json
{
  "type": "object",
  "required": ["client_id"],
  "properties": {
    "client_id": {
      "type": "string",
      "title": "Client ID",
      "description": "From your Spotify Developer Dashboard."
    },
    "access_token": {
      "type": "string",
      "format": "secret",
      "title": "Access token"
    }
  }
}
```

**Skills with complex configs leave `config_schema = None`.** The audiobooks skill's per-language i18n maps + the telegram skill's contacts dict don't fit into a JSON-Schema-rendered form. That's fine; those configs stay in `persona.yaml` (where they already are) and v2's PWA shows "this skill has no PWA form — see [skill docs] to configure manually."

### Persona.yaml stays the source of truth

For both v1 and v2:

```yaml
skills:
  spotify:
    client_id: 8a3c... # plain config in YAML
    # secrets in <persona>/data/secrets/spotify/values.json — never YAML
```

v1: developer hand-edits this. v2: PWA writes to it. Same schema; only the editor differs.

## v1 components

### SDK additions (Phase 1)

`huxley_sdk` adds `Skill.config_schema`, `Skill.data_schema_version`, `SkillContext.secrets`, and the `SkillSecrets` protocol. ~150 LOC of Python + tests + docs. Backward compat: existing skills opt out by default (config_schema=None, data_schema_version=1, secrets unused). Runtime no-ops on opt-out — v1 is fully usable without any skill adopting these.

### Spotify reference skill (Phase 2)

`huxley-skill-spotify` lives in its own repo (or `server/skills/spotify/` initially), pip-installable from PyPI. The canonical worked example for what a third-party skill looks like:

- Declares `config_schema` (Client ID + access_token-as-secret).
- Uses `ctx.secrets` for the access token.
- v1 OAuth UX: skill exposes `prompt_context()` that says "to use Spotify, generate a token at https://... and run `huxley-skill-spotify auth` to paste it" — the auth flow is a CLI command the developer/installer runs, not in-PWA. **No real OAuth flow in v1.**
- Authoring docs reference this as the worked example.

This pressure-tests the SDK additions on a real third-party-shaped skill before v2 commits to JSON-Schema-rendered forms or a registry shape.

### Authoring docs (Phase 3)

`docs/skills/authoring.md` — how to write a `huxley-skill-foo` package:

- Project structure (`pyproject.toml` with the entry point, `src/huxley_skill_foo/skill.py`).
- The `Skill` protocol — what to implement.
- Config schema convention (when to declare, when to leave None).
- Secrets API — `ctx.secrets.get/set/delete`.
- Persona integration — what users add to `persona.yaml`.
- Publishing to PyPI.
- Submitting to the static directory page.

### Static directory page (Phase 4)

`docs/skills/index.md` — a curated list of known `huxley-skill-*` packages. Each entry: name, description, install command, link to author docs. Tracked in git; PRs add/remove entries; renderable by Fumadocs in the docs site.

For v1 this **is** the registry. No JSON, no separate repo, no CI. v2 promotes this to a structured registry.

## v1 user flows

### Skill author publishes a new skill

1. Create `huxley-skill-mytool` package per `docs/skills/authoring.md` template.
2. Implement `Skill` protocol. Optionally declare `config_schema`.
3. Use `ctx.secrets.set/get` for any API keys / tokens.
4. `uv build` + publish to PyPI.
5. PR to Huxley repo adding entry to `docs/skills/index.md`.
6. Mario reviews the PR (skim the source on PyPI, verify the schema). Merge.

### Installer adds a skill to a persona

1. `cd ~/huxley-grandpa && uv add huxley-skill-mytool`.
2. Edit `personas/abuelos/persona.yaml`:
   ```yaml
   skills:
     mytool:
       some_option: value
   ```
3. (If the skill needs secrets) `mkdir -p personas/abuelos/data/secrets/mytool && cat > personas/abuelos/data/secrets/mytool/values.json` with `{"api_key": "..."}`.
4. Restart Huxley.
5. PTT: "use mytool to do thing." LLM dispatches.

### Installer updates an existing skill

1. `uv lock --upgrade-package huxley-skill-mytool`.
2. Restart.
3. Runtime logs a loud warning if `data_schema_version` doesn't match what's on disk; installer reads the skill's CHANGELOG to see if migration is needed.

## v1 build order

| #   | Triage entry | Scope                                                                                                                                                                                                                                                         | Effort     | Status          |
| --- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | --------------- |
| 0   | T2.8         | Move telegram creds to `<persona>/data/secrets/telegram/`. Establishes the secrets-dir pattern T1.14 generalizes.                                                                                                                                             | ~1 hour    | queued (prereq) |
| 1   | T1.14        | SDK additions (`Skill.config_schema`, `Skill.data_schema_version`, `ctx.secrets`) + `huxley-skill-spotify` reference + `docs/skills/authoring.md` + `docs/skills/index.md` directory page. **Three phases tracked inside one entry** per critic-round-2 § 12. | ~1.5 weeks | not started     |

That's the entirety of v1. ~1.5 weeks of focused work.

## v1 Definition of Done

- [ ] T2.8 prerequisite landed.
- [ ] SDK additions shipped (with tests + mypy + docstrings).
- [ ] At least one first-party skill adopts `config_schema` to validate the convention end-to-end.
- [ ] `huxley-skill-spotify` exists, is pip-installable, has a config_schema, uses ctx.secrets, has a working voice-control demo path. Lives at `server/skills/spotify/` or its own repo — decide during implementation.
- [ ] `docs/skills/authoring.md` published with the worked-example walkthrough.
- [ ] `docs/skills/index.md` published with at least the first-party skills listed and a "submit a PR to add yours" footer.
- [ ] Mario smoke: install `huxley-skill-spotify` into a fresh persona via the documented path; PTT-control Spotify; verify secrets land in the secrets dir + don't leak into git diffs.
- [ ] `ruff check server/` + `mypy server/sdk/src server/runtime/src` + per-package pytest all green.

When the above is true, **v1 marketplace is shipped**. Decision to start v2 is independent and depends on whether real third-party skills emerge.

## v2 — caregiver expansion (deferred)

Documented here for continuity; **not committed to**. Filed as triage entries when caregiver-shipping is decided.

### What v2 adds (purely on top of v1)

| Capability             | Triage (when filed) | New surface                                                                                                                                       | Touches v1?                                                                    |
| ---------------------- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| PWA Skills panel       | T1.X                | New WS endpoints + new PWA sheet that reads installed skills, renders forms from `config_schema`, writes config + secrets, toggles enable/disable | No SDK changes; reads what v1 declared                                         |
| Self-restart machinery | T1.X+1              | `install_skill` server endpoint + pip orchestration + atomic-swap-venv approach                                                                   | None                                                                           |
| Curated registry       | T1.X+2              | `huxley/skills` GitHub repo with `index.json` + JSON Schema + CI + tier system + delisting mechanism + PWA Marketplace tab                        | None — registry replaces the markdown page; markdown page can stay or redirect |
| Real OAuth             | T1.X+3              | OAuth helper in the SDK; redirect URL handler in the runtime; PWA "Authenticate with X" button                                                    | Additive — skills opt in                                                       |

### v2 open questions (already known, defer to v2 design phase)

- **Self-restart safety** — `os.execv` has real foot-guns: SQLite WAL torn state on a half-flushed write; bricked venv on partial pip install with no recovery UI; C-extension compile time on a Pi can be 60-90s, breaking the "be right back" UX. Mitigation likely needs staged-venv-and-swap.
- **JSON Schema doesn't fit complex configs** — i18n maps, contact lists, list-of-records render as nested-accordion forms that are unusable. v1's "config_schema is optional" already softens this; v2's PWA needs a graceful "this skill needs manual config" path.
- **Registry scaling** — flat JSON works for ≤100 entries; past that needs facets (categories, ratings), search, install counts.
- **Maintainer load** — Mario reviewing every PR doesn't scale past ~5/week. Tier system (official vs community-auto-merge-if-CI-passes) is the likely answer.
- **Distribution gaps** — private/paid skills (git+ssh URLs, license tokens), version skew (registry says 0.3.0, user has 0.4.0), dep conflicts (skill A vs B on httpx versions).
- **Concurrent install requests** — single asyncio Lock; document the contract.
- **Uninstall while persona has skill enabled** — auto-disable from all personas first, with confirmation.
- **Telegram-style first-time-auth** — SMS code, OAuth redirect, etc. don't fit JSON Schema. v2 needs a "skill provides its own wizard" escape hatch.
- **Interaction with T1.13 hot swap** — pip install + restart is more violent than persona swap; needs the same `_shutting_down` rigor + a persona-swap-during-install integration test.
- **Interaction with proactive turns** — in-flight `inject_turn` from a skill being uninstalled is lost on restart. Document or mitigate.

These are all real concerns — but each is a v2-phase design decision, not a v1 blocker.

## Risks

### v1 risks

- **Nobody writes a third-party `huxley-skill-*`.** The biggest risk: v1 ships, no community materializes, v2 was always going to be vapor. Mitigation: build `huxley-skill-spotify` ourselves as the existence proof + a high-quality authoring guide. If even with that, no external skills emerge in 3 months, the "marketplace" thesis is wrong and we don't owe v2.
- **Authoring docs go stale.** The SDK is in flight; docs lag. Mitigation: tie authoring-docs updates to SDK changes via a doc lint check or a triage convention.
- **`huxley-skill-spotify` becomes a maintenance burden.** OAuth tokens expire; Spotify API changes. Mitigation: ship the v1 with a "paste a long-lived token" UX; punt token refresh to v2's real-OAuth design.

### v2 risks (deferred but documented)

- Self-restart `os.execv` partial-failure mode bricks the venv with no UI to recover.
- Auto-form-rendering breaks for complex skill configs.
- Registry maintainer load.
- Dep-conflict between skills.
- Skill data migration on upgrade for non-developer users.

## References

- [`docs/triage.md` § T1.14](./triage.md) — work tracker, status, lessons.
- [`docs/triage.md` § T2.8](./triage.md) — prerequisite, queued.
- [`docs/concepts.md`](./concepts.md) — persona-as-different-person + framework vocabulary.
- [`docs/architecture.md`](./architecture.md) — runtime topology after T1.13.
- [`docs/protocol.md`](./protocol.md) — wire contract; v2 phases extend additively.
- [`docs/skills/README.md`](./skills/README.md) — current skills documentation; updated as Phase 1 ships.
- VS Code Marketplace — model and prior art for v2's curated-registry-plus-sideload shape.

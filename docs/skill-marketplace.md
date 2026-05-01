# Skill Marketplace

> **Status**: design phase locked (post-critic-round-2). v1 (developer-primary) is feature **T1.14**. v2 (caregiver expansion) is deferred until caregiver-shipping is decided. See [`triage.md` § T1.14](./triage.md) for the work tracker.

## Vision

Huxley's load-bearing thesis is **skill extensibility** — a voice agent framework whose differentiator is "the LLM understands rough natural-language intent and dispatches to user-installable custom tools" (`docs/vision.md`). The marketplace is the layer that turns "you _can_ extend it" into "people _do_ extend it."

The shape we're building toward is **VS Code-like**: curated registry for end-users, sideload escape hatch for developers, in-PWA configuration. We ship in two cleanly-staged steps:

- **v1 — developer-primary** (~2 weeks). SDK additions + authoring conventions that let third-party authors write `huxley-skill-*` packages. Distribution via PyPI; install via `uv add`; configuration via `persona.yaml` + a per-persona secrets dir; "registry" is a static markdown directory page in the docs site. **Earns the optionality.**
- **v2 — caregiver expansion** (deferred). Layers a PWA Skills panel + install machinery + browseable registry on top of v1's primitives. Purely additive: new server endpoints, new PWA tabs. **No v1 rewrites.**

§ Cross-version contracts pins what doesn't change between them.

## Glossary

- **Skill author** — developer who writes a `huxley-skill-<name>` Python package. v1 audience.
- **Caregiver / installer** — non-technical user who'd browse + install skills via PWA. v2 audience. Distinct from the **end user** (e.g. the elderly AbuelOS user) who consumes skills via voice but doesn't install or configure them.
- **Install** — `uv add` a `huxley-skill-<name>` package into the runtime's shared workspace venv. Global; survives across personas. Per-persona scoping is via `persona.yaml.skills:` enable lists, **not** separate venvs (see § Privacy carve-out for T1.13).
- **Enable** — list a skill in a persona's `persona.yaml` `skills:` block so it loads when that persona is active. Per-persona.
- **Configure** — set per-skill fields (API keys, preferences, etc.) declared by the skill's `config_schema`. Per-persona (each persona's instance of a skill has its own config).
- **Secrets dir** — `<persona.data_dir>/secrets/<skill>/` — where API keys, OAuth tokens, etc. live for one persona's instance of one skill. Established by T2.8; generalized for all skills in T1.14.
- **Config schema** — JSON Schema published by a skill class declaring what fields the user must provide. **Optional**: skills with simple configs declare; skills with complex configs (i18n maps, list-of-records) leave it `None` and v2's PWA falls back to "edit YAML directly."
- **Sideload** — install a skill by package name without going through a curated registry. v2 concept; v1 is sideload-only by definition.

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

- Full PWA Skills panel UX shape.
- `os.execv`-vs-launchd self-restart mechanism (and its handling of in-flight SQLite WAL, OpenAI sessions, partial pip install brick, C-extension compile time).
- Curated registry tier system, submission CI, delisting mechanism.
- Real OAuth flow vs paste-token-from-elsewhere.
- Dep-conflict resolution strategy.
- `set_json/get_json` typed accessors for nested secret blobs (sugar over v1's flat `str → str` shape; see § Secrets storage layout).

## Architecture overview

```
V1 — DEVELOPER-PRIMARY

  Skill author flow:
  1. Write huxley-skill-foo using huxley_sdk (own repo from day 1)
  2. Declare config_schema (or leave None) + use ctx.secrets API
  3. pip-publish to PyPI (or git+https:// for private)
  4. Optional: PR to docs/skills/ index page

  Installer (developer / self-hosting caregiver) flow:
  1. uv add huxley-skill-foo  (lands in shared workspace venv)
  2. Edit personas/<name>/persona.yaml: skills.foo: { ... }  (per-persona enable)
  3. mkdir -p personas/<name>/data/secrets/foo/
  4. Drop API keys / tokens into values.json
  5. Restart: cd server/runtime && uv run huxley
       (or `launchctl kickstart -k gui/$UID/com.huxley.<persona>` for launchd-supervised)

  Runtime:
  • Existing entry-point loading (no changes)
  • New SDK: Skill.config_schema, ctx.secrets, data_schema_version
  • Existing per-persona DB + namespaced storage; schema_meta persists
    per-skill data_schema_version
  • Existing T1.13 hot persona swap; venv stays shared

V2 — CAREGIVER EXPANSION (deferred — purely additive)

  PWA Skills panel
    ↕ new WS endpoints (list_installed / get_config / set_config /
      enable / disable / install / uninstall)
  Self-restart machinery (the hard part — its own design pass)
  Curated registry (huxley/skills GitHub repo, JSON index, CI)
  PWA Marketplace tab
  set_json/get_json sugar on SkillSecrets

  None of this requires SDK changes (only additions), persona-model
  changes, or storage-layout changes from v1.
```

## Cross-version contracts

These are the v1 deliverables v2 _must not_ rewrite. Get them right now.

### SDK additions

```python
class Skill(Protocol):
    name: ClassVar[str]

    # NEW (T1.14): JSON Schema describing this skill's per-persona config.
    # OPTIONAL — None means "no PWA form will render; YAML/manual config only."
    # Skills with simple string-or-bool configs declare; skills with
    # nested i18n maps, list-of-records, or skill-specific UX (Telegram
    # SMS auth flow) leave it None. v2's PWA only renders forms for
    # opt-in skills; complex skills get a "manual config required" UI.
    config_schema: ClassVar[dict | None] = None

    # NEW (T1.14): integer version of this skill's persisted data layout
    # (storage namespace + secrets values). Bump on incompatible change.
    # Persisted in the persona's schema_meta table under key
    # `skill_version:<name>`. Runtime logs a loud warning when an
    # installed skill's declared version doesn't match what's on disk.
    # See § Schema versioning.
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
    # persona.yaml. Async to match SkillStorage — both are filesystem
    # I/O called from skill.setup() / skill.dispatch() coroutines.
    secrets: SkillSecrets


class SkillSecrets(Protocol):
    """Per-skill secrets store. v1: string values only.

    The on-disk shape is ALWAYS a flat dict[str, str]. Skills that need
    nested data (OAuth refresh state with access_token + refresh_token +
    expires_at) JSON-encode the dict themselves into a single key like
    `oauth_state`. Documented in authoring.md.

    v2 may add `set_json(key, value: dict)` / `get_json(key) -> dict` as
    sugar over the same on-disk shape — implementation calls
    `set/get` with json.dumps/loads. This is purely additive: v1
    values written as JSON-strings into the flat dict ARE the same
    on-disk bytes v2 would write via set_json. No migration."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def keys(self) -> list[str]: ...
```

### Secrets storage layout

```
<persona.data_dir>/
└── secrets/
    └── <skill_name>/
        ├── values.json     # {"key": "value"}, all strings (flat)
        └── README.md       # auto-generated, explains contents
```

**Permissions**: `0700` on the secrets dir, `0600` on `values.json`. Filesystem-level only — no encryption-at-rest in v1. For self-hosted home Pi deployments this is defensible. v2 can layer encryption when shared-machine or backup-sync deployments emerge.

**Git ignore**: secrets land at `<persona>/data/secrets/<skill>/values.json`. `<persona>/data/` is **already gitignored** at the repo level (see existing `personas/abuelos/data/` exclusion — audiobook library + DB). v1 needs **no new `.gitignore` rule**; the secrets dir inherits the existing exclusion. The smoke-test step "verify secrets do not leak into git diffs" verifies this is still true after the dir is created.

**Sync warning**: secrets travel with the persona's data dir. The authoring docs and the persona-bootstrap docs say "don't sync the secrets dir" and the persona-init scaffolder (when we ship one) drops a `.gitignore` / `.icloud-noopt` in there.

**OAuth-blob convention** (locked in v1, will not change in v2):

Skills that need to persist nested OAuth state — `{access_token, refresh_token, expires_at, scope}` — encode the dict themselves and store under one key:

```python
# In a skill that uses OAuth:
import json

state = {"access_token": "...", "refresh_token": "...", "expires_at": 1735689600}
await ctx.secrets.set("oauth_state", json.dumps(state))

# Reading:
raw = await ctx.secrets.get("oauth_state")
state = json.loads(raw) if raw else None
```

When v2 adds `set_json/get_json`, those methods perform the same `json.dumps/json.loads` internally and write to the same flat-string dict. **The on-disk bytes are identical** between v1's manual encoding and v2's typed accessor — that's what makes v2 additive instead of migratory.

**Corruption recovery**: skills are responsible for `try: json.loads(raw) except json.JSONDecodeError: return None` on read. Recovery from a corrupted blob is `await ctx.secrets.delete("oauth_state")` followed by re-auth. v1 does not auto-quarantine corrupted values; the skill's own retry logic owns that decision.

### Schema versioning (`data_schema_version`)

Each skill declares `data_schema_version: ClassVar[int] = N`. Persisted in the existing per-persona `schema_meta` table (already used by the runtime for its own schema version) under key `skill_version:<skill_name>`.

**Behavior on mismatch**:

- On skill setup, runtime reads `schema_meta.skill_version:<name>`.
- If absent: write current declared version. (First boot for that skill on that persona.)
- If equal: **silent no-op — no event emitted**. T1.13 swaps `setup()` on every persona reconnect; emitting an info-level "checked" event would create log noise on every swap.
- If declared > stored (upgrade): log a `warning` event `skill.schema.upgrade_needed` with both versions. **v1 does not auto-migrate**; the skill author's CHANGELOG instructs the user. After successful skill setup, write the new version. v2's PWA gates cross-major upgrades behind explicit user confirmation; v1 just logs and proceeds.
- If declared < stored (downgrade): log a `warning` event `skill.schema.downgrade_detected`. Skill loads anyway (v1 trusts the developer); v2's PWA refuses without explicit confirmation.

**Atomicity under hot persona swap (T1.13)**: the version-write happens in the same DB transaction as any other skill-init writes for that persona. A torn swap (teardown interrupted mid-init) leaves the prior version intact in `schema_meta`, so the next swap re-runs first-boot semantics correctly. DoD test: swap personas 3× consecutively — `skill.schema.*` events fire only on the first boot of each (skill, persona) pair; subsequent swaps are silent.

This pins the storage location and behavior _now_ so v2's UX layer can make decisions on top without changing v1's on-disk format.

### Config schema convention (when a skill opts in)

JSON Schema 2020-12 with two custom extensions:

- `"format": "secret"` on a string field → routes the value to the secrets dir, never to YAML. PWA (v2) renders password input.
- `"x-huxley:help"` → markdown help text. PWA (v2) renders alongside the field.

Minimal example (for `huxley-skill-stocks`):

```json
{
  "type": "object",
  "required": ["api_key"],
  "properties": {
    "api_key": {
      "type": "string",
      "format": "secret",
      "title": "Alpha Vantage API key",
      "x-huxley:help": "Get a free key at https://www.alphavantage.co/support/#api-key"
    },
    "watchlist": {
      "type": "array",
      "items": { "type": "string" },
      "title": "Default watchlist",
      "description": "Ticker symbols to summarize when the user asks 'how's my watchlist'."
    },
    "currency": {
      "type": "string",
      "enum": ["USD", "EUR", "GBP", "JPY"],
      "default": "USD"
    }
  }
}
```

This single example exercises all three of the JSON-Schema shapes the v2 PWA form-renderer must support: secret string, array, enum.

**Skills with complex configs leave `config_schema = None`.** Audiobooks' per-language i18n maps + telegram's contacts dict don't fit JSON-Schema-rendered forms. Those configs stay in `persona.yaml` and v2's PWA shows "this skill has no PWA form — see [skill docs] to configure manually."

**Schemas describe the post-merge view of `ctx.config`.** The runtime already merges per-language i18n maps from `skills.<name>.i18n.<lang>` into `ctx.config` before the skill sees it (see `SkillContext.config` docstring). A `config_schema` describes the post-merge shape the skill consumes — not the pre-merge YAML structure. Skills that use the i18n merge stay schemaless; declaring a schema for the merged shape would mislead v2's form-renderer into writing un-mergeable flat values.

### Privacy carve-out for T1.13 (shared workspace venv)

T1.13's persona model promises filesystem-enforced privacy: each persona's data dir is private; persona A cannot read persona B's audiobook positions or telegram threads. **That guarantee is preserved.** Per-persona privacy lives in:

- The persona's data dir (`<persona.data_dir>/`) — DBs, secrets, downloaded media.
- The persona's `persona.yaml` `skills:` enable list.

It does **not** live in the Python venv. v1 ships skills into the shared `uv` workspace venv. Two personas that both enable `huxley-skill-foo` share the same Python module; they get separate `SkillContext` instances (separate storage, separate secrets, separate config), but the code is the same import. This is correct: the venv is a code distribution mechanism, not a trust boundary.

If persona A enables a skill and persona B doesn't, persona B's runtime never imports the skill's setup code — no in-memory state, no API calls, nothing. The enable list in `persona.yaml` IS the trust boundary at runtime.

v2 may revisit (e.g. per-persona venvs for fully untrusted third-party skills) but only when the threat model demands it. v1 does not.

### Persona.yaml as the source of truth

For both v1 and v2:

```yaml
skills:
  stocks:
    watchlist: ["AAPL", "MSFT", "GOOG"] # plain config in YAML
    currency: USD
    # api_key in <persona>/data/secrets/stocks/values.json — never YAML
```

v1: developer hand-edits this. v2: PWA writes to it using `ruamel.yaml` (round-trip preserves comments + ordering). Same schema; only the editor differs.

## v1 components

### SDK additions (Phase 1)

`huxley_sdk` adds `Skill.config_schema`, `Skill.data_schema_version`, `SkillContext.secrets`, and the `SkillSecrets` protocol. ~150 LOC of Python + tests + docs. Backward compat: existing skills opt out by default (config_schema=None, data_schema_version=1, secrets unused). Runtime no-ops on opt-out — v1 is fully usable without any skill adopting these.

**First-party config_schema adopter**: `huxley-skill-search` (DuckDuckGo via ddgs; no API key). The schema scopes to **today's actual user-tunable field**: `safesearch: "moderate" | "off" | "strict"` (enum). Validates the JSON-Schema convention end-to-end without dragging in API-key routing on a first-party skill, and without conflating "adopt schema" with "add new functional config" in the same commit.

`start_sound` and `sounds_path` are persona-author / framework-shared fields (sounds palette plumbing), not user-tunable. They stay un-schemaed; v2's PWA does not render them.

### Stocks reference skill (Phase 2)

`huxley-skill-stocks` lives in **its own repo from day 1** (not `server/skills/`). The first canonical example of what a third-party skill looks like:

- Built against `huxley_sdk` (during v1 development pinned as a `uv` path dep against the Huxley repo's main; once Phase 1 publishes `huxley-sdk` to PyPI, switches to a versioned pin — and that's the install path the authoring docs document for external authors).
- Declares `config_schema` with `api_key` (secret, Alpha Vantage), `watchlist` (array of tickers), `currency` (enum). Single example covers all three of the JSON-Schema shapes v2's form-renderer must support.
- Uses `ctx.secrets` for the API key; `ctx.config` for watchlist + currency.
- Voice tools: "what's Apple stock at?" / "how's my watchlist doing?" / "did the S&P close up today?"
- Authoring docs walk through this exact package as the worked example.
- Repo CI: `ruff` + `mypy --strict` + `pytest` + a publishing GitHub Action template (so authors copying it have a reference). Out of scope: code coverage gates, release signing, distribution-channel automation beyond PyPI.

**Why stocks, not Spotify**: Spotify access tokens expire in 1 hour. Building v1's demo on a skill where the secret rotates every hour forces either real OAuth refresh into v1 (out of v1 scope per locked decisions table) or a "regenerate your token every hour" UX (hostile). Alpha Vantage uses long-lived API keys — pure config + secrets, no token-lifecycle complexity.

**Why stocks, not weather**: Huxley already ships `huxley-skill-news` which talks to Open-Meteo for forecasts; a "weather" reference skill would overlap with existing first-party functionality. Stocks is a clean greenfield demo with no overlap, and Alpha Vantage's free tier _requires_ a key (some weather providers don't), which makes it a stronger test of the secrets-routing convention.

This pressure-tests the SDK additions on a real third-party-shaped skill before v2 commits to JSON-Schema-rendered forms or a registry shape.

### Authoring docs (Phase 3)

`docs/skills/authoring.md` — how to write a `huxley-skill-foo` package:

- Project structure (`pyproject.toml` with the entry point, `src/huxley_skill_foo/skill.py`).
- The `Skill` protocol — what to implement.
- Config schema convention (when to declare, when to leave None).
- Secrets API — `ctx.secrets.get/set/delete` (async).
- OAuth-blob convention (json-encode dicts into a single key).
- Persona integration — what users add to `persona.yaml`.
- Publishing to PyPI.
- Submitting to the static directory page.

### Static directory page (Phase 4)

`docs/skills/index.md` — curated list of known `huxley-skill-*` packages. Tracked in git; PRs add/remove entries; rendered by Fumadocs.

**Per-entry metadata schema** (so the PR template is mechanical):

| Field         | Type                              | Example                                     |
| ------------- | --------------------------------- | ------------------------------------------- |
| `name`        | string (PyPI package name)        | `huxley-skill-stocks`                       |
| `description` | one sentence                      | "Real-time stock quotes via Alpha Vantage." |
| `install`     | code-block, exact command         | `uv add huxley-skill-stocks`                |
| `docs_url`    | URL                               | `https://github.com/huxley-skills/stocks`   |
| `tier`        | enum: `first-party` / `community` | `community`                                 |

For v1 this **is** the registry. No JSON, no separate repo, no CI. v2 promotes this to a structured registry. (Auto-aggregating the index from per-skill frontmatter is a v2 nice-to-have, not v1 scope.)

**Inclusion bar**: Mario reviews each PR. Minimum: package installs cleanly, declares `config_schema` if it has user-tunable fields, has a public docs page with at least the install command and one example voice intent. Mario can ask for changes or decline; the bar is "is this a credible third-party skill" not "is this a perfect one."

## v1 user flows

### Skill author publishes a new skill

1. Create `huxley-skill-mytool` package per `docs/skills/authoring.md` template.
2. Implement `Skill` protocol. Optionally declare `config_schema`.
3. Use `ctx.secrets.set/get` for any API keys / tokens.
4. `uv build` + publish to PyPI.
5. PR to Huxley repo adding entry to `docs/skills/index.md`.
6. Mario reviews the PR (skim the source on PyPI, verify the schema). Merge.

### Installer adds a skill to a persona

1. `cd ~/huxley-grandpa && uv add huxley-skill-mytool`. (Lands in shared workspace venv.)
2. Edit `personas/abuelos/persona.yaml`:
   ```yaml
   skills:
     mytool:
       some_option: value
   ```
3. (If the skill needs secrets) `mkdir -p personas/abuelos/data/secrets/mytool && cat > personas/abuelos/data/secrets/mytool/values.json` with `{"api_key": "..."}`.
4. Restart Huxley.
   - Single-instance dev: `cd server/runtime && uv run huxley`.
   - launchd-supervised production: `launchctl kickstart -k gui/$UID/com.huxley.<persona>` (or just stop/start the agent).
   - Multi-instance (Abuelo + Basic side-by-side per T1.13): each instance is its own process; restart each that has the new skill enabled.
5. PTT: "use mytool to do thing." LLM dispatches.

### Installer updates an existing skill

1. `uv lock --upgrade-package huxley-skill-mytool`.
2. Restart (per above).
3. Runtime logs `skill.schema.upgrade_needed` if `data_schema_version` bumped; installer reads CHANGELOG for migration steps.

## v1 build order

| #   | Triage entry | Scope                                                                                                                                                                                                                      | Effort   | Status          |
| --- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | --------------- |
| 0   | T2.8         | Move telegram creds to `<persona>/data/secrets/telegram/`. Establishes the secrets-dir pattern T1.14 generalizes.                                                                                                          | ~1 hour  | queued (prereq) |
| 1   | T1.14        | SDK additions (`Skill.config_schema`, `Skill.data_schema_version`, async `ctx.secrets`, `schema_meta` persistence) + `huxley-skill-search` first-party adopter + `huxley-skill-stocks` reference + authoring docs + index. | ~2 weeks | not started     |

That's the entirety of v1. Tracked as a single triage entry with phase checkboxes (per critic round 1 §12).

## v1 Definition of Done

- [ ] T2.8 prerequisite landed.
- [ ] SDK additions shipped (with tests + mypy + docstrings). `SkillSecrets` is async to match `SkillStorage`.
- [ ] `data_schema_version` persists in `schema_meta` under `skill_version:<name>`; mismatch behavior matches § Schema versioning (warning logs, no auto-migration in v1).
- [ ] `huxley-skill-search` adopts `config_schema` to validate the convention end-to-end on a first-party skill.
- [ ] `huxley-skill-stocks` exists in its own repo, is pip-installable from PyPI, declares `config_schema` (api_key as secret + watchlist array + currency enum), uses async `ctx.secrets` for the API key. Working voice-control demo path.
- [ ] `docs/skills/authoring.md` published with the worked-example walkthrough (`huxley-skill-stocks` as the canonical example).
- [ ] **Authoring-docs self-test**: walking the docs verbatim from a clean checkout produces a working `huxley-skill-stocks` install. Verified by Mario on a fresh persona — if a step doesn't work as written, the doc is the bug.
- [ ] `docs/skills/index.md` published with first-party skills + `huxley-skill-stocks` listed (per the Per-entry metadata schema in § Static directory page) and a "submit a PR to add yours" footer.
- [ ] Mario smoke: install `huxley-skill-stocks` into a fresh **`basicos`** persona (not Abuelo — stocks isn't a credible voice intent for the elderly Spanish-language end user; basicos has no `never_say_no` / `child_safe` constraints to interfere with the test's plumbing focus). PTT-ask "what's Apple stock at"; verify the API key lands in the secrets dir + does not leak into git diffs; verify the call succeeds.
- [ ] **Persona-swap stability test**: with `huxley-skill-stocks` enabled on basicos, swap personas 3× via `?persona=` reconnect. `skill.schema.*` events fire only on first boot of each (skill, persona) pair — no log noise on subsequent swaps.
- [ ] `ruff check server/` + `mypy server/sdk/src server/runtime/src` + per-package pytest all green.

When the above is true, **v1 marketplace is shipped**.

## v2 — caregiver expansion (deferred)

Documented here for continuity; **not committed to**. Filed as triage entries when caregiver-shipping is decided.

### What v2 adds (purely on top of v1)

| Capability              | New surface                                                                                                                                                                      | Touches v1?                                                               |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| PWA Skills panel        | New WS endpoints + new PWA sheet that reads installed skills, renders forms from `config_schema`, writes config (via `ruamel.yaml` round-trip) + secrets, toggles enable/disable | No SDK changes; reads what v1 declared                                    |
| Self-restart machinery  | `install_skill` server endpoint + pip orchestration + atomic-swap-venv approach                                                                                                  | None                                                                      |
| Curated registry        | `huxley/skills` GitHub repo with `index.json` + JSON Schema + CI + tier system + delisting + PWA Marketplace tab                                                                 | None — registry replaces the markdown page; markdown can stay or redirect |
| Real OAuth              | OAuth helper in the SDK; redirect URL handler in the runtime; PWA "Authenticate with X" button. Uses v1's flat-secret + JSON-encode convention internally.                       | Additive — skills opt in                                                  |
| `set_json` / `get_json` | Typed accessors on `SkillSecrets` that wrap `set/get` with `json.dumps/loads`. Same on-disk bytes as v1.                                                                         | Additive — v1 callers keep using `get/set`                                |

v2-blocking concerns (`os.execv` foot-guns, JSON-Schema-doesn't-fit complex configs, registry maintainer load, dep conflicts, telegram-style first-time-auth, T1.13 swap interactions, in-flight `inject_turn` on uninstall) are real and engaged in v2's design phase — not v1 blockers.

## Risks

### v1 risks

- **Nobody writes a third-party `huxley-skill-*`.** The biggest risk: v1 ships, no community materializes, v2 was always going to be vapor. Mitigation: `huxley-skill-stocks` is the existence proof + a high-quality authoring guide. If no external skills emerge in 3 months, the marketplace thesis is wrong and we don't owe v2.
- **Authoring docs go stale.** The SDK is in flight; docs lag. Mitigation: tie authoring-docs updates to SDK changes via a doc lint check or a triage convention.
- **Alpha Vantage free tier rate-limits or breaks.** Mitigation: stocks is a reference, not a load-bearing feature. If AV goes away, swap providers in the skill (its own repo, own release cadence). The framework doesn't depend on it.

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

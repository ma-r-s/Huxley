# Installing a skill into a Huxley persona

This is the operator-side guide: you have a Huxley checkout running a persona, and you want to add a skill (first-party, third-party, or one you wrote yourself). For writing your own skill, see [`authoring.md`](authoring.md). For the architectural contract, see [`docs/skill-marketplace.md`](../skill-marketplace.md).

> **Audience**: contributors and self-hosting operators. Not the AbuelOS end user (who never installs anything).

## How install works (one paragraph)

A skill is a Python package that declares a `huxley.skills` entry point. The Huxley runtime discovers skills at startup via `importlib.metadata.entry_points` — there's no central registration, no manifest file. **If your venv has the package, the framework can see it.** Whether the skill ACTUALLY runs is controlled by the persona's `persona.yaml` (`skills:` block lists what's enabled) and the per-persona secrets dir (`<persona>/data/secrets/<skill>/values.json`).

Three names must match: the entry-point key in the skill's `pyproject.toml`, the key in `persona.yaml`'s `skills:` block, and the secrets dir name. They're all the same string by convention (`stocks`, `audiobooks`, etc.).

## Install paths

Three install scenarios, in increasing order of separation:

### A. Workspace member — already installed

`huxley-skill-audiobooks`, `huxley-skill-news`, `huxley-skill-radio`, `huxley-skill-reminders`, `huxley-skill-search`, `huxley-skill-stocks`, `huxley-skill-system`, `huxley-skill-telegram`, `huxley-skill-timers`. These live at `server/skills/<name>/` in the Huxley repo. `uv sync` from the repo root installs them all into the workspace venv. **Skip to "Enable on a persona"** below.

The framework itself depends on none of them — they're discovered at runtime via Python entry points. The workspace is a development convenience; canonical distribution is PyPI.

### B. Local skill repo (sibling to Huxley)

You're developing your own skill outside the Huxley monorepo — e.g., `~/Projects/Personal/Code/huxley-skill-mytool/` parallel to `~/Projects/Personal/Code/Huxley/`. This is the typical path for an external author building their first skill.

Edit the workspace root `pyproject.toml` to add a `uv` source pointing at your local checkout:

```toml
# pyproject.toml (workspace root)
[tool.uv.sources]
huxley-skill-mytool = { path = "../huxley-skill-mytool", editable = true }
```

Then `uv add huxley-skill-mytool` from a persona's runtime venv (or, if you want the skill always present, add it to a workspace member's deps).

### C. Published on PyPI (steady-state)

`uv add huxley-skill-<name>`. No workspace edits, no path deps. The package's wheel METADATA pulls `huxley-sdk` (currently `>=0.1.1,<0.2`) from PyPI automatically. This is how every external user installs a skill into their Huxley persona.

All 9 first-party skills (`huxley-skill-audiobooks`, `huxley-skill-news`, `huxley-skill-radio`, `huxley-skill-reminders`, `huxley-skill-search`, `huxley-skill-stocks`, `huxley-skill-system`, `huxley-skill-telegram`, `huxley-skill-timers`) are on PyPI alongside `huxley-sdk` itself.

## Verify the entry point is discoverable

After any install method, confirm the framework can see the skill before touching `persona.yaml`:

```bash
uv run python -c "from importlib.metadata import entry_points; print(sorted(ep.name for ep in entry_points(group='huxley.skills')))"
```

You should see your skill's entry-point key alongside the bundled ones. If it's missing, the install didn't take — re-check `uv sync` output and `pyproject.toml`.

## Enable on a persona

Edit the persona's `persona.yaml` (e.g. `server/personas/basic/persona.yaml`):

```yaml
skills:
  stocks: # entry-point key
    watchlist: ["AAPL", "MSFT"] # plain config (anything in config_schema)
    currency: USD
```

Plain config (everything that ISN'T a secret) goes in YAML. The skill reads it via `ctx.config.get(...)`.

## Configure secrets

Skills that need API keys, OAuth tokens, etc. read them from `<persona.data_dir>/secrets/<skill_name>/values.json` — a flat JSON `{key: string}` map. Permissions are enforced by the runtime's `JsonFileSecrets` writer (`0700` on the dir, `0600` on the file); when you create the file by hand, set the perms yourself:

```bash
PERSONA=basic
SKILL=stocks
mkdir -p server/personas/$PERSONA/data/secrets/$SKILL
chmod 700 server/personas/$PERSONA/data/secrets/$SKILL
echo '{"api_key": "your-key-here"}' > server/personas/$PERSONA/data/secrets/$SKILL/values.json
chmod 600 server/personas/$PERSONA/data/secrets/$SKILL/values.json
```

The skill reads each key via `await ctx.secrets.get("api_key")`. See [`authoring.md` § OAuth-blob convention](authoring.md#oauth-blob-convention-for-skills-that-need-it) for skills that store nested OAuth state.

## Verify the security invariants

These three checks together prove the new files are properly gitignored and locked down. **Run all three before booting the server.**

```bash
git status                                                                # 1
git check-ignore -v server/personas/$PERSONA/data/secrets/$SKILL/values.json   # 2
ls -la server/personas/$PERSONA/data/secrets/$SKILL/                       # 3
```

1. `git status` should NOT show your new files. If it does, the gitignore is broken — investigate before proceeding.
2. `git check-ignore` should print a line referencing `.gitignore:38:server/personas/*/data/`. The secrets dir inherits this rule, so no per-skill gitignore is needed.
3. `ls -la` should show `drwx------` on the dir, `-rw-------` on `values.json`. If the perms drifted (e.g. an editor saved through them), re-run `chmod`.

## Restart the server

```bash
cd server/runtime
HUXLEY_PERSONA=$PERSONA uv run huxley
```

Watch the boot log:

| Event                                                   | Meaning                                                                                                                                      |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `<skill>.setup_complete`                                | Skill loaded successfully; the secret was readable.                                                                                          |
| `<skill>.api_key_missing` (or similar warning)          | Skill registered but couldn't read its secret. Check the values.json path + perms + key name.                                                |
| `skill.schema.upgrade_needed` / `downgrade_detected`    | The skill's `data_schema_version` differs from what's stored in `schema_meta`. Read the skill's CHANGELOG; manual migration may be required. |
| `huxley_app_ready` with `skills` listing your new entry | The framework dispatched setup_all and the skill is in the registry.                                                                         |

If `huxley_app_ready` fires WITHOUT your skill in the `skills:` list, the entry point isn't being discovered — re-check the install (step "Verify the entry point" above).

## Smoke-test by voice

Open the PWA at `http://localhost:5174?persona=$PERSONA`, hold PTT, and exercise each tool the skill declares:

- Pick an utterance from each tool's `description`. Tool descriptions are written for the LLM; if the description says "use this when the user asks about a stock," then "what's Apple stock at" should route to that tool.
- The LLM relays the `say_to_user` field of the tool result. If the response sounds canned or robotic, the skill's `_format_*` helpers may be overspecifying — let the LLM rephrase by using shorter, more factual `say_to_user` strings.

The smoke is "did the right tool fire and was the response coherent." The skill author's tests pin behavior; the smoke pins integration.

## Persona-swap stability check

T1.13's hot persona swap calls `setup()` on every reconnect. If the skill emits log noise on every swap, that's a regression we want to catch.

In the PWA, swap personas via `?persona=` reconnect three times: e.g. abuelos → basic → abuelos → basic. **`skill.schema.*` events should fire only on the first boot of each (skill, persona) pair**, never on subsequent swaps. The Phase 1 unit tests pin this invariant ([`test_skill_schema_version.py::test_three_consecutive_swaps_silent_after_first`](../../server/runtime/tests/unit/test_skill_schema_version.py)); the swap smoke confirms the runtime path matches.

## Uninstalling

```yaml
# persona.yaml — just remove or comment out the block:
skills:
  # stocks: ...        # disabled
  audiobooks: ...
```

Restart. The skill stays installed in the venv (the package is still there) but doesn't load — `setup()` never runs, no in-memory state, no API calls. **Per-skill privacy in v1 is enforced by the persona.yaml enable list, not the venv.** See [`docs/skill-marketplace.md` § Privacy carve-out for T1.13](../skill-marketplace.md#privacy-carve-out-for-t113-shared-workspace-venv).

To remove the package entirely, undo the dependency edit in `server/runtime/pyproject.toml` (and the `[tool.uv.sources]` entry if you added one) and `uv sync`.

## When something fails

| Symptom                                                            | Likely cause                                                                                                                         |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| Entry point not in the `entry_points` listing after `uv sync`      | Skill's `pyproject.toml` is missing the `[project.entry-points."huxley.skills"]` block, or `uv sync` errored. Check the sync output. |
| Skill in entry-points listing, not in `huxley_app_ready.skills`    | `persona.yaml` doesn't list the skill's name in `skills:`.                                                                           |
| Tool fires but returns `not_configured`                            | `ctx.secrets.get("...")` returned None. Check values.json path, perms, and key spelling.                                             |
| Tool fires but returns `auth_failed`                               | The secret was read but the upstream service rejected it. Check the key's validity.                                                  |
| Boot crashes with `TypeError: SkillContext.__init__() missing ...` | Your skill is built against an older `huxley-sdk` than the runtime. Re-sync.                                                         |
| `git status` shows secrets dir contents                            | Your gitignore inheritance is broken. Investigate before another commit.                                                             |

Logs are the source of truth. Every skill emits structured events under its own namespace (`stocks.*`, `audiobooks.*`, etc.); see [`docs/observability.md`](../observability.md) for the convention.

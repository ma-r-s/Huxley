# Authoring a Huxley skill

This is the walkthrough for writing your own `huxley-skill-<name>` package — a Python skill that any Huxley persona can install and call by voice. It uses [`huxley-skill-stocks`](https://github.com/ma-r-s/Huxley/tree/main/server/skills/stocks) (Alpha Vantage stock quotes) as the worked example because it exercises every primitive a real third-party skill needs: secret API keys, structured config, multiple voice tools, classified errors, and graceful soft-fail when configuration is missing.

> **Audience**: Python developers who want to extend a Huxley persona with a new skill. Not for end users (who just install + use skills via the docs page) and not for the AbuelOS user (who never installs anything).

If you'd rather read than walk, jump to [`huxley-skill-stocks` on GitHub](https://github.com/ma-r-s/Huxley/tree/main/server/skills/stocks). Everything below is annotation on that repo.

## What a skill is

A skill is a Python package that:

1. Declares one or more **tools** the LLM can call (like OpenAI function calls).
2. Implements `handle(tool_name, args)` to do the work and return a result.
3. Optionally declares a `config_schema` so users can configure it without editing your source code.

The framework discovers skills via Python's [entry points](https://packaging.python.org/en/latest/specifications/entry-points/) — install the package into the runtime venv, list the skill name in `persona.yaml`, restart, and the skill is live.

Read [`docs/skills/README.md`](README.md) for the full SDK reference. This page is the build-your-first-skill walkthrough.

## Prerequisites

- Python **3.13+** (matches Huxley's runtime).
- `uv` for package management ([install instructions](https://docs.astral.sh/uv/getting-started/installation/)).
- A Huxley checkout if you want to run the skill end-to-end. During v1 development your skill builds against `huxley-sdk` as a path dependency on the Huxley repo; after `huxley-sdk` ships to PyPI it's a versioned pin.

## Project layout

A minimal skill repo:

```
huxley-skill-<name>/
├── pyproject.toml
├── README.md
├── LICENSE
├── CHANGELOG.md
├── .gitignore
├── .github/workflows/ci.yml          # ruff + mypy + pytest
├── src/
│   └── huxley_skill_<name>/
│       ├── __init__.py
│       ├── skill.py                  # the Skill class
│       └── provider.py               # external service client (optional)
└── tests/
    ├── test_provider.py
    └── test_skill.py
```

The `provider.py` / `skill.py` split is the canonical pattern for skills that talk to an external HTTP service: the provider is testable with `pytest-httpx` mocks; the skill class stays focused on the Skill protocol. Skills with no external dependency (a calculator, a dice roller) collapse this to one file.

## `pyproject.toml`

The [stocks pyproject](https://github.com/ma-r-s/Huxley/blob/main/server/skills/stocks/pyproject.toml), annotated:

```toml
[project]
name = "huxley-skill-stocks"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "huxley-sdk",       # the Skill protocol + types
    "httpx>=0.27",      # whatever your skill needs
]

# This entry point is how Huxley discovers your skill. The key is the
# string users put in `persona.yaml`'s `skills:` block; the value points
# at the Skill class. **The key, the persona.yaml name, and the
# secrets-dir name (if you use ctx.secrets) must all match.**
[project.entry-points."huxley.skills"]
stocks = "huxley_skill_stocks.skill:StocksSkill"

# During v1 dev, pin huxley-sdk via a path dep against your local
# Huxley checkout. Once huxley-sdk publishes to PyPI, drop this block
# and pin a version range under [project.dependencies].
[tool.uv.sources]
huxley-sdk = { path = "../Huxley/server/sdk", editable = true }
```

The full file also configures `ruff`, `mypy --strict`, and `pytest-asyncio` — copy as-is unless you have a reason to deviate.

## The Skill class

```python
from huxley_sdk import ToolDefinition, ToolResult

class StocksSkill:
    config_schema: ClassVar[dict[str, Any] | None] = _CONFIG_SCHEMA
    data_schema_version: ClassVar[int] = 1

    @property
    def name(self) -> str:
        return "stocks"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [...]  # see below

    async def setup(self, ctx: SkillContext) -> None:
        ...

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        ...
```

This is the entire `Skill` Protocol. There's no base class to inherit from — Huxley uses [structural typing](https://typing.readthedocs.io/en/latest/spec/protocol.html). If your class has the right shape, it's a Skill.

Optional methods (`reconfigure`, `teardown`, `prompt_context`) are no-ops by default; only override them when you need to.

### `name`

The string users write in `persona.yaml` to enable your skill. **Must match the entry-point key in `pyproject.toml`.** Changing it later is a breaking change for every user.

### `tools`

Each tool is an OpenAI function-call schema. Stocks has three:

```python
ToolDefinition(
    name="get_stock_price",
    description=(
        "Get the current price of a stock by ticker symbol. "
        "Use this when the user asks about a specific company "
        "or stock — e.g. 'what's Apple stock at', 'how is Tesla "
        "doing today'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "ticker": {
                "type": "string",
                "description": (
                    "Stock ticker symbol, e.g. AAPL, MSFT, GOOG. "
                    "If the user names a company, resolve to the "
                    "primary US listing yourself before calling."
                ),
            }
        },
        "required": ["ticker"],
    },
)
```

The `description` is **for the LLM, not for human readers** — it's how the model decides whether your tool is the right one for the user's intent. Include example utterances; be specific about when to call it. Vague descriptions cause the model to skip your tool or invoke it for the wrong intent.

Skills that need different descriptions per language (English, Spanish, French) override `reconfigure` to refresh `tools` when `ctx.language` changes — see [`docs/skills/search.md`](search.md) for that pattern.

## `config_schema` — letting users configure your skill

A skill that hardcodes everything is uninteresting. A skill that asks the user to read your source code is hostile. The middle path is `config_schema`: a JSON Schema 2020-12 declaring what fields you accept from `persona.yaml`. v2 of Huxley's PWA renders a form from this; v1 just expects users to hand-write the YAML.

The stocks schema declares all three field shapes the v2 form-renderer must support:

```python
_CONFIG_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["api_key"],
    "properties": {
        "api_key": {
            "type": "string",
            "format": "secret",      # routes to ctx.secrets, not persona.yaml
            "title": "Alpha Vantage API key",
            "x-huxley:help": "Get a free key at https://www.alphavantage.co/support/#api-key",
        },
        "watchlist": {
            "type": "array",
            "items": {"type": "string"},
            "title": "Default watchlist",
            "x-huxley:help": "Ticker symbols to summarize when the user asks 'how's my watchlist'.",
            "default": [],
        },
        "currency": {
            "type": "string",
            "enum": ["USD", "EUR", "GBP", "JPY"],
            "default": "USD",
            "title": "Display currency",
        },
    },
}
```

Two custom extensions Huxley honors on top of standard JSON Schema:

- **`"format": "secret"`** on a string field — tells Huxley this value belongs in `ctx.secrets` (the per-persona secrets file), NOT in `persona.yaml`. The v2 PWA renders these as password inputs.
- **`"x-huxley:help"`** — markdown help text the v2 PWA renders alongside the field. Use this consistently for every user-tunable field, not the standard `description`. (`description` is fine for nested array items or anywhere the PWA isn't rendering.)

### When NOT to declare `config_schema`

Some configs don't fit JSON Schema cleanly: per-language i18n maps, lists of records, contact-style dicts where keys are user-defined, or skill-specific UX (Telegram's SMS auth flow). For those, leave `config_schema = None` (the default) and the v2 PWA shows "this skill needs manual configuration — see [skill docs]." The audiobooks and telegram skills go this route. **You don't lose anything by opting out** — your skill still loads and runs the same way.

## `data_schema_version` — when you bump the persisted shape

```python
data_schema_version: ClassVar[int] = 1
```

Bump this integer when you change anything about the data your skill persists:

- New required keys in `ctx.storage`.
- New required keys in `ctx.secrets` (especially OAuth state shape changes).
- New required fields in your config that aren't backward-compatible.

The Huxley runtime persists each skill's last-seen version in the persona's `schema_meta` table under `skill_version:<your-name>`. On the next boot, if your declared version doesn't match the stored one, Huxley logs a warning event (`skill.schema.upgrade_needed` or `skill.schema.downgrade_detected`) so the operator sees the drift.

**v1 does not auto-migrate.** Document migration steps in your `CHANGELOG.md` so users can apply them. v2 will gate cross-major upgrades behind explicit confirmation in the PWA.

If you never persist anything (a stateless calculator skill), leave the default and ignore this section.

## `setup(ctx)` — wiring the skill at boot

`setup` runs once when a persona enables your skill. It receives a `SkillContext` carrying every framework primitive you need:

- `ctx.logger` — pre-tagged with `skill=<your-name>`. Use `ctx.logger.ainfo("event_name", k=v)` for structured events.
- `ctx.config` — the merged dict from `persona.yaml`'s `skills.<your-name>:` block. Per-language `i18n.<lang>` overrides have already been applied; you see a flat view.
- `ctx.secrets` — async `get`/`set`/`delete`/`keys` over `<persona>/data/secrets/<your-name>/values.json`.
- `ctx.storage` — namespaced KV storage, async `get_setting`/`set_setting`/`list_settings`/`delete_setting`.
- `ctx.persona_data_dir` — the persona's data root, for any files you need beyond storage/secrets.
- `ctx.language` — the active ISO 639-1 language code (`"es"`, `"en"`, `"fr"`).
- `ctx.inject_turn`, `ctx.background_task`, `ctx.start_input_claim`, etc. — see [`docs/skills/README.md`](README.md) for the full surface.

The stocks `setup`:

```python
async def setup(self, ctx: SkillContext) -> None:
    self._logger = ctx.logger

    # 1. Plain config from persona.yaml.
    self._watchlist = [t.upper().strip() for t in ctx.config.get("watchlist") or []]
    self._currency = ctx.config.get("currency", "USD").upper()

    # 2. Secrets — soft-fail if absent.
    api_key = await ctx.secrets.get("api_key")
    if not api_key:
        await self._logger.awarning(
            "stocks.api_key_missing",
            hint="Drop {api_key: <key>} in <persona>/data/secrets/stocks/values.json.",
        )
        self._client = None
        return

    self._client = AlphaVantageClient(api_key)
```

Two patterns to copy:

1. **Always use `ctx.config.get(...)` with defaults**, never `ctx.config["..."]`. A user who hasn't configured your skill yet still gets a working persona; your skill just degrades gracefully.
2. **Soft-fail on missing secrets.** Log a warning, set internal state to "not configured," and let your tool handlers return user-facing error messages. This is the `not_configured` error path in `handle`. Crashing in `setup()` blocks the persona from booting at all, which punishes everyone with the skill listed in their config — a much worse UX than "I can't reach the stock service right now."

## OAuth-blob convention (for skills that need it)

`ctx.secrets` stores **flat strings**. If your skill needs to persist nested OAuth state (`access_token` + `refresh_token` + `expires_at`), JSON-encode the dict yourself into a single key:

```python
state = {"access_token": "...", "refresh_token": "...", "expires_at": 1735689600}
await ctx.secrets.set("oauth_state", json.dumps(state))

# Reading, with corruption recovery:
raw = await ctx.secrets.get("oauth_state")
try:
    state = json.loads(raw) if raw else None
except json.JSONDecodeError:
    state = None
    await ctx.secrets.delete("oauth_state")  # force re-auth
```

Stocks doesn't need this (Alpha Vantage has long-lived API keys, no OAuth refresh). A future SDK version will add `ctx.secrets.set_json` / `get_json` typed accessors that wrap this same pattern; the on-disk bytes stay identical, so v2 is purely additive.

## `handle(tool_name, args)` — doing the work

```python
async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
    if tool_name == "get_stock_price":
        ticker = str(args.get("ticker", "")).strip().upper()
        if not ticker:
            return _error("invalid_args", "I need a ticker symbol to look that up.")
        return await self._get_stock_price(ticker)
    # ... other tools ...
    return _error("unknown_tool", f"I don't know how to handle '{tool_name}'.")
```

`ToolResult` is a frozen dataclass with `output: str` (JSON-serialized; sent back to the LLM as the function call output) and an optional `side_effect` (used by skills that play audio or claim the mic; not relevant here).

The result-shape convention every first-party skill follows:

```python
def _ok(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(output=json.dumps(payload, ensure_ascii=False))

def _error(kind: str, say_to_user: str) -> ToolResult:
    return ToolResult(output=json.dumps(
        {"error": kind, "say_to_user": say_to_user},
        ensure_ascii=False,
    ))
```

Success payloads pack domain data plus a `say_to_user` field — the string the LLM relays in the persona's voice. Error payloads carry `error` (a machine-readable kind for logging / metrics) plus `say_to_user`. The LLM uses `say_to_user` to phrase its response naturally; you don't need to over-specify the wording.

### Classifying errors at the provider boundary

The pattern stocks uses for talking to Alpha Vantage:

```python
# In provider.py — translate HTTP responses into typed errors.
class ProviderError(Exception): ...
class RateLimitError(ProviderError): ...
class AuthError(ProviderError): ...
class UnknownTickerError(ProviderError): ...

# In skill.py — translate typed errors into user-facing messages.
try:
    quote = await self._client.get_quote(ticker)
except UnknownTickerError:
    return _error("unknown_ticker", f"I couldn't find a stock with ticker {ticker}.")
except RateLimitError:
    return _error("rate_limited", "The free tier is rate-limited right now — try again in a minute.")
except AuthError:
    return _error("auth_failed", "The API key was rejected. Check the key file.")
except ProviderError as exc:
    await self._logger.awarning("stocks.provider_error", ticker=ticker, error=str(exc))
    return _error("provider_error", "I couldn't reach the stock data service.")
```

Two-layer translation is load-bearing: provider raises typed errors, skill catches and translates. This keeps the provider testable in isolation and the skill's error messages auditable in one place.

## Tests

Stocks has 40 tests across two files. Both run fully offline.

`test_provider.py` mocks Alpha Vantage's HTTP responses with `pytest-httpx`:

```python
@pytest.mark.asyncio
async def test_rate_limit_note_is_classified(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_url_pattern, json={"Note": "Thank you for using..."})
    client = AlphaVantageClient(api_key="test-key")
    with pytest.raises(RateLimitError):
        await client.get_quote("AAPL")
```

`test_skill.py` uses `huxley_sdk.testing.make_test_context` and a `FakeClient` so the skill's setup + handle are tested without any HTTP at all:

```python
from huxley_sdk.testing import make_test_context

async def _setup_skill(*, api_key: str | None = "test-key") -> StocksSkill:
    skill = StocksSkill(client=FakeClient())
    ctx = make_test_context(config={})
    if api_key is not None:
        await ctx.secrets.set("api_key", api_key)
    await skill.setup(ctx)
    return skill
```

The two test files together pin every error path the skill claims to handle. **A real third-party skill should aim for similar coverage**: every error class your provider raises, every config corner (missing keys, empty values, type mismatches), every tool's happy path + error path.

## Distribution

When you're ready to publish:

1. `uv build` — produces a wheel + sdist in `dist/`.
2. `uv publish` — uploads to PyPI (you'll need a PyPI account + API token).
3. Submit a PR to [`docs/skills/index.md`](index.md) in the Huxley repo to add your skill to the public directory. The required metadata is documented there.

## Self-test

If you copied this walkthrough end-to-end and your skill still doesn't work as described, **the bug is in this doc, not in your code**. Open an issue at [github.com/ma-r-s/Huxley/issues](https://github.com/ma-r-s/Huxley/issues) and we'll fix it.

## See also

- [`docs/skills/README.md`](README.md) — the SDK API reference (`SkillContext`, `ctx.storage`, `ctx.inject_turn`, `InputClaim`, etc.).
- [`docs/skill-marketplace.md`](../skill-marketplace.md) — the architectural contract: storage layout, schema versioning, what v2 will add.
- [`docs/concepts.md`](../concepts.md) — Huxley's vocabulary (persona, skill, turn, side effect).
- [`huxley-skill-stocks`](https://github.com/ma-r-s/Huxley/tree/main/server/skills/stocks) — the worked example. Read the source.

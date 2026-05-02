# huxley-sdk

The skill-author SDK for [Huxley](https://github.com/ma-r-s/Huxley), a voice agent framework. This package defines the protocol every Huxley skill implements; the runtime imports it at startup and dispatches tool calls through it.

> **Status**: 0.1.0 — first public release. The Skill protocol + SkillContext + side-effect types + ctx.secrets/storage are stable. Future versions will add `set_json/get_json` typed accessors on `SkillSecrets` (additive; no breaking change planned).

## Who this is for

You — if you want to write a `huxley-skill-<name>` Python package that any Huxley persona can install and call by voice. Everything you need to implement the `Skill` protocol lives here:

```python
from huxley_sdk import (
    Skill,             # the protocol
    SkillContext,      # what the framework hands you at setup()
    ToolDefinition,    # OpenAI function-call schema
    ToolResult,        # what your tool handler returns
    SkillSecrets,      # async per-skill secrets store
    SkillStorage,      # async per-skill KV storage
    InjectTurn,        # proactive speech
    InputClaim,        # mic + speaker takeover for full-duplex audio
    AudioStream,       # streamed PCM playback as a side effect
    PlaySound,         # one-shot earcon as a side effect
    Catalog,           # personal-content fuzzy matcher
)
```

## Quick example

```python
import json
from typing import Any, ClassVar
from huxley_sdk import SkillContext, ToolDefinition, ToolResult


class StocksSkill:
    config_schema: ClassVar[dict[str, Any] | None] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["api_key"],
        "properties": {
            "api_key": {"type": "string", "format": "secret", "title": "Alpha Vantage API key"},
        },
    }
    data_schema_version: ClassVar[int] = 1

    @property
    def name(self) -> str:
        return "stocks"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="get_stock_price",
                description="Get the current price of a stock by ticker. Use when the user asks about a specific company.",
                parameters={
                    "type": "object",
                    "properties": {"ticker": {"type": "string"}},
                    "required": ["ticker"],
                },
            ),
        ]

    async def setup(self, ctx: SkillContext) -> None:
        self._api_key = await ctx.secrets.get("api_key")

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        # ... call your data source, return a ToolResult ...
        return ToolResult(output=json.dumps({"say_to_user": "..."}))
```

Then in your `pyproject.toml`:

```toml
[project.entry-points."huxley.skills"]
stocks = "huxley_skill_stocks.skill:StocksSkill"
```

`uv add huxley-skill-stocks` from the runtime venv, list `stocks:` in your persona's `persona.yaml`, restart, talk.

## What's in the SDK

- **The `Skill` Protocol** — the structural-typing contract every skill satisfies. Optional methods (`setup`, `reconfigure`, `teardown`, `prompt_context`) have empty defaults.
- **`SkillContext`** — what the framework injects at `setup()`: logger, storage, secrets, persona-data-dir, language, inject_turn, background_task, input-claim handles, client-event subscribers.
- **`ToolDefinition` + `ToolResult`** — OpenAI function-call schema and the return shape skills produce.
- **`SkillSecrets`** — async `get/set/delete/keys` over a per-persona JSON file at `<persona>/data/secrets/<skill>/values.json`. Skills with nested OAuth state JSON-encode the dict into a single key (the OAuth-blob convention).
- **`SkillStorage`** — namespaced async KV adapter for persistent per-skill state.
- **Side-effect types** — `AudioStream`, `PlaySound`, `InputClaim`, `CancelMedia`, `SetVolume` — declarative effects skill handlers attach to `ToolResult`.
- **`InjectTurn` / `InjectTurnAndWait`** — proactive speech (medication reminders, inbound-call announcements) with priority + dedup-key.
- **`Catalog`** — personal-content fuzzy matcher for skills that resolve "play One Hundred Years of Solitude" to a library id.
- **`huxley_sdk.testing`** — `make_test_context()` for unit tests, plus `FakeSkill` for the framework's own test suite.

See [the authoring walkthrough](https://github.com/ma-r-s/Huxley/blob/main/docs/skills/authoring.md) for the full guide.

## Documentation

- [Skill authoring walkthrough](https://github.com/ma-r-s/Huxley/blob/main/docs/skills/authoring.md) — annotated `huxley-skill-stocks` as a worked example.
- [SDK reference](https://github.com/ma-r-s/Huxley/blob/main/docs/skills/README.md) — every primitive, when to use what.
- [Skill marketplace spec](https://github.com/ma-r-s/Huxley/blob/main/docs/skill-marketplace.md) — architectural contract: secrets layout, schema-versioning, OAuth-blob convention, registry shape.
- [Skill registry feed](https://raw.githubusercontent.com/ma-r-s/huxley-registry/main/index.json) — discoverable list of installable Huxley skills.

## Compatibility

- Python 3.13+
- No runtime dependencies beyond `structlog>=24.4`. Skills bring their own deps.

## License

MIT — see [`LICENSE`](LICENSE).

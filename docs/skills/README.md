# Writing a Skill

A Huxley skill is a Python package that teaches the agent to do something new — play music, control lights, send messages, query an API. This document is for skill authors.

For the conceptual model, see [`../concepts.md`](../concepts.md). For a full worked example, see [`audiobooks.md`](./audiobooks.md).

> **SDK status**: the Huxley SDK (`huxley_sdk`) lives at `packages/sdk/`. Skill authors import from it: `from huxley_sdk import Skill, ToolDefinition, ToolResult, SkillContext`. The two built-in skills (`audiobooks`, `system`) currently still live inside `packages/core/src/huxley/skills/` for legacy reasons; stage 2 of the active refactor moves them into their own `packages/skills/<name>/` packages with entry-point loading, at which point they become the model for third-party skill packages. The SDK protocol shape and contract below are stable across that move.

## The Skill protocol

Skills are structurally typed (PEP 544 `Protocol`), not nominal subclasses. Implement the interface and the registry accepts you — no inheritance required.

```python
from huxley_sdk import Skill, ToolDefinition, ToolResult

class MySkill:
    @property
    def name(self) -> str: ...

    @property
    def tools(self) -> list[ToolDefinition]: ...

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult: ...

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...
```

- **`name`** — unique identifier for logging and registry lookups.
- **`tools`** — list of tool schemas exposed to the LLM (see below).
- **`handle`** — dispatch entry point. Route on `tool_name`.
- **`setup`** / **`teardown`** — lifecycle hooks. Load catalogs in `setup`, persist state in `teardown`.

## Anatomy of a tool definition

```python
ToolDefinition(
    name="search_audiobooks",
    description="Searches the user's local audiobook library by title or author.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search text (title, author, or partial name)",
            }
        },
        "required": ["query"],
    },
)
```

- **`name`** — globally unique across all skills enabled in a persona. Registration fails loudly on collisions.
- **`description`** — **written in the persona's language**. The LLM uses it to decide when to call the tool. Vague descriptions cause bad dispatch; precise descriptions are worth their weight.
- **`parameters`** — standard JSON Schema. The LLM fills these from conversation context.

### Multilingual descriptions

A skill that supports multiple personas may need to expose its tool descriptions in multiple languages. The convention (still being designed): the skill receives the persona's `language` in its context at `setup()` and returns the appropriate description set. For now (single-language Huxley deployments), hardcode the description in the language your target persona uses.

## Returning results

Tools return a `ToolResult`:

```python
ToolResult(
    output=json.dumps({"results": [...], "message": "Found 3 books"}),
)
```

- **`output`** is JSON text sent back to the LLM as the function-call output. The LLM narrates it to the user.
- **`audio_factory`** _(optional, will become `side_effect` after the next refactor)_ is a callable that returns an async iterator of PCM16 chunks. The framework invokes it after the model finishes speaking, so any tool-produced audio plays cleanly _after_ the model's verbal acknowledgement. Skills with no audio side effect leave it `None`.

### Info tools vs side-effect tools

| Kind            | `audio_factory` | Examples                                     | Framework behavior                                                                                                                        |
| --------------- | --------------- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **Info**        | `None`          | `search_audiobooks`, `get_current_time`      | Coordinator requests a follow-up response so the model can narrate the result. Multi-round chained turn.                                  |
| **Side-effect** | not `None`      | `play_audiobook`, `audiobook_control` (seek) | Coordinator latches the factory; after the model's terminal `response.done`, the factory fires and streams PCM through the audio channel. |

The model is told (via the tool description) to **pre-narrate** the side effect — e.g. _"Putting on the book for you."_ — _before_ calling the tool. The framework guarantees the narration plays before the factory does.

### The factory closure pattern

For tools that compute a parameter at dispatch time (e.g. a rewound position), capture the value in a closure rather than persisting it eagerly:

```python
def _build_factory(self, book_id: str, path: str, start_position: float):
    async def stream():
        bytes_read = 0
        try:
            async for chunk in self._player.stream(path, start_position=start_position):
                bytes_read += len(chunk)
                yield chunk
        finally:
            elapsed = bytes_read / BYTES_PER_SECOND
            await self._storage.save_audiobook_position(
                book_id, start_position + elapsed,
            )
    return stream
```

Why: if the turn is interrupted before the framework invokes the factory, the closure is never executed and storage stays at the last actually-played position. The skill never writes the new position eagerly during dispatch. This gives interrupt-atomicity without a transaction model.

## Persona constraints — what your skill should respect

Some personas declare behavioral constraints (see [`../concepts.md#constraint`](../concepts.md#constraint)). Skills targeting those personas should honor them. The current constraint set:

### `never_say_no`

If the persona enables `never_say_no`, your skill must not return dead-end negatives. Every tool response must include a `message` field with a constructive alternative or a clarifying question.

❌ Bad:

```python
return ToolResult(output=json.dumps({"error": "Not found"}))
```

✅ Good (in the persona's language):

```python
return ToolResult(output=json.dumps({
    "results": [],
    "message": "I don't have that exact book. The closest match is 'Cien años de soledad'. Would you like that?",
    "closest_match": {"id": "...", "title": "Cien años de soledad"},
}))
```

The LLM reads this and offers the alternative naturally. See [`../personas/abuelos.md`](../personas/abuelos.md) for the canonical worked example of `never_say_no` in production.

### `confirm_destructive`

If the persona enables `confirm_destructive`, any tool that performs an irreversible action should either:

- Take an explicit `confirmed: true` parameter, OR
- Have a separate "preview" tool that returns "what would happen if I did this," letting the model ask before calling the real action.

### `child_safe`

If your skill could surface adult or profane content (search results, news headlines, etc.), apply filtering when this constraint is active.

### Forward-compatibility

A skill that doesn't know about a future constraint just won't handle it specially. The framework injects the matching system-prompt language regardless, so the LLM can still steer correctly. Skills opt in to constraint-aware behavior; they don't have to.

## Optional: `prompt_context()` for baseline awareness

Some questions don't need a tool call — they need the LLM to already know. _"What books do you have?"_ is the canonical example: if the catalog is already in the session prompt, the LLM can answer immediately without round-tripping through `search_audiobooks`.

Skills that want to contribute baseline context to every session prompt can implement an optional `prompt_context()` method:

```python
class AudiobooksSkill:
    def prompt_context(self) -> str:
        if not self._catalog:
            return ""
        lines = ["Books in the user's library:"]
        for book in self._catalog[:50]:
            lines.append(f'- "{book["title"]}" by {book["author"]}')
        return "\n".join(lines)
```

**How it's wired**: at session connect time, the framework iterates registered skills and collects any non-empty `prompt_context()` strings, appending them to the system prompt before sending `session.update`.

**Not in the Skill protocol** — it's optional. Skills that don't implement it contribute nothing.

**Scaling rule**: keep each skill's context under a few hundred tokens. For collections that would blow past that (e.g., thousands of items), return a short summary and let the LLM paginate via search.

**When _not_ to use it**: don't dump state that changes frequently — the context is only refreshed on session connect, not mid-conversation.

## Logging — make your skill debuggable

Skills get a logger via the SDK context. Use it. The framework's debugging workflow (described in [`../observability.md`](../observability.md)) depends on every component emitting structured events with the right namespace.

```python
async def handle(self, tool_name: str, args: dict) -> ToolResult:
    await self.log.info("audiobooks.dispatch", tool=tool_name, args_keys=list(args))
    result = await self._do_the_thing(args)
    await self.log.info("audiobooks.result", success=result.success)
    return result
```

The convention: `<skill_name>.<event>`. The framework auto-injects the `turn` ID, so you don't have to thread it through.

## Testing

Skills must have unit tests. Mock the infrastructure (`Storage`, any external clients), assert on `ToolResult.output` and — for side-effect tools — invoke `result.audio_factory()` and verify the underlying stream call.

For end-to-end coverage of how your skill behaves inside the framework (factory latching, mid-chain interrupts, follow-up rounds), see the integration test pattern in [`test_coordinator_skill_integration.py`](../../packages/core/tests/unit/test_coordinator_skill_integration.py) — it wires a real `TurnCoordinator` to a real skill with a mocked infrastructure.

Integration tests that hit real subprocess (ffmpeg) or real provider APIs live in `packages/core/tests/integration/` and are marked `@pytest.mark.integration`. Skipped by default.

## Distribution — making your skill installable

Built-in skills (audiobooks, system) live in `packages/skills/<name>/` in this repo. Community skills are independent Python packages published on PyPI under the convention `huxley-skill-<name>`.

A persona enables a skill by listing it in `persona.yaml`:

```yaml
skills:
  - my_skill: { config_key: value }
```

The framework matches the YAML key (`my_skill`) to the package name (`huxley-skill-my_skill`) and instantiates it with the config dict.

## File layout for a new skill (post-SDK-extraction)

```
huxley-skill-my-thing/
├── pyproject.toml            # depends on huxley-sdk
├── README.md                 # what it does, config, examples
├── src/
│   └── huxley_skill_my_thing/
│       ├── __init__.py       # exports MySkill class
│       └── skill.py
└── tests/
    └── test_my_skill.py
```

For now, the two built-in skills (`audiobooks`, `system`) still live in `packages/core/src/huxley/skills/<name>.py` with tests in `packages/core/tests/unit/test_<name>_skill.py`; stage 2 of the active refactor moves them out into their own `packages/skills/<name>/` packages, which is the model third-party skills should follow.

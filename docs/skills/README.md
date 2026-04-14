# Skills

Skills are AbuelOS's extension points. A skill declares a set of LLM tools and handles their invocations. The skill registry collects every tool at startup and routes incoming calls by tool name.

This document covers **authoring**. For the v0 skill spec see [`audiobooks.md`](./audiobooks.md).

## The Skill protocol

Skills are structurally typed (PEP 544 `Protocol`), not nominal subclasses. Implement the interface and the registry accepts you — no inheritance required.

```python
# server/src/abuel_os/types.py
@runtime_checkable
class Skill(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def tools(self) -> list[ToolDefinition]: ...

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult: ...
    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...
```

- **`name`** — unique identifier for logging and registry lookups.
- **`tools`** — list of tool schemas exposed to the LLM. See below.
- **`handle`** — dispatch entry point. Route on `tool_name`.
- **`setup`** / **`teardown`** — lifecycle hooks. Load catalogs in `setup`, persist state in `teardown`.

## Anatomy of a tool definition

```python
ToolDefinition(
    name="search_audiobooks",
    description="Busca audiolibros en la biblioteca local del usuario.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Texto de búsqueda (título, autor, o parte del nombre)",
            }
        },
        "required": ["query"],
    },
)
```

- **`name`** — globally unique across all skills. Registration fails loudly if two skills declare the same tool name.
- **`description`** — **written in Spanish**. The LLM uses it to decide when to call the tool. Vague descriptions cause bad dispatch; precise descriptions are worth their weight.
- **`parameters`** — standard JSON Schema. The LLM fills these from conversation context.

## Returning results

Tools return a `ToolResult`:

```python
ToolResult(
    output=json.dumps({"results": [...], "message": "Encontré 3 libros"}),
)
```

- **`output`** is JSON text sent back to the LLM as the function-call output. The LLM narrates it to the user.
- **`audio_factory`** (optional) is a callable that returns an async iterator of PCM16 chunks. The `TurnCoordinator` invokes it after the model finishes speaking, so any tool-produced audio (an audiobook stream, music, news clip) plays cleanly _after_ the model's verbal acknowledgement. Skills with no audio side effect leave it `None`. See [`turns.md`](../turns.md) for the full design.

### Info tools vs side-effect tools

| Kind            | `audio_factory` | Examples                                     | Coordinator behavior                                                                                                                                          |
| --------------- | --------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Info**        | `None`          | `search_audiobooks`, `get_current_time`      | Coordinator requests a follow-up response so the model can narrate the result. Multi-round chained turn.                                                      |
| **Side-effect** | not `None`      | `play_audiobook`, `audiobook_control` (seek) | Coordinator latches the factory onto `pending_factories`. After the model's terminal `response.done`, the factory fires and streams PCM through `send_audio`. |

The model is told (via the tool description) to **pre-narrate** the side effect — e.g. _"Ahí le pongo el libro, don."_ — _before_ calling the tool. The coordinator guarantees the narration plays before the factory does.

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

Why: if the turn is interrupted before the coordinator invokes the factory, the closure is never executed and storage stays at the last actually-played position. The skill never writes the new position eagerly during dispatch. This gives interrupt-atomicity without a transaction model.

## The "nunca decir no" contract — skill author rules

This is non-negotiable. See [`../vision.md#the-nunca-decir-no-contract`](../vision.md#the-nunca-decir-no-contract) for the product rationale. A review that finds a violation is a blocker.

### Rule 1 — No empty-handed negatives

❌ Bad:

```python
return ToolResult(output=json.dumps({"error": "Not found"}))
```

✅ Good:

```python
return ToolResult(output=json.dumps({
    "results": [],
    "message": "No tengo ese libro exacto. Lo más parecido que tengo es 'Cien años de soledad'. ¿Quiere ese?",
    "closest_match": {"id": "...", "title": "Cien años de soledad"},
}))
```

The LLM reads this and offers the alternative naturally.

### Rule 2 — Every `output` includes a `message` field

The `message` field is Spanish-language text aimed at the LLM narrator. It tells the LLM _what to tell grandpa_, in the tone required by [`../vision.md`](../vision.md#persona). Without it, the LLM invents explanations and often lands on _"no disponible"_ — the one thing we're trying to eliminate.

### Rule 3 — Errors wrapped in plain Spanish

Internal exceptions (file not found, socket error, timeout) must be caught and turned into a result the LLM can narrate gracefully:

```python
try:
    await self._player.probe(path)
except PlayerError:
    return ToolResult(output=json.dumps({
        "playing": False,
        "message": "No pude abrir ese libro. Déjeme intentar otra vez o escoger otro.",
    }))
```

### Rule 4 — Confirm ambiguity, don't guess

When multiple interpretations are valid, return the candidates in `output` and let the LLM ask. Never guess silently.

## Optional: `prompt_context()` for baseline awareness

Some questions don't need a tool call — they need the LLM to already know. _"¿qué libros tienes?"_ is the canonical example: if the catalog is already in the session prompt, the LLM can answer immediately without round-tripping through `search_audiobooks`.

Skills that want to contribute baseline context to every session prompt can implement an **optional** `prompt_context()` method:

```python
class AudiobooksSkill:
    def prompt_context(self) -> str:
        if not self._catalog:
            return ""
        lines = ["Biblioteca de audiolibros disponibles:"]
        for book in self._catalog[:50]:
            lines.append(f'- "{book["title"]}" por {book["author"]}')
        return "\n".join(lines)
```

**How it's wired**: at connect time, `SkillRegistry.get_prompt_context()` iterates registered skills and collects any non-empty `prompt_context()` strings. `SessionManager.connect()` appends the result to the system prompt before sending `session.update`.

**Not in the Skill protocol** — it's optional. Skills that don't implement it contribute nothing. `getattr(skill, "prompt_context", None)` is how the registry discovers it, so you don't need to subclass or register a capability flag.

**Scaling rule**: keep each skill's context under a few hundred tokens. For a library that would blow past that (e.g., thousands of audiobooks), return a short summary instead of the full list and let the LLM paginate via search. For single-user AbuelOS scale (dozens of books, a handful of skills), the full list is fine.

**When _not_ to use it**: don't dump state that changes frequently — the context is only refreshed on session connect, not mid-conversation. If grandpa adds a book mid-session, he won't see it until the next wake-word.

## Registering a skill

```python
# server/src/abuel_os/app.py
audiobooks = AudiobooksSkill(library_path=..., player=..., storage=...)
self.skill_registry.register(audiobooks)
```

The registry calls `setup()` on startup and `teardown()` on shutdown. Use them for catalog loading and state persistence respectively.

## Testing

Skills must have unit tests. Mock the infrastructure (`AudiobookPlayer`, `Storage`), assert on `ToolResult.output` and — for side-effect tools — invoke `result.audio_factory()` and verify the underlying stream call. Example: [`server/tests/unit/test_audiobooks_skill.py`](../../server/tests/unit/test_audiobooks_skill.py).

For coordinator ↔ skill contract coverage (factory latching, mid-chain interrupts, follow-up rounds), see [`test_coordinator_skill_integration.py`](../../server/tests/unit/test_coordinator_skill_integration.py) — wires a real `TurnCoordinator` to a real skill with a mocked player.

Integration tests that hit real ffmpeg or real OpenAI live in `server/tests/integration/` and are marked `@pytest.mark.integration`. They are skipped by default. Run them explicitly with `uv run pytest -m integration`.

## File layout for a new skill

```
server/src/abuel_os/skills/my_skill.py      # the skill
server/tests/unit/test_my_skill.py          # unit tests
docs/skills/my_skill.md                     # product spec (copy audiobooks.md template)
```

Then wire it in `app.py` next to existing skills, and add it to [`../roadmap.md`](../roadmap.md).

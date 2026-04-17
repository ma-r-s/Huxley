# Skill: audiobooks

The first first-party Huxley skill. Will be packaged as `huxley-skill-audiobooks` after the SDK extraction. Provides voice-driven audiobook search, playback, and navigation against a local library of M4B files. The Spanish examples below come from the AbuelOS persona ([`../personas/abuelos.md`](../personas/abuelos.md)) because it's the canonical use case, but the skill works against any persona that enables it — tool descriptions and user-facing strings should be localized to the persona's language.

## Product surface

A user enabled with this skill must be able to:

- **Find** a book by natural language (_"búscame el libro de García Márquez"_, _"that one about the colonel"_).
- **Start** playback from a search result, or have the LLM decide if the top match is obvious.
- **Resume** the last-played book automatically (_"sigue con el libro"_, _"keep going with the book"_).
- **Pause / resume** mid-sentence.
- **Navigate** — back a chapter, forward a minute, _"un poquito atrás"_.
- **Stop** and come back later to the same second.
- **Hear what's playing** — _"¿qué estoy escuchando?"_
- **Get recommendations** — _"¿qué libros tienes?"_
- Never hit a dead-end _"no"_ (when the persona enables `never_say_no`). See [Nunca-decir-no wiring](#nunca-decir-no-wiring).

## Content format

### Preferred — M4B

Single file per book. AAC audio inside an MP4 container with:

- Embedded chapter markers (`chpl` atom or Nero-style chapters)
- Embedded metadata: title, author, narrator, description, cover art
- One file = one book

`ffmpeg` decodes M4B natively and `ffprobe` exposes chapters + metadata as JSON. M4B gives us metadata and chapter markers in one file with no sidecar to maintain.

**Getting M4B**:

- [LibriVox](https://librivox.org/) — public-domain audiobooks in multiple languages, free M4B downloads (Spanish catalog is modest but growing).
- Purchased Audible → `Libation` or similar → strip DRM → M4B.
- Ripped CDs → `m4b-tool` or `AudioBookBinder` to merge + add chapters.
- Existing folder of MP3s → `m4b-tool merge` to produce a single M4B.

### Fallback — folder of MP3 chapters + sidecar

Some books only come as MP3s per chapter. Structure:

```
server/data/audiobooks/
└── Gabriel García Márquez/
    └── El coronel no tiene quien le escriba/
        ├── metadata.json
        ├── 01 - Capítulo 1.mp3
        ├── 02 - Capítulo 2.mp3
        └── 03 - Capítulo 3.mp3
```

```json
// metadata.json
{
  "title": "El coronel no tiene quien le escriba",
  "author": "Gabriel García Márquez",
  "narrator": "…",
  "description": "…",
  "chapters": [
    { "title": "Capítulo 1", "file": "01 - Capítulo 1.mp3" },
    { "title": "Capítulo 2", "file": "02 - Capítulo 2.mp3" }
  ]
}
```

The skill plays the chapter files as an ordered sequence via `ffmpeg` concat; chapter navigation maps to seeking to each chapter's start time.

### Library root

```
server/data/audiobooks/
├── Gabriel García Márquez/
│   ├── Cien años de soledad.m4b                       # preferred
│   └── El coronel no tiene quien le escriba/          # fallback
│       ├── metadata.json
│       └── chapter*.mp3
└── Jorge Isaacs/
    └── María.m4b
```

Configured via `audiobook_library_path` in [`packages/core/src/huxley/config.py`](../../packages/core/src/huxley/config.py), defaults to `data/audiobooks` (relative to `packages/core/`). After stage 4 of the active refactor, this moves into `personas/abuelos/persona.yaml` under the `skills.audiobooks.library_path` key.

## Current state

The skill lives in [`packages/skills/audiobooks/src/huxley_skill_audiobooks/skill.py`](../../packages/skills/audiobooks/src/huxley_skill_audiobooks/skill.py). It's loaded via the `huxley.skills` entry point declared in its `pyproject.toml`. Backed by [`AudiobookPlayer`](../../packages/skills/audiobooks/src/huxley_skill_audiobooks/player.py), a stateless ffmpeg wrapper exposing `probe()` + `stream(path, start_position)`. The skill returns playback as a `ToolResult.audio_factory` closure that the [`TurnCoordinator`](../turns.md) invokes after the model finishes speaking — book audio is forwarded through the same `server.send_audio` channel as OpenAI model audio. Honest audit:

| Capability                                                                              | Status                                                                                                            |
| --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Library scan (filename-based)                                                           | ✅                                                                                                                |
| Fuzzy search (`difflib.SequenceMatcher`) over title + author                            | ✅                                                                                                                |
| `search_audiobooks` tool                                                                | ✅                                                                                                                |
| `play_audiobook` tool with optional `from_beginning`                                    | ✅ (returns `audio_factory`; coordinator fires it after the model's pre-narration)                                |
| Resume on play via `Storage.get_audiobook_position`                                     | ✅                                                                                                                |
| `audiobook_control`: pause / resume / rewind / forward / stop                           | ✅ (seconds-based, not chapters)                                                                                  |
| `resume_last` tool — _"sigue con el libro"_ without naming it                           | ✅ via `LAST_BOOK_SETTING` in storage                                                                             |
| **Audio streams through WebSocket** (not local speakers)                                | ✅ (factory yields PCM → coordinator → `server.send_audio`)                                                       |
| **Closure-captured atomicity for rewind/forward/resume**                                | ✅ new position lives in factory closure; storage only updated when factory actually runs (interrupt-safe)        |
| **Position save on factory cancel + natural EOF**                                       | ✅ generator `finally` block computes `start + bytes_read / BYTES_PER_SECOND` and writes via `Storage`            |
| **PlayerError on `probe()` wrapped in Spanish "déjeme intentarlo otra vez"**            | ✅                                                                                                                |
| **Catalog injected into session prompt** (LLM knows the library without calling search) | ✅ via `prompt_context()` → `SkillRegistry.get_prompt_context()`                                                  |
| **Empty-query `search_audiobooks` returns the full catalog**                            | ✅ (_"¿qué libros tienes?"_ never dead-ends)                                                                      |
| Periodic position save while playing (every 10 s)                                       | ❌ not implemented (no longer needed in practice — finally-block save covers cancel/EOF, only matters on SIGKILL) |
| M4B embedded metadata parsing (read title/author/desc from tags)                        | ❌ uses filename only (ffprobe has it, not wired into catalog)                                                    |
| Chapter navigation (`seek_chapter`)                                                     | ❌ only `seek_time`                                                                                               |
| `list_in_progress()` — books with a saved position                                      | ❌                                                                                                                |
| `describe_current()` — what's playing right now                                         | ❌                                                                                                                |
| Nunca-decir-no wiring on every return path                                              | ⚠️ play errors wired; `search`/`control` still have some bare `{error}`                                           |

## Designed v1 spec

### Tools (all descriptions in Spanish for the LLM)

| Tool                | Parameters                                                                                                                              | Returns                                                                                                              |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `search_audiobooks` | `query: string`                                                                                                                         | Top 5 fuzzy matches: `id, title, author, description, last_position`                                                 |
| `list_in_progress`  | —                                                                                                                                       | Books with saved position > 0, ordered by most recently played                                                       |
| `resume_last`       | —                                                                                                                                       | Starts the most-recently-played book at its saved position, or returns _"no hay nada a medias"_                      |
| `play_audiobook`    | `book_id: string`, `from_beginning?: bool`                                                                                              | Returns title, author, chapter, position + an `audio_factory` the coordinator fires after the model's pre-narration. |
| `describe_current`  | —                                                                                                                                       | What's playing: title, author, chapter name + number, position, duration, remaining                                  |
| `audiobook_control` | `action: pause \| resume \| stop \| seek_time \| seek_chapter`, `seconds?: number`, `chapter_delta?: number`, `chapter_number?: number` | Ok + new position/chapter                                                                                            |

### Natural-language vocabulary — what the user says → what the LLM calls

| He says                                            | LLM calls                                                    |
| -------------------------------------------------- | ------------------------------------------------------------ |
| _"busca el libro del coronel"_                     | `search_audiobooks(query="coronel")`                         |
| _"quiero ese primero"_ / _"el primero"_            | `play_audiobook(book_id=<result[0].id>)`                     |
| _"sigue con el libro"_ / _"el de anoche"_          | `resume_last()`                                              |
| _"pausa"_ / _"detente"_ / _"espera"_               | `audiobook_control(action="pause")`                          |
| _"sigue"_ / _"reanuda"_                            | `audiobook_control(action="resume")`                         |
| _"retrocede un poquito"_                           | `audiobook_control(action="seek_time", seconds=-15)`         |
| _"adelanta un minuto"_                             | `audiobook_control(action="seek_time", seconds=60)`          |
| _"el siguiente capítulo"_                          | `audiobook_control(action="seek_chapter", chapter_delta=1)`  |
| _"el capítulo anterior"_                           | `audiobook_control(action="seek_chapter", chapter_delta=-1)` |
| _"vuelve al principio"_                            | `play_audiobook(book_id=<current>, from_beginning=true)`     |
| _"¿qué estoy escuchando?"_                         | `describe_current()`                                         |
| _"¿qué libros tienes?"_ / _"¿qué me recomiendas?"_ | `list_in_progress()` + `search_audiobooks(query="")`         |

### Resume UX rule

When the user says _"sigue con el libro"_ / _"el de anoche"_ / similar:

1. **Exactly one book** has a saved position → auto-resume, no confirmation. Say _"sigo con 'X' donde lo dejó, don."_
2. **Multiple books** have saved positions → ask _"¿quiere seguir con 'X' o con 'Y'?"_
3. **No book** has a saved position → _"no hay ningún libro a medias. ¿Busco algo?"_ — and wait.

### Position persistence

After the v3 turn-coordinator refactor, position persistence is owned by the playback factory itself, not by the skill's control actions. The factory closure tracks `bytes_read` and writes the terminal position in its `finally` block:

```python
async def stream():
    bytes_read = 0
    try:
        async for chunk in player.stream(path, start_position=start_position):
            bytes_read += len(chunk)
            yield chunk
    finally:
        elapsed = bytes_read / BYTES_PER_SECOND
        await storage.save_audiobook_position(book_id, start_position + elapsed)
```

- **On user interrupt** (PTT pressed mid-book) → coordinator cancels media task → `finally` runs → position saved ✅
- **On natural EOF** (book reaches its end) → generator exits → `finally` runs → position saved ✅
- **On rewind / forward** — the new position lives only in the **factory closure**, never written to storage during dispatch. If the turn is interrupted before the factory runs, storage stays at the old position (interrupt-atomicity for free).
- **On server shutdown** — `_shutdown` calls `coordinator.interrupt()` which cancels the media task → `finally` runs.
- **Periodically while playing** (every 10 s) — ❌ not implemented; only matters under SIGKILL where `finally` blocks don't run.

`last_book_id` persists via `LAST_BOOK_SETTING` in the `settings` table — written by `_play` during dispatch and read by `_control` and `resume_last`.

### Nunca-decir-no wiring

Every tool return path must include a `message` field written for the LLM narrator, in the tone required by [`../vision.md#persona`](../vision.md#persona).

| Scenario                                            | Return payload                                                                                                            |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Search with zero results                            | `{ results: [], available_count: N, message: "No encontré nada con esas palabras, don. ¿Quiere que le diga qué tengo?" }` |
| Search with results                                 | `{ results: [...], message: "Encontré estos libros…" }`                                                                   |
| Play: book not found                                | `{ playing: false, closest: {...}, message: "No tengo ese exacto. Lo más parecido es 'X'. ¿Pongo ese?" }`                 |
| Play: probe / decode error                          | `{ playing: false, message: "Algo pasó con el reproductor. Déjeme intentarlo otra vez." }`                                |
| Resume last: nothing pending                        | `{ resumed: false, message: "No tiene ningún libro a medias. ¿Busco algo?" }`                                             |
| Resume last: ambiguous (N candidates)               | `{ resumed: false, candidates: [...], message: "Tiene varios a medias. ¿Sigue con 'X' o con 'Y'?" }`                      |
| Describe current: nothing playing                   | `{ playing: false, message: "No hay nada sonando ahora. ¿Quiere que ponga algo?" }`                                       |
| Control: invalid action (shouldn't hit, enum-gated) | `{ ok: false, message: "No entendí qué hacer. ¿Pauso o sigo?" }`                                                          |

### Edge cases

- **Library empty** — `search_audiobooks` returns `{ results: [], message: "La biblioteca está vacía. Hay que agregar libros." }`
- **Corrupt file / probe fails** — wrap in Rule 3 of the [nunca-decir-no contract](./README.md#rule-3--errors-wrapped-in-plain-spanish).
- **Saved position > book duration** (book truncated or replaced) — clamp to 0, log a warning, don't fail the tool call.
- **Book renamed on disk** — `book_id` is the relative path, so a rename invalidates the id. Resume won't find it. Acceptable for v0; fix in v2 with a content-hash id if it bites.
- **Very long search query** — truncate to 100 chars before fuzzy matching.
- **Two books with identical filenames under different authors** — the relative path differs, so ids still unique. ✅

## Gaps / TODO for v1

- [ ] M4B embedded-metadata reader (surface `ffprobe`'s `format.tags` into the catalog)
- [ ] Chapter awareness via `ffprobe`'s `chapters` array
- [ ] `seek_chapter` sub-action on `audiobook_control` (chapter_delta / chapter_number)
- [ ] `resume_last` tool + `last_book_id` in `settings` table
- [ ] `list_in_progress` tool
- [ ] `describe_current` tool
- [ ] Periodic position save while playing (every 10 s via a background task)
- [ ] Nunca-decir-no audit: every `search` / `control` return path has a `message` field
- [ ] MP3-folder-with-sidecar fallback support
- [ ] Unit tests for all new tools
- [ ] End-to-end smoke test with a real M4B and the browser client

Roadmap reference: [v1 — the MVP in roadmap.md](../roadmap.md#v1--the-mvp-marios-bar).

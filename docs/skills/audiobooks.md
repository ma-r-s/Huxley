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

Configured in the persona's `skills.audiobooks` block (see [`server/personas/abuelos/persona.yaml`](../../personas/abuelos/persona.yaml)): `library` is a path relative to the persona's `data/` directory (default `audiobooks`). `ffmpeg` / `ffprobe` let a persona pin specific binaries if the PATH defaults aren't right.

## Current state

The skill lives in [`server/skills/audiobooks/src/huxley_skill_audiobooks/skill.py`](../../server/skills/audiobooks/src/huxley_skill_audiobooks/skill.py). It's loaded via the `huxley.skills` entry point declared in its `pyproject.toml`. Backed by [`AudiobookPlayer`](../../server/skills/audiobooks/src/huxley_skill_audiobooks/player.py), a stateless ffmpeg wrapper exposing `probe()` + `stream(path, start_position)`. The skill returns playback as a `ToolResult(side_effect=AudioStream(factory=...))` that the [`TurnCoordinator`](../turns.md) invokes after the model finishes speaking — book audio is forwarded through the same `server.send_audio` channel as OpenAI model audio. Honest audit:

| Capability                                                                               | Status                                                                                                            |
| ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Library scan (filename-based)                                                            | ✅                                                                                                                |
| Fuzzy search (`difflib.SequenceMatcher`) over title + author                             | ✅                                                                                                                |
| `search_audiobooks` tool                                                                 | ✅                                                                                                                |
| `play_audiobook` tool with optional `from_beginning`                                     | ✅ (returns `AudioStream` side effect; coordinator fires it after the model's pre-narration)                      |
| Resume on play via `Storage.get_audiobook_position`                                      | ✅                                                                                                                |
| `audiobook_control`: pause / resume / rewind / forward / stop                            | ✅ (seconds-based, not chapters)                                                                                  |
| `resume_last` tool — _"sigue con el libro"_ without naming it                            | ✅ via `LAST_BOOK_SETTING` in storage                                                                             |
| **Audio streams through WebSocket** (not local speakers)                                 | ✅ (factory yields PCM → coordinator → `server.send_audio`)                                                       |
| **Closure-captured atomicity for rewind/forward/resume**                                 | ✅ new position lives in factory closure; storage only updated when factory actually runs (interrupt-safe)        |
| **Position save on factory cancel + natural EOF**                                        | ✅ generator `finally` block computes `start + bytes_read / BYTES_PER_SECOND` and writes via `Storage`            |
| **PlayerError on `probe()` wrapped in Spanish "déjeme intentarlo otra vez"**             | ✅                                                                                                                |
| **Catalog injected into session prompt** (LLM knows the library without calling search)  | ✅ via `prompt_context()` → `SkillRegistry.get_prompt_context()`                                                  |
| **Empty-query `search_audiobooks` returns the full catalog**                             | ✅ (_"¿qué libros tienes?"_ never dead-ends)                                                                      |
| Resume rewinds 20 s before saved position (avoids mid-sentence cold-start)               | ✅                                                                                                                |
| Human-readable `position_label` in play/seek responses (e.g. "23 minutos y 40 segundos") | ✅                                                                                                                |
| `get_progress` tool — current position, total duration, remaining time, % complete       | ✅ (estimates live position without storage round-trip while playing)                                             |
| `list_in_progress` tool — all books with a saved position > 0                            | ✅                                                                                                                |
| **`book_start` earcon** before book audio begins                                         | ✅ leading PCM bytes yielded by factory; loaded from `server/personas/<name>/sounds/book_start.wav`                      |
| **`book_end` earcon** after natural completion                                           | ✅ trailing PCM bytes yielded by factory before `completed = True`; PTT during chime still records book as done   |
| **`on_complete_prompt` triggers LLM-narrated end-of-book announcement**                  | ✅ persona-overridable text; coordinator creates synthetic IN_RESPONSE turn and calls `request_response`          |
| **Completion silence buffer overlaps with model first-token latency**                    | ✅ `completion_silence_ms` on `AudioStream`; coordinator sends silence AFTER firing `request_response`            |
| **`sounds_enabled` master toggle** to opt persona out of all earcons                     | ✅ `false` clears palette + zeros silence_ms                                                                      |
| **WAV palette loaded via `wave.open()`** (handles non-44-byte headers)                   | ✅ wrong-format files (non-mono / non-24kHz / non-PCM16) silently skipped                                         |
| Periodic position save while playing (every 10 s)                                        | ❌ not implemented (no longer needed in practice — finally-block save covers cancel/EOF, only matters on SIGKILL) |
| M4B embedded metadata parsing (read title/author/desc from tags)                         | ❌ uses filename only (ffprobe has it, not wired into catalog)                                                    |
| Chapter navigation (`seek_chapter`)                                                      | ❌ only seconds-based rewind/forward                                                                              |
| `describe_current()` — what's playing right now                                          | ❌ (use `get_progress` for position info)                                                                         |
| Nunca-decir-no wiring on every return path                                               | ⚠️ play errors wired; `search`/`control` still have some bare `{error}`                                           |

## Designed v1 spec

### Tools (all descriptions in Spanish for the LLM)

| Tool                | Parameters                                                                              | Returns                                                                                                                                                                |
| ------------------- | --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `search_audiobooks` | `query: string`                                                                         | Top 5 fuzzy matches: `id, title, author`                                                                                                                               |
| `list_in_progress`  | —                                                                                       | All books with a saved position > 0: `id, title, author, position_seconds, position_label`                                                                             |
| `resume_last`       | —                                                                                       | Starts the most-recently-played book at its saved position (rewound 20 s), or returns _"no hay nada a medias"_                                                         |
| `play_audiobook`    | `book_id: string`, `from_beginning?: bool`                                              | `{ playing, title, author, position_seconds, position_label, resuming }` + an `AudioStream`. Resumes from 20 s before the saved position unless `from_beginning=true`  |
| `get_progress`      | —                                                                                       | `{ title, author, position_seconds, position_label, playing, total_seconds?, remaining_seconds?, remaining_label?, percent? }` — estimates live position while playing |
| `audiobook_control` | `action: pause \| resume \| stop \| rewind \| forward`, `seconds?: number` (default 30) | `{ playing, title, author, position_seconds, position_label }` + `AudioStream` for seek actions; `CancelMedia` for pause/stop                                          |

### Natural-language vocabulary — what the user says → what the LLM calls

| He says                                            | LLM calls                                                |
| -------------------------------------------------- | -------------------------------------------------------- |
| _"busca el libro del coronel"_                     | `search_audiobooks(query="coronel")`                     |
| _"quiero ese primero"_ / _"el primero"_            | `play_audiobook(book_id=<result[0].id>)`                 |
| _"sigue con el libro"_ / _"el de anoche"_          | `resume_last()`                                          |
| _"pausa"_ / _"detente"_ / _"espera"_               | `audiobook_control(action="pause")`                      |
| _"sigue"_ / _"reanuda"_                            | `audiobook_control(action="resume")`                     |
| _"retrocede un poquito"_                           | `audiobook_control(action="rewind")`                     |
| _"adelanta un minuto"_                             | `audiobook_control(action="forward", seconds=60)`        |
| _"vuelve al principio"_                            | `play_audiobook(book_id=<current>, from_beginning=true)` |
| _"¿cuánto llevo?"_ / _"¿cuánto me queda?"_         | `get_progress()`                                         |
| _"¿qué libros tengo empezados?"_                   | `list_in_progress()`                                     |
| _"¿qué libros tienes?"_ / _"¿qué me recomiendas?"_ | `search_audiobooks(query="")`                            |

### Resume UX rule

When the user says _"sigue con el libro"_ / _"el de anoche"_ / similar:

1. **Exactly one book** has a saved position → auto-resume, no confirmation. Say _"sigo con 'X' donde lo dejó."_
2. **Multiple books** have saved positions → ask _"¿quiere seguir con 'X' o con 'Y'?"_
3. **No book** has a saved position → _"no hay ningún libro a medias. ¿Busco algo?"_ — and wait.

### Position persistence

Position persistence is owned by the playback factory itself. The factory closure tracks `bytes_read` and writes the terminal position in its `finally` block:

```python
async def stream():
    skill._now_playing_id = book_id          # for live get_progress queries
    skill._now_playing_start_pos = start_position
    skill._now_playing_start_time = time.monotonic()
    bytes_read = 0
    completed = False
    try:
        async for chunk in player.stream(path, start_position=start_position):
            bytes_read += len(chunk)
            yield chunk
        completed = True
    finally:
        skill._now_playing_id = None
        elapsed = bytes_read / BYTES_PER_SECOND
        # Natural completion → reset to 0 so next listen starts fresh.
        # Interrupted → save resume point so the user picks up where they left off.
        final_pos = 0.0 if completed else start_position + elapsed
        await set_position(book_id, final_pos)
```

- **On user interrupt** (PTT pressed mid-book) → coordinator cancels media task → `finally` runs → current position saved ✅
- **On natural EOF** → `completed = True` → position reset to 0.0 so next play starts from the beginning ✅
- **On rewind / forward** — the new position lives only in the factory closure. If interrupted before the factory runs, storage keeps the old position (interrupt-atomicity for free).
- **On server shutdown** — `_shutdown` calls `coordinator.interrupt()` → media task cancelled → `finally` runs ✅
- **Periodically while playing** — ❌ not implemented; only matters under SIGKILL.

**Resume rewind**: when loading a saved position for resume, the skill subtracts `RESUME_REWIND_SECONDS` (20 s) so playback begins slightly before the interrupt point, avoiding a cold mid-sentence start.

**Live position tracking**: `_now_playing_id/start_pos/start_time` on the skill instance let `get_progress` estimate the current position as `start_pos + (now - start_time)` without a storage round-trip while audio is streaming.

`last_id` is stored via `LAST_BOOK_KEY` — written by `_play` during dispatch, read by `_control` (resume action) and `resume_last`.

### Nunca-decir-no wiring

Every tool return path must include a `message` field written for the LLM narrator, in the tone required by [`../vision.md#persona`](../vision.md#persona).

| Scenario                                            | Return payload                                                                                                       |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Search with zero results                            | `{ results: [], available_count: N, message: "No encontré nada con esas palabras. ¿Quiere que le diga qué tengo?" }` |
| Search with results                                 | `{ results: [...], message: "Encontré estos libros…" }`                                                              |
| Play: book not found                                | `{ playing: false, closest: {...}, message: "No tengo ese exacto. Lo más parecido es 'X'. ¿Pongo ese?" }`            |
| Play: probe / decode error                          | `{ playing: false, message: "Algo pasó con el reproductor. Déjeme intentarlo otra vez." }`                           |
| Resume last: nothing pending                        | `{ resumed: false, message: "No tiene ningún libro a medias. ¿Busco algo?" }`                                        |
| Resume last: ambiguous (N candidates)               | `{ resumed: false, candidates: [...], message: "Tiene varios a medias. ¿Sigue con 'X' o con 'Y'?" }`                 |
| get_progress: no active or last book                | `{ message: "No hay ningún libro activo. ¿Quiere que busque uno?" }`                                                 |
| Control: invalid action (shouldn't hit, enum-gated) | `{ ok: false, message: "No entendí qué hacer. ¿Pauso o sigo?" }`                                                     |

### Edge cases

- **Library empty** — `search_audiobooks` returns `{ results: [], message: "La biblioteca está vacía. Hay que agregar libros." }`
- **Corrupt file / probe fails** — wrap in Rule 3 of the [nunca-decir-no contract](./README.md#rule-3--errors-wrapped-in-plain-spanish).
- **Saved position > book duration** (book truncated or replaced) — clamp to 0, log a warning, don't fail the tool call.
- **Book renamed on disk** — `book_id` is the relative path, so a rename invalidates the id. Resume won't find it. Acceptable for v0; fix in v2 with a content-hash id if it bites.
- **Very long search query** — truncate to 100 chars before fuzzy matching.
- **Two books with identical filenames under different authors** — the relative path differs, so ids still unique. ✅

## Gaps / TODO

- [x] **End-of-book announcement** — earcon (`book_end.wav`) plays via the stream factory; coordinator then injects `on_complete_prompt` into the conversation and the LLM narrates "el libro terminó, ¿busco otro?" in the persona's tone. Full architecture in [`../sounds.md`](../sounds.md).
- [x] **Sound UX (earcons + completion silence buffer)** — `book_start` plays before book audio; `book_end` plays after natural completion; coordinator sends `completion_silence_ms` of silence concurrently with model first-token latency. All configurable via `server/personas/<name>/persona.yaml`.
- [ ] Playback speed control — elderly users may benefit from 0.8x. ffmpeg `atempo` filter; stored per-session.
- [ ] M4B embedded-metadata reader (surface `ffprobe`'s `format.tags` into the catalog)
- [ ] Chapter awareness via `ffprobe`'s `chapters` array + `seek_chapter` action
- [ ] Periodic position save while playing — only matters under SIGKILL; finally-block covers all normal shutdown paths.
- [ ] Nunca-decir-no audit: every `search` / `control` return path has a `message` field
- [ ] MP3-folder-with-sidecar fallback support
- [ ] Replace Wii BIOS earcons with CC0/original sounds — current `book_start.wav` / `book_end.wav` are derived from copyrighted Nintendo audio. Personal-use only until replaced; ship-blocker if AbuelOS persona is ever distributed.

Roadmap reference: [v1 — the MVP in roadmap.md](../roadmap.md#v1--the-mvp-marios-bar).

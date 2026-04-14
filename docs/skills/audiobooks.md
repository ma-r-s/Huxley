# Skill: audiobooks

The v0 skill. The reason AbuelOS exists. Search, play, navigate, and resume audiobooks by voice.

## Product surface

Grandpa must be able to:

- **Find** a book by natural language (_"busca el libro de García Márquez"_, _"quiero ese del coronel"_).
- **Start** playback from a search result, or have the LLM decide for him if the top match is obvious.
- **Resume** the last-played book automatically (_"sigue con el libro"_).
- **Pause / resume** mid-sentence.
- **Navigate** — back a chapter, forward a minute, _"un poquito atrás"_.
- **Stop** and come back tomorrow to the same second.
- **Hear what's playing** — _"¿qué estoy escuchando?"_
- **Get recommendations** — _"¿qué libros tienes?"_
- Never hit a dead-end _"no."_ See [Nunca-decir-no wiring](#nunca-decir-no-wiring).

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

Configured via `audiobook_library_path` in [`server/src/abuel_os/config.py`](../../server/src/abuel_os/config.py), defaults to `data/audiobooks` (relative to `server/`).

## Current state

The skill exists in [`server/src/abuel_os/skills/audiobooks.py`](../../server/src/abuel_os/skills/audiobooks.py). Backed by [`AudiobookPlayer`](../../server/src/abuel_os/media/audiobook_player.py), which spawns `ffmpeg` to decode to 24 kHz mono PCM16 and streams chunks through the same `AudioServer.send_audio` channel as OpenAI model audio. Honest audit of what works today:

| Capability                                                                              | Status                                                                  |
| --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Library scan (filename-based)                                                           | ✅                                                                      |
| Fuzzy search (`difflib.SequenceMatcher`) over title + author                            | ✅                                                                      |
| `search_audiobooks` tool                                                                | ✅                                                                      |
| `play_audiobook` tool with optional `from_beginning`                                    | ✅                                                                      |
| Resume on play via `Storage.get_audiobook_position`                                     | ✅                                                                      |
| `audiobook_control`: pause / resume / rewind / forward / stop                           | ✅ (seconds-based, not chapters)                                        |
| **Audio streams through WebSocket** (not local speakers)                                | ✅ (via `AudiobookPlayer` → `server.send_audio`)                        |
| **Position save on pause / seek / stop**                                                | ✅ `AudiobooksSkill.save_current_position`                              |
| **Position save on exit_playing (interrupt via wake_word)**                             | ✅                                                                      |
| **Position save on shutdown**                                                           | ✅                                                                      |
| **Absolute seek + rewind/forward with clamping**                                        | ✅                                                                      |
| **`audio_clear` message on seek** (drops client's stale queue)                          | ✅                                                                      |
| **PlayerError wrapped in Spanish "déjeme intentarlo otra vez"**                         | ✅                                                                      |
| **Catalog injected into session prompt** (LLM knows the library without calling search) | ✅ via `prompt_context()` → `SkillRegistry.get_prompt_context()`        |
| **Empty-query `search_audiobooks` returns the full catalog**                            | ✅ (_"¿qué libros tienes?"_ never dead-ends)                            |
| Periodic position save while playing (every 10 s)                                       | ❌ not implemented                                                      |
| M4B embedded metadata parsing (read title/author/desc from tags)                        | ❌ uses filename only (ffprobe has it, not wired into catalog)          |
| Chapter navigation (`seek_chapter`)                                                     | ❌ only `seek_time`                                                     |
| `resume_last()` — _"sigue con el libro"_ without naming it                              | ❌                                                                      |
| `list_in_progress()` — books with a saved position                                      | ❌                                                                      |
| `describe_current()` — what's playing right now                                         | ❌                                                                      |
| Nunca-decir-no wiring on every return path                                              | ⚠️ play errors wired; `search`/`control` still have some bare `{error}` |

## Designed v1 spec

### Tools (all descriptions in Spanish for the LLM)

| Tool                | Parameters                                                                                                                              | Returns                                                                                         |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `search_audiobooks` | `query: string`                                                                                                                         | Top 5 fuzzy matches: `id, title, author, description, last_position`                            |
| `list_in_progress`  | —                                                                                                                                       | Books with saved position > 0, ordered by most recently played                                  |
| `resume_last`       | —                                                                                                                                       | Starts the most-recently-played book at its saved position, or returns _"no hay nada a medias"_ |
| `play_audiobook`    | `book_id: string`, `from_beginning?: bool`                                                                                              | Starts playback; returns title, author, chapter, position. Action: `START_PLAYBACK`             |
| `describe_current`  | —                                                                                                                                       | What's playing: title, author, chapter name + number, position, duration, remaining             |
| `audiobook_control` | `action: pause \| resume \| stop \| seek_time \| seek_chapter`, `seconds?: number`, `chapter_delta?: number`, `chapter_number?: number` | Ok + new position/chapter                                                                       |

### Natural-language vocabulary — what grandpa says → what the LLM calls

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

When grandpa says _"sigue con el libro"_ / _"el de anoche"_ / similar:

1. **Exactly one book** has a saved position → auto-resume, no confirmation. Say _"sigo con 'X' donde lo dejó, don."_
2. **Multiple books** have saved positions → ask _"¿quiere seguir con 'X' o con 'Y'?"_
3. **No book** has a saved position → _"no hay ningún libro a medias. ¿Busco algo?"_ — and wait.

### Position persistence

As of the `AudiobookPlayer` refactor, position save happens on every control action that changes playback state plus on shutdown. Remaining gap: periodic save while playing.

- **On pause** → save ✅
- **On seek** (rewind / forward) → save ✅
- **On stop** → save ✅
- **On shutdown / teardown / exit_playing** → save ✅
- **Periodically while playing** (every 10 s) → ❌ gap for v1

`AudiobookPlayer.position` is computed from `start_position + (bytes_read / BYTES_PER_SECOND)`, so it reflects what has actually been decoded + streamed, not just what has been played on the client (client-side lag is ~100–200 ms).

Still to add: persist **`last_book_id`** in the `settings` table so a future `resume_last` tool can find it without scanning all books.

### Nunca-decir-no wiring

Every tool return path must include a `message` field written for the LLM narrator, in the tone required by [`../vision.md#persona`](../vision.md#persona).

| Scenario                                            | Return payload                                                                                                            |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Search with zero results                            | `{ results: [], available_count: N, message: "No encontré nada con esas palabras, don. ¿Quiere que le diga qué tengo?" }` |
| Search with results                                 | `{ results: [...], message: "Encontré estos libros…" }`                                                                   |
| Play: book not found                                | `{ playing: false, closest: {...}, message: "No tengo ese exacto. Lo más parecido es 'X'. ¿Pongo ese?" }`                 |
| Play: mpv error                                     | `{ playing: false, message: "Algo pasó con el reproductor. Déjeme intentarlo otra vez." }`                                |
| Resume last: nothing pending                        | `{ resumed: false, message: "No tiene ningún libro a medias. ¿Busco algo?" }`                                             |
| Resume last: ambiguous (N candidates)               | `{ resumed: false, candidates: [...], message: "Tiene varios a medias. ¿Sigue con 'X' o con 'Y'?" }`                      |
| Describe current: nothing playing                   | `{ playing: false, message: "No hay nada sonando ahora. ¿Quiere que ponga algo?" }`                                       |
| Control: invalid action (shouldn't hit, enum-gated) | `{ ok: false, message: "No entendí qué hacer. ¿Pauso o sigo?" }`                                                          |

### Edge cases

- **Library empty** — `search_audiobooks` returns `{ results: [], message: "La biblioteca está vacía. Hay que pedirle a Mario que agregue libros." }`
- **Corrupt file / mpv load fails** — wrap in Rule 3 of the [nunca-decir-no contract](./README.md#rule-3--errors-wrapped-in-plain-spanish).
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

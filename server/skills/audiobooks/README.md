# huxley-skill-audiobooks

Local-library audiobook playback for [Huxley](https://github.com/ma-r-s/Huxley). Pause, resume, position-persist; multi-language; ffmpeg-backed.

> **Status**: bundled with the Huxley repo as a workspace member. Installed automatically by `uv sync` from the repo root. The on-PyPI publication path is the same as for any third-party `huxley-skill-*` package — see [`docs/skills/installing.md`](../../docs/skills/installing.md).

## What it does

- **`search_audiobooks`** — "do you have anything by García Márquez" — fuzzy matches title + author against the local library scan.
- **`play_audiobook`** — "play One Hundred Years of Solitude" — resumes from the last persisted position, narrates a chime if the persona opted in.
- **`pause_audiobook`** — "pause the book" — saves position; the next `play_audiobook` resumes from there.

Streaming is via `ffmpeg`: the framework's turn coordinator receives an `AudioStream` side effect from the skill's tool result and pipes the PCM16 frames out through the WebSocket as the model talks (and after it finishes).

## Configure

```yaml
skills:
  audiobooks:
    library_path: "audiobooks/" # resolved against ctx.persona_data_dir
    ffmpeg: "ffmpeg" # PATH lookup; override for non-standard installs
    ffprobe: "ffprobe" # ditto; only used at library scan time
    sounds_path: "sounds" # for the start-of-playback chime
    sounds_enabled: true
    i18n: # per-language tool descriptions
      es:
        search_desc: "Busca un audiolibro en la biblioteca local..."
      en:
        search_desc: "Search the local audiobook library..."
```

Library directory layout:

```
<library_path>/
├── Gabriel García Márquez/
│   ├── Cien años de soledad.m4b
│   └── El coronel no tiene quien le escriba.m4b
└── Jorge Isaacs/
    └── María.m4b
```

`config_schema = None` — i18n maps + filesystem paths don't fit JSON Schema cleanly; v2's PWA falls back to "edit YAML directly."

## Requirements

- `ffmpeg` and `ffprobe` available on the host (`brew install ffmpeg` on macOS; system package manager on Linux).
- Audiobook files in M4B (preferred — preserves chapter metadata) or MP3.

## Development

```bash
uv run --package huxley-skill-audiobooks pytest server/skills/audiobooks/tests
uv run ruff check server/skills/audiobooks
uv run mypy server/skills/audiobooks/src
```

## License

MIT — see [`LICENSE`](LICENSE).

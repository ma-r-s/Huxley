# Changelog

## 0.1.0 — 2026-05-01

Initial release. Bundled with Huxley as a workspace member; `uv sync` from the Huxley repo installs it.

### Added

- `AudiobooksSkill` with three voice tools: `search_audiobooks`, `play_audiobook`, `pause_audiobook`.
- Library scan of M4B/MP3 files at `<library_path>` with author folders.
- Resume-position persistence via `ctx.storage` (`position:<book_id>` keys).
- ffmpeg-backed PCM16 streamer that emits an `AudioStream` side effect for the framework's turn coordinator to play.
- Per-language tool descriptions via the persona's `i18n.<lang>` block.
- `config_schema = None` declared (i18n maps + filesystem paths don't fit JSON Schema).
- `data_schema_version = 1`.

### Notes

- Requires `ffmpeg` and `ffprobe` on the host.
- Library path is resolved against `ctx.persona_data_dir`, not CWD.

# huxley-skill-radio

Internet radio for [Huxley](https://github.com/ma-r-s/Huxley). HTTP/Icecast streams via ffmpeg, with last-played-station resume.

> **Status**: bundled with the Huxley repo as a workspace member.

## What it does

- **`play_radio_station`** — "play radio" / "put on La X" — starts streaming the named station (or the last one if no name given).
- **`list_radio_stations`** — "what stations do you have" — speaks the configured station list.
- **`pause_radio`** — "pause the radio" — stops streaming. The next `play_radio_station` resumes the last station automatically (`last_station_id` persists in `ctx.storage`).

## Configure

```yaml
skills:
  radio:
    ffmpeg: "ffmpeg" # PATH lookup; override for non-standard installs
    language_code: "en" # for tool descriptions
    sounds_path: "sounds"
    start_sound: radio_start # opt-in chime
    stations:
      - id: "lax-radio"
        name: "La X"
        url: "https://stream.example.com/lax.aac"
        language: "es"
      - id: "bbc-world"
        name: "BBC World Service"
        url: "https://stream.live.vc.bbcmedia.co.uk/bbc_world_service"
        language: "en"
```

`config_schema = None` — `stations` is a list-of-records, which v2's PWA form-renderer would render as a deeply nested accordion. v2 falls back to "edit YAML directly" for this skill.

## Requirements

- `ffmpeg` available on the host (`brew install ffmpeg` on macOS; system package manager on Linux).
- Network access to the configured station URLs.

## Development

```bash
uv run --package huxley-skill-radio pytest server/skills/radio/tests
uv run ruff check server/skills/radio
uv run mypy server/skills/radio/src
```

## License

MIT — see [`LICENSE`](LICENSE).

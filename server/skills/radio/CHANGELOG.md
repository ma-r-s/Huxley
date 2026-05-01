# Changelog

## 0.1.0 — 2026-05-01

Initial release. Bundled with Huxley as a workspace member.

### Added

- `RadioSkill` with three voice tools: `play_radio_station`, `list_radio_stations`, `pause_radio`.
- ffmpeg-backed Icecast/HTTP streaming with auto-reconnect on transient drops.
- Last-played station persisted in `ctx.storage` (`last_station_id`) so "play radio" resumes the last channel.
- `start_sound` chime + per-language tool descriptions.
- `config_schema = None` declared (stations is a list-of-records — complex shape).
- `data_schema_version = 1`.

### Notes

- Requires `ffmpeg` on the host.
- Stations declared in persona.yaml under `skills.radio.stations` as a list of `{id, name, url, language}` records.

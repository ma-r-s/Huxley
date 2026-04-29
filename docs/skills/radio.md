# Skill: radio

Internet radio playback. Persona-agnostic; same `AudioStream` machinery as
[audiobooks](./audiobooks.md), minus position tracking — radio is live, so
"where I left off" is meaningless.

## What it does

| Tool            | Args                            | Returns                                                 |
| --------------- | ------------------------------- | ------------------------------------------------------- |
| `play_station`  | `station?: string` (id or name) | `{ playing, station_id, station_name }` + `AudioStream` |
| `resume_radio`  | —                               | Plays the most-recently-played station, or `no_history` |
| `stop_radio`    | —                               | `CancelMedia` — stops the current stream                |
| `list_stations` | —                               | `{ stations[], default, count }`                        |

The skill returns structured JSON; the LLM narrates per its persona's tone.
Optional `PlaySound` chime via `start_sound` config (yielded as the FIRST
chunk of the stream factory — same trick audiobooks uses for `book_start`).

## Data source

**Curated list in `persona.yaml`** — there is no station-discovery layer in
v1. The trade-off:

- **Pros**: zero external deps, install-and-go, the persona owns exactly
  the stations they want and nothing else.
- **Cons**: stream URLs rot over time. When one stops working, look it up
  on [radio-browser.info](https://www.radio-browser.info/) and edit the
  config. We use radio-browser as a one-time lookup to populate the list,
  not as a runtime dependency.

If the persona ever wants ad-hoc discovery (_"ponme algo de jazz"_), an
additive `search_stations(query)` tool integrating radio-browser is the
follow-up. Out of scope for v1.

## Configuration

Persona's `skills.radio` block:

| Key             | Required | Default  | Notes                                                                                                                                                                                                   |
| --------------- | -------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `stations`      | yes      | —        | List of `{id, name, url, description?}`                                                                                                                                                                 |
| `default`       | yes      | —        | Station id played when `play_station` is called with no argument                                                                                                                                        |
| `language_code` | no       | `en`     | Switches tool descriptions between Spanish and English                                                                                                                                                  |
| `ffmpeg`        | no       | `ffmpeg` | Path to ffmpeg binary                                                                                                                                                                                   |
| `start_sound`   | no       | _none_   | Sound palette role (e.g. `radio_start`); omit for no chime                                                                                                                                              |
| `sounds_path`   | no       | `sounds` | Sound palette directory (relative to persona `data_dir` or absolute). Personas typically point this at `../../_shared/sounds` to use the framework-shared palette — see [`../sounds.md`](../sounds.md). |

Example (excerpted from `server/personas/abuelos/persona.yaml`):

```yaml
radio:
  language_code: "es"
  default: caracol
  stations:
    - id: caracol
      name: "Caracol Radio"
      description: "Cadena nacional de noticias y opinión"
      url: "https://playerservices.streamtheworld.com/api/livestream-redirect/CARACOL_RADIOAAC.aac"
    - id: blu
      name: "Blu Radio"
      url: "https://playerservices.streamtheworld.com/api/livestream-redirect/BLURADIO.mp3"
    # ...
  sounds_path: ../../_shared/sounds
  start_sound: radio_start
```

The skill validates at startup: missing `stations` / `default`, empty
station list, station entries missing `id`/`name`/`url`, or `default` not
matching a station id all raise `ValueError` and crash the server with a
clear message — bad config is caught at boot, not at first user interaction.

## Audio plumbing

```
play_station(...) →
  ToolResult(side_effect=AudioStream(factory))
    where factory yields: chime PCM (if start_sound) → ffmpeg PCM stream
```

The ffmpeg subprocess uses `-reconnect 1 -reconnect_streamed 1
-reconnect_delay_max 5` — if the upstream stream drops briefly (which
internet radio does constantly), ffmpeg auto-retries up to 5 seconds.
Beyond that, ffmpeg exits non-zero and the skill raises `PlayerError`.

`-re` throttles ffmpeg to realtime playback rate, which gives natural
WebSocket backpressure without explicit rate-limiting.

`-user_agent "huxley-radio/0.1 (+https://github.com/mario/huxley)"`
identifies us cleanly so server admins aren't blocking us as anonymous bot
traffic. Some radio servers (BBC most notoriously) refuse connections from
default ffmpeg user-agents.

## Storage

Per-skill namespaced KV (`huxley_sdk.SkillStorage`):

- `last_id` → most-recently-played station id

Used by `resume_radio` to restart the same station after a stop.
**Position is NOT tracked** — radio is live, "resume from second 3247" is
meaningless. `resume_radio` is just a shortcut for `play_station(<last_id>)`.

## Persona prompt

Radio needs the same anti-hallucination rule that news + audiobooks have.
Add to the persona's `system_prompt`:

```
RADIO: NUNCA inventes emisoras de radio ni programación. SIEMPRE usa
`play_station` para encender la radio o cambiar de emisora, `stop_radio`
para apagarla, y `resume_radio` cuando pida "sigue con la radio" sin
nombrar emisora. Si pregunta qué emisoras hay, llama a `list_stations`.
La radio es en vivo — no inventes lo que está sonando ahora; si
preguntan dile que no tienes esa información en este momento.
```

The "no inventes lo que está sonando" line is important — without it the
LLM will happily fabricate a current track when asked _"qué está sonando"_,
just like it fabricated news headlines until we forbade it.

## File layout

```
server/skills/radio/
├── pyproject.toml                   # huxley-skill-radio; depends on huxley-sdk
├── src/huxley_skill_radio/
│   ├── __init__.py                  # exports RadioSkill
│   ├── skill.py                     # tool dispatch, ToolResult construction
│   ├── player.py                    # RadioPlayer (ffmpeg HTTP-stream wrapper)
│   └── py.typed
└── tests/
    ├── conftest.py                  # FakeRadioPlayer (canned PCM chunks per URL)
    └── test_skill.py
```

## Honest limitations

- **No `now_playing`**. ICY metadata parsing was deferred — most stations
  send unreliable or no metadata, and parsing it well requires a real
  ffmpeg subprocess upgrade. If the user asks _"qué está sonando"_, the
  LLM should say it doesn't have that information (per the persona prompt).
- **Stream URLs rot**. The curated list will need maintenance every few
  months. The `description` field on each station hints at why we picked
  it, so swapping a dead URL is "find another station with similar
  description."
- **`m3u8` (HLS) streams**. ffmpeg handles them but treats each segment
  as a small reconnection — extra latency on first play. Not all of them
  expose chunked-transfer cleanly. La FM uses HLS in our default config;
  it works but has slightly higher first-play latency than the AAC/MP3
  Icecast streams.
- **Geo-restrictions**. Some stations refuse connections from certain
  IPs. Not much we can do about it from this side.

## Failure modes

| Failure                              | Behavior                                                                          |
| ------------------------------------ | --------------------------------------------------------------------------------- |
| Unknown station id passed            | Returns `{playing: false, error: "unknown_station", available: [...]}`. No chime. |
| Stream server refuses / 4xx / 5xx    | ffmpeg exits non-zero → `PlayerError` raised → coordinator drops the stream.      |
| Stream drops briefly (network blip)  | ffmpeg auto-reconnects (up to 5s). Audio recovers; user hears a glitch.           |
| `start_sound` configured but missing | Logs `radio.start_sound_missing` warning at startup; runs without chime.          |
| User PTT mid-stream                  | Coordinator cancels the media task; ffmpeg killed via SIGTERM.                    |
| Required config missing              | Skill `setup()` raises `ValueError` — server fails to start with clear message.   |

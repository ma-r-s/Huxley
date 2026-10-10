# Skill: spotify

Plays music from a household Spotify Premium account on any Huxley
device. Persona-agnostic; same `AudioStream` path as
[radio](./radio.md): Spotify's audio reaches the client through the
server's one audio channel, never through a separate speaker.

## What it does

| Tool            | Args                                                                  | Returns                                                                      |
| --------------- | --------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `play_music`    | `query: string`, `kind?: any \| track \| artist \| album \| playlist` | `{ ok, playing, by, kind }` + `AudioStream`, or `{ ok: false, reason, say }` |
| `music_control` | `action: pause \| resume \| next \| previous`                         | pause: `CancelMedia`; the others: `AudioStream` (resume)                     |
| `whats_playing` | (none)                                                                | `{ ok, title, by }` + resume `AudioStream`, or `nothing_playing`             |

An artist plays as their songs (up to 10, the first plays and the rest
are queued; Spotify autoplay continues after), because Soloist can't
play an artist URI and Spotify removed the top-tracks endpoint for new
apps.

## How it works

```
Spotify app ─┐                        ┌─ WebSocket (control, events) ─┐
             └► Spotify Soloist ──────┤                               ├─ skill ─► AudioStream ─► client
                (Connect device)      └─ null sink monitor ─► TCP PCM ┘
```

- **Engine: [Spotify Soloist](https://developer.spotify.com/documentation/soloist)**,
  Spotify's official headless Spotify Connect client (Linux only).
  librespot-based players don't work for Premium accounts created since
  December 2024: Spotify refuses their audio keys (see T1.28 in
  `docs/triage.md`).
- **Control:** Soloist's local WebSocket (`soloist.py`): one reconnecting
  connection, a cache of auth and playback state, and an ordered command
  queue. A stream's `finally` queues a `pause` without awaiting it (it
  runs in the coordinator's cancel path); commands from a replaced
  stream (an older generation) are dropped.
- **Audio:** Soloist plays into a PulseAudio null sink whose monitor is
  served over TCP as PCM16 mono 24 kHz (`capture.py`). Each stream opens
  a fresh connection, so nothing stale plays after a pause, and the
  sink's clock paces it.
- **Search:** Spotify Web API with the app's own token
  (client-credentials, renewed hourly). Development mode: the app owner
  must be Premium, at most 5 users, 10 results per search. `market` must
  be set; app tokens can't use `from_token`.
- **Every press stops the music first** (the coordinator cancels the
  stream). So `next`, `previous` and `whats_playing` act and then return a
  resume stream, and "pause" is just `CancelMedia`.
- **Content type:** `NONMIXABLE` with `patience` (default 30 min): a
  reminder or call pauses the music and it comes back afterwards.
  (Ducking would interleave music and speech; see T1.29.)
- **Volume** stays with the client: every play pins Soloist at 100, so a
  Spotify app on a phone can't silently turn the device down.

## Failures (every one is spoken)

| Situation                                     | What happens                                                  |
| --------------------------------------------- | ------------------------------------------------------------- |
| Soloist unreachable                           | `play_music` returns `player_offline`; the model offers radio |
| Soloist not paired                            | `player_not_paired`; offers radio                             |
| Web API app missing or failing                | `search_not_configured` / `search_failed`; offers radio       |
| Nothing found                                 | `not_found`; asks for another song or artist                  |
| Soloist doesn't start playing within 10 s     | stream ends, narrated `start_failed`                          |
| Someone picks another device in a Spotify app | stream ends, narrated `takeover` (no pause sent)              |
| Album / playlist runs out                     | stream ends quietly                                           |

## Configuration

Persona's `skills.spotify` block (all optional):

| Key                | Default               | Notes                                                  |
| ------------------ | --------------------- | ------------------------------------------------------ |
| `soloist_ws`       | `ws://127.0.0.1:9090` | Soloist's WebSocket API (`--ws`)                       |
| `pcm`              | `127.0.0.1:4712`      | `host:port` of the PCM capture                         |
| `market`           | (none)                | ISO country for search; set it so results are playable |
| `start_sound`      | (none)                | Earcon role before the music (first start only)        |
| `sounds_path`      | `sounds`              | Earcon directory, relative to the persona data dir     |
| `patience_minutes` | `30`                  | How long paused music waits to come back               |

Secrets, in `<persona data>/secrets/spotify/values.json` (gitignored):
`client_id`, `client_secret` (Web API app, for search) and
`soloist_api_key` (read by the dev scripts; on Linux it belongs in the
Soloist unit's environment).

## Setup

1. With the **Premium** account that will play, on
   [developer.spotify.com](https://developer.spotify.com/dashboard):
   generate a Soloist API key (Dashboard → Soloist), and create a Web
   API app (redirect URI `http://127.0.0.1:8888/callback`) for its client
   id and secret. Put all three in `values.json`.
2. Run Soloist with a null sink and the TCP capture.
   - **Mac (development):** `server/skills/spotify/dev/` has a container
     that does all of it. Pair once with `pair-on-mac.sh` (it announces
     the device from the Mac, since a container can't join the local
     network's discovery), then `run.sh`. The container downloads the
     current Soloist build from Spotify at each start (builds expire after
     90 days; Spotify forbids redistributing them, so they never go in the
     image or the repo).
   - **Linux (production):** the same as `dev/entrypoint.sh`, as a
     systemd user unit next to the server; pair once with `--pair`.
3. Add `spotify:` to the persona's `skills` block and restart the server.

## Logs

`spotify.setup_complete`, `spotify.ws_state` (connected / retrying),
`spotify.player_state` (logged in, active, status), `spotify.track`,
`spotify.play_requested` (query, match, uri), `spotify.play_started`
(`latency_ms`), `spotify.stream_ended` (`reason`), `spotify.soloist_error`,
`spotify.search_failed`, `spotify.capture_failed`, `spotify.unavailable`.

# Skill: spotify

Plays music from a household Spotify Premium account on any Huxley
device. Persona-agnostic; same `AudioStream` path as
[radio](./radio.md): Spotify's audio reaches the client through the
server's one audio channel, never through a separate speaker.

## What it does

| Tool            | Args                                                                  | Returns                                                                                        |
| --------------- | --------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `play_music`    | `query: string`, `kind?: any \| track \| artist \| album \| playlist` | `{ ok, playing, by, kind }` + `AudioStream`, or `{ ok: false, reason, say }`                   |
| `music_control` | `action: pause \| resume \| next \| previous`                         | pause: `CancelMedia`; the others: `AudioStream` (resume)                                       |
| `whats_playing` | (none)                                                                | `{ ok, title, by, then? }` (info tool: `then` tells the model to resume), or `nothing_playing` |

An artist plays as the artist (`spotify:artist:...`), like the play
button on an artist page: their popular songs, then more. Soloist's docs
list only track, album, playlist and episode URIs, but it plays artist
URIs (verified 2026-10-10). No queueing, so nothing leaks into the next
request.

Soloist's events, as observed (its docs don't show the shapes): a track
item's title is at `decorations.identity.name` and its artists at
`decorations.creators[].entity.decorations.identity.name`. After a
`play`, Soloist first reports `playing` for the _old_ context, then
`context_changed`, `track_changed`, `buffering`, `playing`; and a freshly
started device turns `is_active` only a moment after playback starts. So
a start counts only when the new context is playing, and a takeover only
when the device was active and then wasn't.

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
  stream). So `next` and `previous` act and then return a resume stream,
  and "pause" is just `CancelMedia`. `whats_playing` can't return a
  stream: a tool with an `AudioStream` gets no follow-up round, so its
  answer would never be spoken (seen live). It answers as an info tool
  and its `then` field asks the model to call `music_control(resume)`
  after speaking.
- **Content type:** `NONMIXABLE` with `patience` (default 30 min): a
  reminder or call pauses the music and it comes back afterwards.
  (Ducking would interleave music and speech; see T1.29.)
- **Volume** stays with the client: every play pins Soloist at 100, so a
  Spotify app on a phone can't silently turn the device down.

## Failures (every one is spoken)

| Situation                                     | What happens                                                                                  |
| --------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Soloist unreachable                           | `play_music` returns `player_offline`; the model offers radio                                 |
| Soloist not paired                            | `player_not_paired`; offers radio                                                             |
| Web API app missing or failing                | `search_not_configured` / `search_failed`; offers radio                                       |
| Nothing found                                 | `not_found`; asks for another song or artist                                                  |
| Soloist doesn't start playing within 10 s     | stream ends, narrated `start_failed`                                                          |
| Someone picks another device in a Spotify app | stream ends, narrated `takeover` (no pause sent)                                              |
| The audio feed drops mid-song                 | stream ends, narrated `interrupted`                                                           |
| The audio feed closes before any sound        | narrated `player_offline` (Docker accepts the port even when the sound server inside is down) |
| Paused from a Spotify app for 3 s             | stream ends quietly, freeing the speaker                                                      |
| A call outlasts the 30 min patience           | narrated `patience_expired` (the music stays paused)                                          |
| Album / playlist runs out                     | stream ends quietly                                                                           |

"What song is this?" brings the music back only if a press stopped it in
the last two minutes: not after "pausa", and not after it moved to
another device.

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

The step-by-step guide for any user connecting their own account is on
the docs site: [Connect Spotify](../../web/content/docs/use/connect-spotify.mdx).
In short:

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
`spotify.auth_state` (logged in, active), `spotify.playback` (status,
context), `spotify.track`, `spotify.play_requested` (query, match, uri),
`spotify.play_started` (`latency_ms`, context), `spotify.start_failed`,
`spotify.stream_ended` (`reason`: cancelled, finished, takeover,
paused_elsewhere, capture_closed, start_failed, player_offline),
`spotify.capture_closed` (`chunks`), `spotify.soloist_error`,
`spotify.search_failed`, `spotify.capture_failed`, `spotify.unavailable`,
`spotify.control`.

The Soloist build's expiry is logged by the container at start ("client
expires in N days"); an expired build exits with code 10, which the skill
sees as `player_offline`.

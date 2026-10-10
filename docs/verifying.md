# Verifying a fresh checkout

End-to-end smoke test for a reviewer (or anyone picking up the repo fresh after a big refactor). Walks from `git clone` to a working voice conversation, and calls out what to watch in the logs. Follow top-to-bottom — each step depends on the previous.

## Prerequisites

- Python **3.13+**
- [uv](https://docs.astral.sh/uv/) (package manager)
- [bun](https://bun.sh) (for the web dev client)
- `ffmpeg` + `ffprobe` on PATH (for audiobook decoding)
- An **OpenAI API key with Realtime API access**. This is a real gate — the key must be entitled to the Realtime beta.
- A working microphone and speaker (or headphones).

The framework has been tested on macOS. Linux should work; Windows is untested.

## Install

```bash
git clone <repo-url> huxley
cd huxley
uv sync --all-packages
echo "HUXLEY_OPENAI_API_KEY=sk-..." > .env
```

`--all-packages` matters — workspace skills register their `huxley.skills` entry points only when installed, and without the flag `uv sync` only installs the root.

## Pre-flight — should all be green

Run from the repo root:

```bash
uv run ruff check server/
uv run ruff format --check server/
uv run mypy server/sdk/src server/runtime/src \
            server/skills/audiobooks/src server/skills/system/src
uv run --directory server/sdk pytest -q                        # 10 passed
uv run --directory server/runtime pytest -q                    # 94 passed
uv run --directory server/skills/audiobooks pytest -q          # 40 passed
cd clients/pwa && bun install && bun run check && cd ..        # 0 errors
```

Confirm entry-point discovery:

```bash
uv run python -c "from importlib.metadata import entry_points; \
    print(sorted(ep.name for ep in entry_points(group='huxley.skills')))"
# → ['audiobooks', 'system']
```

## Boot the server

```bash
uv run huxley
```

Expected log lines (in order, within the first second):

```
huxley_starting
storage_initialized   path=.../personas/abuelos/data/abuelos.db
audiobooks.catalog_loaded   count=<N>   path=.../personas/abuelos/data/audiobooks
huxley_ready   skills=['audiobooks', 'system']   tools=[...6 tools...]
[Huxley] Server listening on ws://localhost:8765
state_transition   from_state=IDLE   to_state=CONNECTING   trigger=wake_word
session_connected   model=gpt-realtime-2.1-mini
state_transition   from_state=CONNECTING   to_state=CONVERSING   trigger=connected
```

If you see `connection_failed` with `invalid_request_error.invalid_api_key`, your `HUXLEY_OPENAI_API_KEY` doesn't have Realtime access.

`catalog_loaded count=0` on a fresh clone is expected — the persona's audiobook dir is gitignored. Drop some `.m4b` files into `server/personas/abuelos/data/audiobooks/` (optionally in `Author/Title.m4b` subfolders) and restart to populate.

## Boot the web client

In another terminal:

```bash
cd clients/pwa && bun dev
```

Open `http://localhost:5174`. You should see:

- Status: **"Conectado — mantén el botón para hablar"**
- State badge: **CONVERSING**
- A large button labeled **"Mantén presionado para hablar"**

### On your phone, as an installed app

Phones only grant the microphone over HTTPS. Tailscale Serve gives the dev server a private HTTPS address reachable only from your tailnet (phone needs the Tailscale app, signed in):

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:5174
```

Open `https://<mac-name>.<tailnet>.ts.net:8443` in Safari, then Share → Add to Home Screen. It launches full screen with the Huxley icon. Vite proxies `/ws` to the server, so the page and the voice socket share one origin.

The server takes **one client at a time**: a newer connection evicts the older one (`client_evicted` in the log). A copy that isn't on screen (a Safari tab behind the home-screen app) no longer reconnects until it's shown again, but a desktop tab left open and visible still competes, so close it while testing on the phone. Turn the address off with `tailscale serve --https=8443 off`.

iOS rules the PWA works around (details in `clients/pwa/src/lib/audio/capture.ts`): only a finger *lift* counts as a gesture, so the mic is acquired on the first lift or press of each launch and then kept muted between turns; iOS asks for mic permission once per launch of a home-screen app; reconnects happen automatically when the app returns to the foreground. If a phone misbehaves, start from `client.device` and the `client.mic_*` / `client.playback_*` lines in the server log (see `docs/observability.md`).

### As a native iOS app (Capacitor)

`clients/pwa/ios/` wraps the same web app in a native shell. Why: a home-screen web app asks for the microphone on every launch; the native app asks once per install, and its web view auto-grants the page's mic request and lets audio play without a tap.

Dev loop, no reinstall for web changes: the app loads the live Vite dev server, so edits hot-reload on the phone.

```bash
cd clients/pwa
CAP_SERVER_URL=https://<mac-name>.<tailnet>.ts.net:8443 bun run ios:sync   # point the app at the dev server
bun run ios:open                                                            # Xcode: pick the phone, Run (once)
```

Reinstall only when native config changes (Info.plist, plugins, `capacitor.config.ts`). Without `CAP_SERVER_URL`, `ios:sync` bundles `dist/` (run `bun run build` first). Building needs the iOS platform installed in Xcode (Settings > Components) and, for a real phone, an Apple ID under Xcode > Settings > Accounts.

Signing: put your team in `clients/pwa/ios/local.xcconfig` (gitignored, included by `debug.xcconfig`): `DEVELOPMENT_TEAM = <team id>`. The first device build must be run from Xcode (it creates the certificate and profile); after that the command line works:

```bash
xcodebuild -project ios/App/App.xcodeproj -scheme App -configuration Debug -destination 'id=<device udid>' build
xcrun devicectl device install app --device <device udid> <DerivedData>/Build/Products/Debug-iphoneos/App.app
xcrun devicectl device process launch --terminate-existing --device <device udid> com.marioruiz.huxley
```

A free Apple ID's profile expires after 7 days; re-run the build and install. On a new phone, trust the certificate once: Settings > General > VPN & Device Management.

In the native app the mic is open only while the button is held (`MicCapture.releaseWhenIdle`): holding a muted stream between turns kept iOS in play-and-record, which sends replies to the earpiece and keeps the mic indicator on. Re-opening per press measured 14-45 ms to first frame.

### Testing on the iOS simulator without kicking the phone off

A second server + PWA pair, isolated so it can't load the real persona's Telegram session:

```bash
mkdir -p /tmp/huxley-rig/personas && cd /tmp/huxley-rig
ln -s <repo>/server/personas/basic personas/basic && ln -s <repo>/server/personas/_shared personas/_shared
printf 'HUXLEY_OPENAI_API_KEY=...\nHUXLEY_SERVER_PORT=8766\nHUXLEY_PERSONA=basic\n' > .env
uv run --project <repo> --package huxley huxley
```

Then start the `pwa-sim` launch config (PWA on 5175, proxied to 8766) and open `http://localhost:5175` in the simulator. The macOS Simulator app needs microphone permission the first time.

## Smoke test 1 — info tool path (`get_current_time`)

1. **Hold** the PTT button, say _"¿qué hora es?"_, **release**.
2. Expected: the agent replies with the current time in Spanish.

Watch the log for:

```
coord.ptt_start ... → coord.ptt_stop
coord.tool_dispatch   name=get_current_time   has_audio_stream=False
system.time_query     time=...
coord.response_done   follow_up=True
coord.response_done   follow_up=False   pending_audio_streams=0
coord.turn_summary    reason=ended   tool_calls=1   response_done_count=2
```

Two `response_done` events confirm the chained-round behavior: first round calls the tool, second round narrates the result. `tool_calls=1` + `response_done_count=2` is the signature of a correctly-handled info tool.

## Smoke test 2 — side-effect tool path (audiobook playback)

Requires at least one `.m4b`/`.mp3` in `server/personas/abuelos/data/audiobooks/`.

1. Hold, say _"reproduce [title or author]"_ or _"sigue con el libro"_, release.
2. Expected: the agent says something brief (_"Ahí le pongo el libro."_), then the book audio starts playing.

Watch the log for:

```
coord.tool_dispatch   name=play_audiobook   has_audio_stream=True
audiobooks.factory_built
coord.response_done   follow_up=False   pending_audio_streams=1
coord.audio_stream_started
audiobooks.stream_started   book_id=...   start=...
coord.turn_summary    reason=ended   spawned_audio_stream=True
```

`has_audio_stream=True` + `spawned_audio_stream=True` confirms the side-effect path fired the stream only after the model's acknowledgement finished.

## Smoke test 3 — mid-book interrupt

1. While a book is playing from smoke test 2, **hold PTT** and say anything (e.g. _"pausa"_).
2. Expected: book audio stops immediately; agent acknowledges.

Watch the log for:

```
coord.ptt_start   has_media=True   will_interrupt=True
coord.interrupt   prev_state=applying_factories   has_media=True   pending_audio_streams=0
audiobooks.stream_ended   book_id=...   elapsed=<seconds>   final_pos=<seconds>
```

The `audiobooks.stream_ended` line with a non-zero `final_pos` confirms the playback position was persisted by the factory's `finally` block before cancellation — crucial for `resume_last` to work on the next run.

## Smoke test 4 — resume across restart

1. Kill the server (`Ctrl-C`), restart with `uv run huxley`, refresh the web client.
2. Hold PTT, say _"sigue con el libro"_, release.
3. Expected: the same book resumes near the position where you interrupted it.

This exercises the `audiobooks:last_id` + `audiobooks:position:<book_id>` keys in the namespaced KV storage. If the book restarts from zero, the skill storage layer is broken.

## What "passing" looks like

All four smoke tests work. The log is readable end-to-end. No uncaught exceptions. No `response_cancel_not_active` warnings (regression signal from an earlier bug).

## Known gaps (not regressions)

- **No automated test for the audio path.** Tests cover the coordinator contract + the skill logic up to the factory closure, but the actual WebSocket-PCM-to-browser path has no unit or integration coverage. Manual smoke test 2 is the only verification.
- **Thinking tone is a 440 Hz sine pulse** inside the vocal band — a known sonic-UX violation documented in [`docs/research/sonic-ux.md`](./research/sonic-ux.md). P2 on the roadmap.
- **Audiobook library is gitignored.** Fresh clones have an empty library; drop files in yourself.
- **First PTT press after cold-start** can take ~1–2 s while the OpenAI WebSocket opens. Subsequent presses are immediate.

## Reporting issues

When something fails a smoke test, paste the full relevant log window (from the triggering user action to ~3 seconds later) rather than a screenshot or a narration of what you saw. The logs are structured — see [`observability.md`](./observability.md) — and nearly every failure surface has a purpose-built event. _"I held the button, said X, nothing happened"_ plus 30 lines of log is a diagnosable report; a screenshot of a silent UI isn't.

# launchd deployment

macOS-only. Runs the Huxley server as a user-level background agent so it
starts at login and auto-restarts if it crashes.

## Install

```bash
./scripts/launchd/install.sh
```

Copies `com.huxley.server.plist` to `~/Library/LaunchAgents/` and loads it.
Server starts immediately, logs to `~/Library/Logs/Huxley/huxley.log`.

The plist hardcodes:

- Working directory: `/Users/mario/Projects/Personal/Code/Huxley/packages/core`
- Path to `uv`: `/Users/mario/.local/bin/uv`

If your install paths differ, edit `com.huxley.server.plist` before running install.

## Verify

```bash
launchctl list | grep huxley           # should show com.huxley.server with a PID
tail -f ~/Library/Logs/Huxley/huxley.log
```

## Stop / start without uninstalling

```bash
launchctl unload ~/Library/LaunchAgents/com.huxley.server.plist
launchctl load   ~/Library/LaunchAgents/com.huxley.server.plist
```

## Uninstall

```bash
./scripts/launchd/uninstall.sh
```

Logs at `~/Library/Logs/Huxley/` are kept; remove manually if you want.

## Auto-restart behavior

`KeepAlive` with `Crashed=true, SuccessfulExit=false`:

- Crash (non-zero exit) → restart immediately
- Clean exit (zero) → stay stopped (so manual `launchctl unload` works as expected)
- `ThrottleInterval=10` → minimum 10s between restart attempts (prevents tight
  crash-loops from burning CPU when there's a permanent failure like a missing
  API key)

## When this isn't enough

This is dev/personal-use deployment for a single Mac. Production for a real
walky-talky / ESP32 client would run the server somewhere reachable (a small
home server, a Raspberry Pi, a cheap VPS) — at which point you'd want a real
process supervisor (systemd on Linux) and proper log rotation.

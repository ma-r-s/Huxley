# Production on the Orange Pi

The production server for grandpa's walkie runs on the shared Orange Pi
(plan: [`docs/remote-operation.md`](../../docs/remote-operation.md), W1).

- Code: `~/huxley`, a clone of the private bare repo `~/huxley.git` on the
  Pi (push to it from the Mac: `git push ssh://mario@<pi>/home/mario/huxley.git main`).
- Runtime: `uv` in `~/.local/bin`, Python 3.13 fetched by uv, system
  `libopus0` and `ffmpeg`.
- Config: `~/huxley/server/runtime/.env` (mode 600) and the persona's
  `data/` (DB, secrets, Telegram session), copied from the Mac at the
  cutover. Never run the Mac and the Pi on the same Telegram session or
  Spotify account at once (decision 5). Production's `.env` also sets
  `HUXLEY_PERSONA=abuelos` and `HUXLEY_PERSONA_AT_BOOT=true`: the persona
  starts with the server, not when the walkie first connects, so reminders
  and Telegram work right after a power cut or a deploy. The unit reads no
  other environment file; the server loads this one from its working
  directory.
- Health: `curl -s localhost:8765/health` on the Pi (JSON; `ok` is true
  when the persona runs and its OpenAI session is up; `commit` is the
  running checkout). From the Mac over Tailscale only if
  `HUXLEY_SERVER_HOST` binds the Pi's Tailscale address: `0.0.0.0` would
  also open the unauthenticated voice socket, admin messages included, to
  the house LAN until the device token (W2). Other sources and proxied
  requests get 404; the Cloudflare tunnel must not route `/health`.
  Fields in [`docs/protocol.md`](../../docs/protocol.md#http-health).
- Before relying on reminders right after a restart: T1.44 in
  [`docs/triage.md`](../../docs/triage.md).
- Service: `huxley.service` (system unit, `User=mario`), installed by
  `sudo ~/huxley/scripts/pi/install.sh`: restarts on crash, waits for
  real time after boot, and outranks the Pi's containers for CPU, disk
  and memory.
- Spotify: the Soloist container from `server/skills/spotify/dev/`
  (`run.sh`), with Watchtower kept away from it.

Status 2026-10-12 21:03Z: **production runs here.** The walkie dials
ws://192.168.68.75:8765/ (saved in its NVS; the firmware's compiled
baseline follows in its next build). User units, no sudo (lingering on):
`huxley.service` (the server, commit at /health) and `huxley-watch.service`
(the caregiver page, http://100.75.152.70:8780 on the tailnet). Soloist is
the `huxley-soloist` container (run.sh), logged in from the copied
pairing. Persona data was copied from the Mac with its server stopped;
the local audiobook library (4.8 GB, skill off) stayed on the Mac. The
Mac's server and its Soloist container are stopped (restart=no): never
run grandpa's persona, Telegram session or Spotify account there again.
Open: reserve 192.168.68.75 in the router (DHCP) until the tunnel
hostname replaces the IP; the system unit's priority settings need sudo
once (`install.sh`), optional.

Commands on the Pi:

```bash
systemctl --user status huxley huxley-watch
systemctl --user restart huxley          # at night or when idle: the walkie reconnects in ~3 s
curl -s http://127.0.0.1:8765/health     # ok, persona, recording, walkie_last_seen, commit
tail -f ~/huxley/server/runtime/logs/huxley.log
docker logs -f huxley-soloist
```

Deploy from the Mac: `git push ssh://mario@100.75.152.70/home/mario/huxley.git main`,
then on the Pi `cd ~/huxley && git pull --ff-only && uv sync && systemctl --user restart huxley`.

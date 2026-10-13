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
  `HUXLEY_SERVER_HOST` binds the Pi's Tailscale address (or `0.0.0.0`:
  with the device token set, LAN clients without it get 401 on the voice
  socket). Other sources and proxied requests get 404; the Cloudflare
  tunnel must not route `/health`.
- Production role: production's `.env` sets `HUXLEY_DEVICE_TOKEN` (32+
  random characters; the walkie carries the same token). With it set the
  voice socket wants the token from anything relayed by a proxy or from
  outside loopback and Tailscale (401), the walkie's connection may send
  only what the firmware sends, and the PWA works from the Pi itself or
  over Tailscale only (served from a local address: a page on another
  site gets 403). A PWA never knocks the walkie off: its connect gets
  409 while the walkie is connected. Only a tool can replace it, with
  `?evict=1` and `X-Huxley-Admin: <HUXLEY_ADMIN_TOKEN>` (browsers can't
  send that header on a WebSocket).
  Details: [`docs/protocol.md`](../../docs/protocol.md#authentication-and-roles).
  Fields in [`docs/protocol.md`](../../docs/protocol.md#http-health).
- Firmware updates (T1.43 W4; routes in
  [`docs/protocol.md`](../../docs/protocol.md#http-firmware-and-admin)): the
  walkie pulls images from `~/huxley/server/runtime/firmware/` with the
  device token, `HUXLEY_DEVICE_TOKEN` in the `.env` (unset, the image
  routes answer 503). To offer a build, from the Mac (the image and its
  ELF, named after the version the image carries; the offer refuses a
  misnamed image or a mismatched ELF):

  ```bash
  cd clients/firmware/build
  VER=$(python3 -c "f=open('huxley-firmware.bin','rb'); f.seek(0x30); print(f.read(32).split(b'\0')[0].decode())")
  ssh mario@100.75.152.70 mkdir -p huxley/server/runtime/firmware
  scp huxley-firmware.bin mario@100.75.152.70:huxley/server/runtime/firmware/$VER.bin
  scp huxley-firmware.elf mario@100.75.152.70:huxley/server/runtime/firmware/$VER.elf
  ssh mario@100.75.152.70 "~/huxley/scripts/pi/admin.sh firmware-offer version=$VER"
  # After a test, offer nothing again (an old offer is re-sent to any walkie on another version):
  ssh mario@100.75.152.70 "~/huxley/scripts/pi/admin.sh firmware-offer withdraw=1"
  ```

  It answers `sent: true` when the walkie got `firmware.update` now, or
  `reason: no_device_connected` (it gets it at its next hello). Add
  `force=1` to send an uncommitted build carrying the running version.
  Follow it with `~/huxley/scripts/pi/admin.sh firmware` on the Pi (offer,
  files, the walkie's last hello, the accept flow) and the `firmware.*`
  log lines. A console line: `~/huxley/scripts/pi/admin.sh dev-cmd
  'line=info'`. The admin routes answer only loopback and Tailscale, never
  through a proxy, and need `X-Huxley-Admin: <HUXLEY_ADMIN_TOKEN>` (in the
  server's `.env`, mode 600): `admin.sh` reads it there and hands it to
  curl on stdin, so it never lands in a command line or a log. Without the
  token anything else on this shared box (Home Assistant and two other
  containers share the host's network) or on the tailnet could drive the
  walkie's console. The Cloudflare tunnel routes `/firmware/*` and
  the voice socket, never `/admin` or `/health`.
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

Deploy the server from the Mac: `scripts/pi/deploy.sh --when-idle` (or no
flag: now if the walkie is idle; `--force`: now regardless). It pushes main
to the Pi, which installs exactly that commit once the walkie is idle and
checks `/health`; a newer deploy replaces a waiting one; the result is in
`/tmp/huxley-deploy.result` on the Pi. See the script headers.

The page runs from its own checkout, `~/huxley-watch`: after the push,
`cd ~/huxley-watch && git pull --ff-only && uv sync --package huxley-watch &&
systemctl --user restart huxley-watch` (its unit file says why it doesn't
start through `uv run`).

## On the internet: walkie.ma-r-s.com (W2)

Created 2026-10-12 with the `cf` CLI (Mario's logged-in Cloudflare account):
tunnel `huxley-walkie` (9c9efdac-b9d2-40b3-8e36-b832a9191414, remotely
managed), routing `walkie.ma-r-s.com` -> `http://localhost:8765` with
`/admin` and `/health` answered 404 at Cloudflare, and a proxied CNAME
`walkie.ma-r-s.com` -> `<tunnel id>.cfargotunnel.com`. The connector's
token is in `~/.config/huxley-tunnel/env` on the Pi (mode 600).
`scripts/pi/tunnel.sh` starts the connector (pinned cloudflared, out of
Watchtower). **Live since 2026-10-12 22:07Z** (production role deployed at 22:06, ad6f87d).
Proven from outside: /health and /admin 404 at the edge; /firmware/* 401
without or with a wrong token, 404 (nothing offered) with the walkie's token,
so Authorization reaches the origin; a WSS upgrade without a token gets 401.

Inspect or change the routing from the Mac:

```bash
cf zero-trust tunnels cloudflared configurations get 9c9efdac-b9d2-40b3-8e36-b832a9191414
cf zero-trust tunnels cloudflared get 9c9efdac-b9d2-40b3-8e36-b832a9191414
cf dns records list -z ma-r-s.com
```


## Outside check: huxley-walkie-deadman (Cloudflare Worker)

The Pi can't report its own death, so a Cloudflare Worker (free plan,
created 2026-10-13, source in [`deadman-worker/`](./deadman-worker/)) asks
from outside every 5 minutes: `GET https://walkie.ma-r-s.com/`, 10 s
timeout. Healthy only on **401** (the server wants a device token); 530 is
the Pi or the tunnel connector down, 502 the connector up but the server
down, and any other status or a fetch error fails too. After 2 failed
checks in a row (about 10 minutes) it sends one "Walkie: needs you" push to
huxley-watch's ntfy topic, and one "Walkie: fixed" push when the server
answers again (nothing if it recovered before a push went out). Quiet
22:00 to 07:00 Bogota: a down push waits for the first check after 07:00;
the fixed push is never held. A push that fails is retried at the next
check. Cron only, no public URL.

**Pushes don't get through yet (2026-10-13).** ntfy.sh counts anonymous
publishers by IP, and a Worker publishes from Cloudflare's shared egress
addresses, whose daily quota other users have already spent: the
production rehearsal got `ntfy 429 42908 limit reached: daily message
quota reached` on every try (from the Mac the same code delivers). A free
ntfy.sh account would not help: ntfy counts per account only for paid
tiers. Until Mario picks a channel (a paid ntfy tier, or another service),
the Worker keeps checking and recording state, retries the push every 5
minutes during an outage, and the refusal shows as `push_error` in its log.

- State: one JSON value under the key `state` in the KV namespace
  `huxley-deadman` (`c022afaaf2d34fde88429e76257a3738`): `down_since`,
  `fails`, `notified_down`, `up_since` (only while a fixed push is
  pending), `last_check`, `last_result`. Written once per check (288 a day,
  the free plan allows 1000), before the push, plus once more when a push
  fails. Read it from the Mac:
  `cf kv keys get state --namespace-id c022afaaf2d34fde88429e76257a3738 --text`.
- Logs: one `deadman.check` line per run in Workers Logs (dashboard,
  Workers > huxley-walkie-deadman > Logs).
- Test: `cd scripts/pi/deadman-worker && bun install && bun test` (the
  state machine), then `bun selftest.js` (real fetches and real pushes to
  a fresh throwaway ntfy topic, read back and compared).
- Redeploy: `cd scripts/pi/deadman-worker && bun install && cf deploy`
  (config in `cloudflare.config.ts`). wrangler is pinned to 4.118.0
  because `cf` 0.6.0 expects that version's build output; a newer `cf`
  needs a newer wrangler and a newer config shape.
- The topic is the Worker secret `NTFY_TOPIC`, never in the repo. It is
  declared in `cloudflare.config.ts` because a deploy drops any secret the
  config doesn't name (a first deploy from scratch therefore fails until the
  secret is set: deploy once without the declaration, set it, add it back). Set or
  rotate it by piping it (cf reads a piped secret from stdin; `--text`
  would put it on a command line):

  ```bash
  ssh mario@100.75.152.70 "sed -n 's/^ntfy_topic = \"\(.*\)\"/\1/p' ~/.config/huxley-watch/secrets.toml" \
    | cf workers secrets update NTFY_TOPIC --worker huxley-walkie-deadman --type secret_text
  ```

- Rehearse the alert path in production: set `NTFY_TOPIC` to a throwaway
  topic, add `CHECK_URL: { type: "text", value: "https://walkie.ma-r-s.com/health" }`
  (404 at the edge) to `env` in `cloudflare.config.ts` and deploy; two
  crons later the down push should land in the throwaway topic (read it
  with `curl -s "https://ntfy.sh/<topic>/json?poll=1&since=all"`). Remove
  it, deploy, wait for one healthy check, then pipe the real topic back.

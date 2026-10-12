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
  over Tailscale only. A PWA never knocks the walkie off: its connect gets
  409 while the walkie is connected, unless its URL adds `&evict=1`.
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
  ssh mario@100.75.152.70 "curl -s -H 'X-Huxley-Admin: 1' 'http://127.0.0.1:8765/admin/firmware-offer?version=$VER'"
  ```

  It answers `sent: true` when the walkie got `firmware.update` now, or
  `reason: no_device_connected` (it gets it at its next hello). Add
  `&force=1` to send an uncommitted build carrying the running version.
  Follow it with `curl -s -H 'X-Huxley-Admin: 1'
  http://127.0.0.1:8765/admin/firmware` on the Pi (offer, files, the
  walkie's last hello, the accept flow) and the `firmware.*` log lines.
  A console line: `curl -s -H 'X-Huxley-Admin: 1' --get --data-urlencode
  'line=info' http://127.0.0.1:8765/admin/dev-cmd`. The admin routes
  answer only loopback and Tailscale, never through a proxy, and need the
  `X-Huxley-Admin` header. The Cloudflare tunnel routes `/firmware/*` and
  the voice socket, never `/admin` or `/health`.
- Before relying on reminders right after a restart: T1.44 in
  [`docs/triage.md`](../../docs/triage.md).
- Service: `huxley.service` (system unit, `User=mario`), installed by
  `sudo ~/huxley/scripts/pi/install.sh`: restarts on crash, waits for
  real time after boot, and outranks the Pi's containers for CPU, disk
  and memory.
- Spotify: the Soloist container from `server/skills/spotify/dev/`
  (`run.sh`), with Watchtower kept away from it.

Status 2026-10-12: code and venv in place (runtime tests 775/775 on the
Pi), a staging run with the `basic` persona on 127.0.0.1:8766 connected to
OpenAI with Opus audio; the unit is written, not installed; the cutover
from the Mac has not happened.

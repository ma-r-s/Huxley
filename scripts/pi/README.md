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
  Spotify account at once (decision 5).
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

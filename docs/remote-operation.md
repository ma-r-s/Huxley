# Running the walkie from afar

**Status**: plan, 2026-10-12, reviewed by two critics (server and firmware).
Built so far: log retention, persona at boot and `/health` (W1), recording v1 (W3). Triage: [T1.43](./triage.md) (this plan), [T1.42](./triage.md)
(the caregiver's view, its front page).

**The ask** (Mario, 2026-10-12): the walkie goes to grandpa at his retirement
home, on its Wi-Fi, far from Mario's house. From home Mario must be able to
update its firmware, hear every conversation, see everything that came out of
its speaker (book, music, call, silence), know its battery and health, be told
when something needs him, and keep developing while grandpa uses it. Grandpa
agreed to full recording.

**The rule this plan follows**: after handover nobody can touch the device.
Everything that needs the cable or the open case happens before; everything
else must work, fail and recover through one outbound connection.

## Where things stand (measured 2026-10-12)

| Area               | Today                                                                                                                                                                                                                                                                                                                                                                                   | At the retirement home                                                                                                                |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Server host        | Mac (`uv run huxley`, port 8765; Soloist in Docker on the Mac). The launchd plist isn't installed.                                                                                                                                                                                                                                                                                      | The Orange Pi runs no Huxley yet.                                                                                                     |
| Orange Pi          | Orange Pi 5B, 8 cores, 16 GB (8.9 used, 273 MB free, swap in use), 256 GB eMMC + the media HDD. 31 containers on `:latest` under Watchtower (Immich + ML, Jellyfin, Minecraft, qBittorrent, Home Assistant, Uptime Kuma, 4 cloudflared). 75-81 °C, no fan. 3 resets in 2 days: power cuts (heavy rain). No RTC: after a cut the clock is wrong until NTP. libopus and ffmpeg installed. | Every power cut at Mario's house takes grandpa's walkie down.                                                                         |
| Starting           | Persona at boot built (`HUXLEY_PERSONA_AT_BOOT`, off by default, production sets it); otherwise it starts on the first client connection. `GET /health` built (v1).                                                                                                                                                                                                                     | With it set: up after a cut or a deploy, but a reminder due before the walkie reconnects is spoken to nobody (T1.44).                 |
| Finding the server | `ws://192.168.68.71:8765/` compiled into `secrets.h`.                                                                                                                                                                                                                                                                                                                                   | A LAN IP.                                                                                                                             |
| Server socket      | Plain `ws://`, no authentication (`server.py:470-508`). Admin messages share it (`install_skill`, `restart_server`, `set_skill_secret`); `?persona=` swaps the persona; `list_sessions` hands out transcripts.                                                                                                                                                                          | Exposed as is, anyone could install a package on the server or read grandpa's conversations.                                          |
| One client         | A new connection evicts the old; the walkie then waits for a press before reconnecting (`hux_app.c:601-614`).                                                                                                                                                                                                                                                                           | Mario's PWA on grandpa's server silences grandpa.                                                                                     |
| Accounts           | One Telegram session, one Spotify account, one OpenAI project, one persona DB.                                                                                                                                                                                                                                                                                                          | Dev and prod running at once kill Telegram (duplicate session), fight over Spotify, fire reminders twice, share one budget.           |
| Firmware updates   | `dev.py flash`: the Mac calls the board's door (port 80), the board pulls from a temporary HTTP server on the Mac's LAN IP, `dev.py` confirms after 30 s.                                                                                                                                                                                                                               | Nothing reaches the door behind NAT, the board can't reach the Mac, an unconfirmed image reverts at the next restart.                 |
| Remote console     | `info`, `cmd`, `coredump`, `clips` all go through the door. The firmware ignores `server_event`.                                                                                                                                                                                                                                                                                        | Same.                                                                                                                                 |
| Wi-Fi              | One network compiled in; open networks refused; no list; no roaming. WebSocket retries every 2 s forever.                                                                                                                                                                                                                                                                               | A changed password or a login page and it's dead. During a long outage at Mario's house it would hammer a 4G SIM with TLS handshakes. |
| Hangs              | Task watchdog only logs. F-0023 (go back after repeated crashes) queued. The power rocker isn't reachable with the case closed. A reset while the expander's line is low can still boot download mode (F-0020's fix is software polling).                                                                                                                                               | A hang, or an unlucky reset at low battery, and only a power cut fixes it. Nobody there can do one.                                   |
| Battery            | Not wired (F-0025). No charging detection (the charger's STAT drives only the LED). Never drained to cutoff and recharged. Wi-Fi power save off ("mains-powered").                                                                                                                                                                                                                      | No level, no warning, unknown behaviour at 0 %.                                                                                       |
| Recording          | Recorder built (W3 v1), off in every persona. Transcripts (user via whisper-1, assistant text) logged and kept per session; `transcript` and cost lines carry no turn id.                                                                                                                                                                                                               | Nothing to listen to.                                                                                                                 |
| Logs               | Per-run files, last 10 kept, pruned at each start (`logging.py:33-75`). ~15 MB a day.                                                                                                                                                                                                                                                                                                   | A few days of restarts erase what happened.                                                                                           |
| Backups            | A DB snapshot only when the app starts, same disk, 7 kept.                                                                                                                                                                                                                                                                                                                              | A Pi that dies takes grandpa's data with it.                                                                                          |
| Cost               | Kill switch at USD 20/day, then auto-reconnect resumes; the day rolls over at 19:00 in Colombia.                                                                                                                                                                                                                                                                                        | "Credits out" means a beeping device.                                                                                                 |
| Code               | `main` 31 commits ahead of `origin`.                                                                                                                                                                                                                                                                                                                                                    | The firmware grandpa runs exists only on this Mac.                                                                                    |

## Target shape

```
retirement home                         internet                      Mario's house (on a UPS)
walkie --2.4 GHz--> their router --> wss://<host> (Cloudflare edge) --tunnel--> production box
                                                                              |- huxley (grandpa's persona, starts at boot)
                                                                              |- recordings (Ogg/Opus) + logs by date
                                                                              |- huxley-watch: alerts + the caregiver page (Tailscale)
                                                                              '- Soloist, cloudflared, firmware images
                                                         healthchecks.io <-- pinged every few minutes (alerts if silent)
                                                         Mac: dev server, its own accounts, the twin board
```

## Decisions

1. **Production stays at Mario's house, on a UPS.** Not a VPS: Spotify ties the
   account to a country (and the audiobook market with it), the Telegram
   userbot and Soloist do worse from datacenter IPs, and recordings stay
   home. Not at the retirement home: no space, no access. Cost: grandpa's
   walkie depends on Mario's power and internet, so a UPS on the box, router
   and modem with a clean shutdown when it runs low, an outside dead-man
   check, and a 4G failover for the home internet if outages outlast the UPS
   (the tunnel dials out, so the carrier's shared NAT is fine).
2. **Production runs on the shared Orange Pi** (Mario, 2026-10-12; a
   dedicated box was recommended and declined). So Huxley is made the box's
   first priority: `CPUWeight`, `IOWeight`, `Nice`, `OOMScoreAdjust=-500` in
   its unit, a data directory of its own on the eMMC, and Huxley, Soloist and
   its cloudflared out of Watchtower (pinned versions, updated by hand). If
   the heartbeat later shows stutter while Immich ML, Jellyfin or Minecraft
   are busy, those move, not Huxley.
3. **The walkie reaches it through a Cloudflare Tunnel over `wss://`** (the
   pattern the Pi already runs four times), behind a Cloudflare Access
   service token, plus Huxley's own device token. No port forwarding, no
   Tailscale on the ESP32 (no client). cloudflared pinned to a version and
   out of Watchtower (every update drops grandpa's connection). TLS 1.2
   minimum on the zone (the firmware has TLS 1.3 off); the host exempt from
   Bot Fight Mode and WAF challenges; no certificate pinning.
4. **Production grants by token, never by address.** Tunneled connections
   arrive from loopback (cloudflared shares the network namespace), so an
   address check would give the internet admin. On production the device
   token can send only what the firmware sends (`audio` and binary Opus,
   `ptt_start`, `ptt_stop`, `wake_word`, `client_event`, `ping`); `?persona=`
   is ignored; every admin message is off. Config changes go through git and
   a deploy.
5. **Dev and prod never share an account.** Dev gets its own OpenAI project,
   its own persona data, its own Spotify login, and Telegram off (or its own
   number). Production's OpenAI project gets a hard budget limit and
   auto-recharge so "credits out" can't happen silently.
6. **Updates are pulled through the server link, and an image keeps itself
   only after it proves the next update will work** (see W4). No recovery
   partition, no secure boot, no flash encryption, no anti-rollback, no eFuse
   burned: each is a brick risk with nothing to gain for one device.
7. **Record voices, timeline the rest.** Every press (mic), every spoken
   answer, both legs of every call: the Opus packets exactly as they crossed
   the wire, in Ogg files. Books, music, radio, earcons, the waiting cue:
   what, when, how it ended, as log lines, no audio (about 6x the storage,
   Spotify's audio, and nothing a replay can't show).
8. **No new database.** The server writes audio files and log lines, nothing
   else; logs are kept by date and never pruned. The caregiver page builds a
   disposable cache from them. No writes on the audio path beyond the files,
   no migrations in the server.
9. **The caregiver's view is one calm page plus a plain archive** (T1.42), in
   a separate process (`huxley-watch`) so it keeps alerting when the voice
   server is down and can never glitch audio. Tailscale only. It never opens
   the voice socket.
10. **The twin board goes first.** The original Huxley board (a0:f2:62:e2:fb:60)
    runs every firmware against the dev server over the same public path;
    the walkie in the case is 44:1b:f6:85:26:80.

## Workstreams

### W1. Production server

- The shared Pi, hardened per decision 2; UPS with NUT shutdown; an RTC
  battery if the board takes one.
- systemd unit: `Restart=always`, `After=time-sync.target` with
  `systemd-time-wait-sync` (reminders use the clock), waits on the instance lock.
- **Start the persona at boot**: done 2026-10-12. `HUXLEY_PERSONA_AT_BOOT=true`
  with `HUXLEY_PERSONA` in production's `.env` (off by default, so dev
  keeps the lazy boot); a failed boot leaves the server up and the next
  connect retries. "Pinned" (ignoring `?persona=`) comes with the
  production role (W2, decision 4). Before relying on it: T1.44 (a
  reminder due before the walkie reconnects is spoken to nobody; a
  provider connect that fails at boot waits for the walkie's
  `wake_word`, so the deploy script should poll `/health`, not judge one
  sample).
- **`/health`** (local and Tailscale only): v1 done 2026-10-12, `GET /health`
  on the voice port, 404 for other sources ([protocol](./protocol.md#http-health)):
  persona up, OpenAI session, skills as setup left them (`ok`, `failed`,
  `disabled`), client, walkie last seen, recording, running commit. Not
  yet: Telegram connected and Soloist up need per-skill probes. The tunnel
  must not route `/health` (tunneled requests arrive from loopback).
- Deploy script: pulls a tag, `uv sync`, restarts at night or when Mario
  forces it (he may listen to books for hours), checks `/health` and the
  running commit, rolls back to the previous tag if it fails. Prompt changes
  ship the same way, after replaying recorded presses on the dev server.
- Logs kept: done 2026-10-12 (archives are no longer pruned to 10 runs;
  `HUXLEY_LOG_KEEP_DAYS` sets an age limit, 0 keeps all; about 5 GB a year
  raw, far less gzipped).
- Backups: a nightly timer copies the DB, the Telegram session, secrets,
  recordings and firmware ELFs to the Mac and an encrypted cloud copy. A
  restore drill: rebuild production on the Mac and repoint the tunnel in
  under an hour (the walkie dials a hostname, so it needs no change).
- Cost: thresholds recalibrated from a week of real data; the day in
  grandpa's timezone; a quota refusal gets a spoken clip, not endless beeps.

### W2. Accounts and reach

- The account split (decision 5).
- Tunnel, Access token (non-expiring or rotated over the live link with an
  overlapping second token; alert before expiry), device token, the
  production role (decision 4). Tailscale key expiry off on the box. One
  existing tunnel's token is in a container's command line: move it to an env file.

### W3. Recording

A framework mechanism, opt-in per persona (`recording: {enabled: true}`),
over the three streams the framework already owns (mic, speaker, events).

**v1 built 2026-10-12** (`huxley.server.recorder`, `ogg_opus.py`; events
and file layout in [observability](./observability.md#recordings)). Off in
every persona: grandpa's gets it at the production cutover.

Built:

- **Tap what was written, not what was handed over**: speaker packets are
  recorded in `_drain_opus` as each is written, so audio dropped by
  `audio_clear` or a cancel is not in the file. Audio sent with no client
  is logged now (`server.tx.audio_dropped_no_client`, rate-limited, with a
  count). Mic packets are recorded raw in `_on_opus_packet`, before
  decoding (not `_on_audio_frame`, which only sees PCM), so presses that
  never committed (T1.35) and packets that fail to decode are kept.
- **Opus packets stored as they are**, in Ogg (own muxer, granule from each
  packet's TOC). PCM clients skipped, said once per connection.
- **Sources** (framework words), from the control frames the client got:
  `claim` (label: its title or skill), `stream` (label), `model`, `sound`.
  Files for voices only: mic per press and per claim, `model` per
  `model_speaking` span, `claim` per claim (the peer leg); `stream` and
  `sound` are log lines. Found in the 2026-10-12 log: `model_speaking`
  is a "speaker owned" flag (streams and claim pumps raise it too) and
  arrives about 0.3 ms before `stream_started`, so a stream's or claim's
  first packets looked like model audio; a `model` segment under 0.5 s
  old and under 1 s of audio when a stream or claim starts is retagged
  to it.
- **Recording follows the current persona**: the runtime hands the server
  the new persona's recorder (or none) when a swap commits, so neither
  persona's audio lands in the other's folder.
- **Writer thread** with a bounded queue (3000 packets) that drops and
  counts (never blocks the event loop; control frames are never dropped).
  Files written as `.ogg.part`, fsynced and renamed when closed. On the
  persona's data dir: on the Pi that must be the eMMC/SSD, never the HDD.
- **One `recorder.segment` log line per segment** (source, label, start,
  end, duration, packets, bytes, path, end reason, `cut_by_clear`,
  dropped, `conn_id`).

Left:

- ~~Speech over a parked stream~~: done 2026-10-12 (`coord.content_paused`):
  an injected turn or a call that parks or evicts a playing stream now
  sends `stream_ended`, and its resume `stream_started`, so the model's
  speech then is recorded as `model` and the walkie stops showing
  "playing".
- **Turn ids**: `model` segments carry no response id or turn yet (the
  AudioServer doesn't know them); `turn` bound on `transcript` and
  `cost.response_done` lines; tokens per response logged, not only the
  daily total. Until then a file is joined to its transcript by time.
- **Heard, not sent**: the coordinator's playhead estimate
  (`coordinator.py:1476-1488`) gives speaker time; the walkie reports how
  many ms it discarded on each `audio_clear` and its underrun ms in the
  heartbeat. Heard = sent minus discarded, with stutters marked.
- **What the server can't see**: sounds the walkie plays itself (the offline
  cue) go into its black box (W5) and arrive with the next hello.
- Call legs transcribed in a nightly batch (cents a day). Telegram message
  text is the Telegram skill's own log line.
- Size: 40-60 minutes of voice a day is about 3-4 GB a year.

### W4. Firmware updates and the remote console (firmware session builds it)

- **Pull**: the walkie's hello carries version, slot, reset reason, rollback
  reason, coredump summary. The server offers a version per device (twin
  first) with `server_event firmware.update {version, url}`, at night or when
  Mario forces it, never under 40 % battery unless charging. The download
  uses `esp_https_ota` from the same host; the image's own checks are enough
  (no extra hash). Before restarting, the walkie checks it is still idle.
- **An image keeps itself only after all of**: a local self-test (codec up,
  the loopback test with the amp off, mic frames flowing, the lever at rest,
  heap floors), the hello, an HTTPS GET of the update manifest with the OTA
  client's exact settings, one server-initiated close and reconnect, and an
  explicit `ota.accept` from the server. Then 24 h of probation: the server
  alerts if presses stop producing answered turns, and `ota rollback` stays
  available.
- **The rollback image must be able to come back**: the update channel (hello
  identity, `firmware.update`, `ota.accept`) is frozen and handled in every
  state, including a protocol mismatch (today that parks the walkie silently,
  `hux_app.c:472-477`); the server keeps the last two firmware versions
  working; the home's network, the rescue hotspot and the server URI are
  compiled into every image, NVS only adds to them; NVS changes are additive
  (a rollback across an NVS format change wipes it, `main.c:146-150`).
- **Rollback bookkeeping**: the reason (no_server, crash, selftest, manual)
  saved in NVS before rolling back; a crash rollback is never re-offered
  automatically, a no_server one gets one retry; F-0023 required, ignoring
  BROWNOUT, switching slots once only.
- **Remote console through the server**: `server_event dev.cmd {id, line}` ->
  `client_event dev.cmd_result {id, output}`; `dev.py` talks to the box
  (Tailscale), not the board's IP. Core dump summary in the hello, full dump
  uploaded on request and erased once acknowledged. Every flashed ELF kept
  on the box and backed up.
- **The port-80 door compiled out of grandpa's build** (on the home's Wi-Fi
  other residents share the LAN, and its token travels in plain HTTP).

### W5. A walkie that survives alone (firmware session builds it)

- **One escalation ladder**: restart the WebSocket (2 min without the
  server), restart Wi-Fi (10 min), try the next known network, reboot (60
  min), after 3 reboots switch to the other slot if valid. A network counts
  as working when it reached the server, not when it gave an IP (a dead
  login page hands out IPs).
- **Reconnect backoff** 2 s to 60 s with jitter (today a fixed 2 s: a TLS
  handshake every 2 s through every power cut at Mario's house, about 200 MB
  a day on a 4G SIM).
- **A supervisor task** feeds the watchdog only while each part's liveness
  counter advances (app tick, speaker progress, time since the last
  WebSocket receive or pong), logs which part stalled, then lets it reset.
- **Wi-Fi list**: compiled baseline (the home's network, a rescue hotspot,
  Mario's home) plus NVS entries added remotely; `wifi.add` tries the
  network and reverts if it fails; the active and compiled networks can't be
  removed. The rescue hotspot is a family phone's hotspot, pre-stored: anyone
  visiting turns it on, the walkie comes back, Mario pushes the new network.
  (iPhone hotspots need "Maximize Compatibility" for 2.4 GHz.)
- **Server URI in NVS**, kept only once a hello succeeds on it.
- **Black box in RTC memory**: the last ~4 KB of log lines, reset cause,
  rollback reason; sent with the next hello (the PSRAM log queue dies with
  every reboot).
- **Low battery**: below a threshold, say a clip once and deep-sleep with a
  timer wake instead of brownout-looping.
- **Heartbeat** (`client_event device.health`, every 60 s): uptime, heap and
  PSRAM minimums, largest free internal block, rssi, channel, BSSID, Wi-Fi
  disconnect reasons, TLS error codes, time to hello, reconnects, underrun
  ms, discarded ms, volume, quiet, OTA state, battery mV and %, charging.
  Remote log level WARN by default, stats folded into the heartbeat.
- Volume kept in NVS (today a remote reboot resets it to 50). An alert if
  the walkie ever falls back to raw audio (16x the data).
- What grandpa hears: spoken clips (not tones) for no connection, low
  battery, OpenAI refusing. Updates stay silent (they happen at night).
- TLS memory measured during an OTA while connected and playing (both TLS
  sessions use internal RAM today, 137-146 KB free); split binary frames
  through the tunnel tested.

### W6. The caregiver's view and alerts (T1.42)

**Status**: v0 built 2026-10-12 ([`server/watch/`](../server/watch/README.md),
branch `t142-watch`): the visual page Mario approved, `/status.json`, every
rule with a signal today, alerts (Telegram or the log, once on appearing and
once on clearing, held at night, kept across restarts), the bench and test
windows, the dead-man ping. Not yet: the archive (the recorder is in, the
archive is next), battery (F-0025), rollback and probation (W4), `/health`
(W1), Access token and SIM expiry. Not installed on the box yet.

- **`huxley-watch`**, its own process on the box: reads the logs, the
  recordings and `/health`; runs the rules every few minutes; serves the
  page on Tailscale; sends alerts; pings the outside dead-man check.
- **The page** (T1.42 proposal v0): "All good" or "2 things need you", each
  a plain sentence with why and one action; everything healthy folded into
  one quiet line; one small "how he uses it" block (days used, conversations
  a day, hours of books, music, radio, calls). No event lists.
- **The archive**, one click away: day, then conversation, then the audio
  player and transcript, calls included. Mario's "see it with my own eyes".
- **Rules**: walkie offline > 15 min in the day or not seen for days; audio
  cutting up (underrun share); battery < 20 %; crash, rollback, probation
  failure; server restart loop; OpenAI connect or quota failures; spend
  against the balance Mario enters, and the budget limit; audiobook hours
  low; Telegram or Soloist logged out; disk > 80 %; Access token near expiry;
  the 4G SIM near expiry. Each sent once when it appears and once when it
  clears, never repeated, quiet at night.
- **The box itself**: the dead-man check alerts when the box goes silent
  (it also measures how long Mario's outages last).

### W7. Before handover: the cable and the open case

All in one session with the case open, because it never opens again:

- Battery to IO6 by the R78 solder bridge (pads 1-3; decided), calibrated
  at two points against a multimeter; median of idle samples, a Li-ion
  table with hysteresis, 6 dB attenuation (`hux_battery.c:43` uses 12 dB).
  Good for warnings, not a precise %.
- Charging detection: an inline 100K/100K divider from header 5V to IO8 in
  the harness, no board soldering (decided): the only way to know it's charging.
  IO8, not IO5: the tails of IO4/5/6/9 sit 0.7 mm from the holder bridge's
  bar, so a wire there gets crushed; IO8's clears it dressed upward (case
  session, 2026-10-12). A bridge failure's fallback divider goes to IO3 the
  same way.
- Remove R15 (the 0 ohm link from the expander's INT# to GPIO0; the firmware
  never uses INT#): no reset can ever boot download mode again.
- No reset pinhole: Mario dropped it (2026-10-12, "I don't want to make
  any holes or print anything else"). Staff can't reach the board's buttons,
  so a hung walkie has to recover by itself: the supervisor watchdog, the
  escalation ladder and a remote reboot (W5), with the black box saying why.
  Whether staff can restart it at all depends on the power rocker: the
  case design puts it fingertip-reachable in a recess in the left wall,
  the line above says it isn't; Mario is checking his walkie (2026-10-12).
  If it is, the restart is unplug, rocker off, wait, on, plug back in, and
  the card says to always leave it on (off, the cell never charges).
  Either way the firmware must cover every hang.
- Check the cell has a protection board.
- Partition table: OTA slots 2 MB to 4 MB each (image 1.3 MB, 10 MB free),
  NVS 24 KB to 64 KB, coredump 64 KB to 256 KB. Final bootloader. Last USB
  flash. Save a full flash dump and `espefuse summary`.
- Tests: drain to cutoff with the rocker on, plug in, it must boot and
  reconnect by itself; a full realistic day on battery; current with Wi-Fi
  power save off vs on, then decide.
- Charging: a plain USB-C cable into a wall charger (Mario declined the
  magnetic charge-only adapter). A wall charger has no data lines, so nothing
  can pull the reset and boot lines (F-0020): **never charge it from a
  computer**. The staff card says so. Try the staff's own cable in the case
  once before handover: the pocket takes any plug within the USB-C overmold
  limit (12.35 x 6.5 mm) with 0.6 mm spare, and some cheap cables are chunkier.

### W8. Developing from home

- Grandpa won't file bugs: the archive and the alerts are the bug reports.
- A recorded press replays against the dev server (bench.py already injects
  speech): a real mishearing becomes a test.
- Optional, Mario's call: a voice flag ("avísale a Mario que esto no
  funcionó") that marks the turn and alerts him; a daily digest.

## Order

0. **Now, no code**: UPS on the Pi, router and modem; the dead-man check;
   push the code (with the private-data scan); split the dev accounts.
1. **Production server, at home** (W1, W3, the dead-man and offline alerts):
   Mario keeps using the walkie at home and real recordings pile up.
2. **The open case and the cable** (W7), so everything after is built on the
   final flash layout.
3. **Reach** (W2), the twin board over the public path.
4. **Remote updates and survival** (W4, W5).
5. **The caregiver's view and all alerts** (W6).
6. **Dress rehearsal, 7 days**: grandpa's exact build at Mario's house on a
   foreign network (a phone hotspot), public path only, no LAN tools, no
   USB. During it: pull the box's plug mid-book, cut the home internet,
   restart cloudflared mid-answer and mid-call, force a rollback, run the
   restore drill. Exit: no manual touch for 7 days, two remote updates, one
   forced rollback, every alert fired and received.
7. **Handover** with a staff card: what it is, how to charge it, wall
   charger only (never a computer), the MAC (for
   their IT), who to call.

## Open questions for Mario

Answered 2026-10-12: production on the shared Pi (decision 2); the home's
Wi-Fi is a normal password network (compiled into the baseline list; check
2.4 GHz signal in his room on the day; no 4G box unless that fails); the case
opens once more (W7, all of it); push after a private-data scan.

Defaults until Mario says otherwise: alerts through a Telegram bot; dev on
Mario's own Spotify login with Telegram off; offline is said when he presses,
never unprompted.

Still open:
- Is he in the same city? (Timezone is set twice in `persona.yaml`,
  weather is Villavicencio.)
- Handover date; who there can charge it and help.
- The emergency path (F1, filed April, "blocks deploying to grandpa as a
  primary device"): before or after handover?
- Calls record the other person too: tell the family.

## Critic notes

Server critic (2026-10-12): taken: token-based production role (address
checks would give the tunnel admin); the account split; UPS, time-sync and
dead-man first; the page cut to T1.42's one page plus an archive; no new
database (logs plus files); tap what was written, store Opus as is, writer
thread, `.part` files; reuse the coordinator's playhead estimate plus the
walkie's discarded ms; persona at boot and `/health`; off-site backups and a
restore drill; cost limits on a separate OpenAI project; cloudflared pinned
and out of Watchtower; deploy at night; storage estimate corrected (media is
~6x voice, not 100x). Dedicated box: recommended, Mario's call.

Firmware critic (2026-10-12): taken: the confirm proves the next update's
path (B1); the rollback image must be able to come back (B2); R15 removal,
RST pinhole, charge-only adapter (B3; both later dropped by Mario); battery behaviour tested, low-battery
deep sleep, VBUS divider (B4); rollback bookkeeping, one escalation ladder,
backoff, supervisor watchdog, black box, bigger coredump and NVS, token
rotation. Cut on its advice: the recovery partition, secure boot and flash
encryption, captive-portal automation, the server-sent hash.
Corrected: the idle tasks are subscribed to the task watchdog (it logs, it
doesn't reset); the server-URI command always reverts after 15 min, it
doesn't test reachability.

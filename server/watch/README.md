# huxley-watch

The caregiver's view of a Huxley deployment (T1.42, W6 of
[`docs/remote-operation.md`](../../docs/remote-operation.md)): one page you
read at a glance, and alerts that reach your phone once when something
needs you and once when it's fixed.

Its own process, next to the server: it keeps watching when the server
is down or crash-looping, and it can never glitch audio. It **reads,
never writes**, the server's data:

- the logs (`<logs>/huxley.log`, the per-run archives
  `huxley_<YYYYMMDDTHHMMSS>.log`, `<logs>/keep/*.log`), line by line, each
  line once;
- `<logs>/bench.jsonl`: when the firmware tools drove the walkie (see
  "Test windows");
- the persona database, opened read-only (`mode=ro`): this month's
  audiobook seconds (`spotify:book:seconds:<YYYY-MM>`);
- `persona.yaml`: the timezone and `spotify.book_hours_per_month`;
- the pid in the server's instance lock (`<logs>/huxley-<port>.lock`, for
  `huxley.port`, default 8765), to tell a stopped server from a quiet one
  (same box only), or its `/health` once it has one.

It never opens the voice WebSocket (a second client would evict the walkie).

## Run

```bash
uv run --package huxley-watch huxley-watch            # loop every 5 min + the page on 127.0.0.1:8780
uv run --package huxley-watch huxley-watch --once     # one cycle: print the cards and tiles, alert, exit
uv run --package huxley-watch huxley-watch --config ~/.config/huxley-watch/watch.toml
uv run --package huxley-watch pytest server/watch/tests
```

With no config it watches this checkout (`server/runtime/logs`,
`server/personas/abuelos`). Settings: [`watch.example.toml`](./watch.example.toml);
the Telegram bot and the dead-man ping URL go in a separate
[`secrets.toml`](./secrets.example.toml) that must be `chmod 600`. Any
missing optional piece (Telegram, the ping, the OpenAI balance, the
audiobook allowance, the database) turns just that piece off, with one log
line saying so.

The page binds to `127.0.0.1:8780`. To read it from a phone on the
tailnet, set `bind` to the box's Tailscale address. Tailscale only: it
shows who he calls. An IPv6 address works (`[fd7a:...]:8780`). Binding
every address (`0.0.0.0`, `::`, `0`, in any spelling) is refused unless
`allow_any_address = true`.

## The page

Labels, numbers and charts, no sentences (Mario's approved mockup,
2026-10-12). It renders only from the data `/status.json` serves.

- **Top**: a status dot (green, amber, red) and "All good" or "N need you".
  While a test window silences alerts, a hatched dot and "Testing until
  HH:MM" instead of "All good". If the last check is more than about three
  intervals old, a grey dot and "Stopped checking", also flagged
  `"stale": true` in the JSON; the page written to disk by `--once` checks
  its own age the same way: a dead loop never leaves "All good" up. If the
  full page can't be built, the header and the cards still show.
- **Cards**: one per open item: one big number, a 2-3 word label, an
  action chip ("42 h", "Walkie offline", "Check charger").
- **Tiles**: walkie (online since, or away and last seen), Wi-Fi (rssi and
  bars), audio gaps over the last 3 h (meter against the threshold),
  server (up, or "not ready" when its `/health` says so; uptime, the walkie's firmware), battery (percent and volts from `client.system.battery`; "not wired" or "not reported" until a reading),
  OpenAI (today's spend, or the balance left once you enter one).
- **Today, hour by hour**: three lanes from midnight: the walkie connected
  (its connections merged, never summed); him talking (top half) and
  Huxley speaking (bottom half); music, books, radio and calls. Test
  windows hatched ("bench" grey, "test" amber), a dashed "now" line, a
  tooltip on everything.
- **Last 7 days**: stacked hours of music, books, radio and calls, presses
  under each day, days before the logs begin as dashed "no logs".
- **Audiobook hours**: this month's seconds against `books.hours_per_month`
  ("allowance not set" without one).
- **Audio gaps per half hour**: the last 12 hours, every half hour a slot
  (nothing played: an empty slot), the 5 % threshold dashed, bars over it
  in amber. Same arithmetic as `clients/firmware/tools/dev.py link`.
- **Calls**: the week's total and one row per person.

Charts are HTML and CSS, so labels stay readable at phone width; light and
dark follow the system. One small inline script shows tooltips on hover or
tap. Stream kinds come from the skill's own line, logged right after the
stream starts: a `spotify.play_started` on a `spotify:show:` is a book,
other Spotify plays music, `radio.stream_started` radio,
`audiobooks.stream_started` a book; streams pair by `stream_id`, calls are
claims (`server.tx.claim_started`, titled with the contact). A
`stream_started` said again for a stream already playing (the walkie
coming back) changes nothing; an `ended` then a `started` with the same id
is a pause (a reminder or a call parked it): two blocks, only the played
time counted.

Only the walkie counts: presses from its connections, and answers,
streams and calls while it was connected (one client at a time: outside
those stretches they went to another client, or nowhere). Tests in the
logs with the phone app or the PWA don't show up as his use.

## The rules

Each a pure function in `rules.py`, with tests. The Telegram alert carries
the long form (a sentence, why it matters, what to do); the page only the
card.

| Item                   | Opens when                                                                                                     | Clears when                                           |
| ---------------------- | -------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| `walkie.offline`       | the walkie (client.device shell=firmware) away 15 min of daytime (07:00-22:00), or 2 days at all               | it connects                                           |
| `battery.low`          | the walkie's last `system.battery` reading is under 20 % (critical under 10 %)                                 | back at 30 % (an old reading keeps it open)           |
| `audio.gaps`           | gaps 5 % or more of the speaker's playing time over 3 h (at least 2 min played)                                | under 60 % of that threshold                          |
| `server.down`          | the server's process is gone, or its last line is a stop with no app ready after it, for 5 min                 | it runs                                               |
| `server.restarts`      | 3 starts within an hour                                                                                        | fewer in the last hour                                |
| `server.unclean`       | a start with no stop since the previous one (crash, kill, power cut), within the last hour                     | an hour later                                         |
| `skill.<name>`         | `skill.setup_failed` or `disabled_skills` in the running persona                                               | the persona starts it                                 |
| `openai.down`          | 3 failed connects in a row (`connection_failed`; an HTTP 429 counts here, as a rate limit)                     | `session_connected`                                   |
| `openai.quota`         | `insufficient_quota` / `billing_hard_limit_reached`                                                            | a billed answer (`cost.response_done`)                |
| `openai.balance`       | the balance you entered minus the spend since is under USD 5 or 7 days at the last week's pace                 | 20 % past both                                        |
| `openai.spend_today`   | the server's `bug_canary` or `kill_switch` cost threshold today                                                | local midnight                                        |
| `books.hours`          | under 2 h of audiobook time left this month, or `spotify.book_hours_out` / `start_refused cause=book_no_hours` | 20 % past it; 30 s of a book playing; the month turns |
| `spotify.down`         | Spotify's setup failed, Soloist unreachable 10 min, or the player logged out                                   | connected and logged in                               |
| `telegram.down`        | Telegram's setup failed or its credentials are missing                                                         | `telegram.transport.connected`                        |
| `disk.full`            | the disk holding the logs is 80 % full                                                                         | under 75 %                                            |
| `watch.logs`           | the logs can't be found or have nothing in the lookback                                                        | they can                                              |
| `watch.alerts_failing` | 3 alert deliveries failed in a row                                                                             | one goes through                                      |

Every threshold is in `[thresholds]`. With the server down, "walkie
offline" is not said (nothing can connect; the server is the problem).
Battery alerts come from the walkie's own readings (`client.system.battery`, F-0025: after each hello, on every change of percent, every 5 min). With no reading there is no alert.

## Test windows

The firmware session's tools drive the real walkie: `bench.py` (injected
speech, link faults) and `dev.py talk/release/say/tone`. They append to
`<logs>/bench.jsonl`, one line at a run's start (`"end": null`) and one at
its end, same `start` and `what`; a run that never wrote its end counts
for 2 h, and no window lasts more than 6 h (a longer one is capped, with
a log line: a mistyped end must not silence alerts for days).

- `what` starting with "link test": a planned outage. No walkie-offline or
  audio-gap alert inside it (offline time counts from its end); the strip
  still shows the walkie offline, hatched "test".
- anything else ("bench.py ...", "dev.py ..."): the same, and its presses,
  answers, streams and calls are left out of use, the activity lanes and
  the week; hatched "bench".

An item already open when a window starts stays open (no false
"Cleared"); the same holds for an item the server going down, or a quota
error, would otherwise hide. No file: nothing is excluded. A damaged line
is skipped and logged once.

## Alerts

Telegram's Bot API when `telegram_bot_token` and `telegram_chat_id` are
set, otherwise only the watcher's log (`watch.alert_sent channel=log`).
Once when an item appears ("Needs you: ..."), once when it clears
("Cleared: ..."), never repeated. Between 22:00 and 07:00 (`quiet_hours`)
they wait and go out together in the morning; something that appeared and
cleared before its alert went out becomes one "Came and went" line.
Messages go in chunks Telegram accepts (4096 characters); each leaves the
queue only once its chunk is sent. A failed send stays queued for the
next cycle, Telegram's reason is logged, and after 3 in a row the page
shows "Alerts failing". The open items, the queue and the failure count
are kept in `<state_dir>/alerts.json`, so a restart doesn't send anything
twice.

If `healthchecks_url` is set, every cycle pings it: healthchecks.io tells
you when the pings stop, which is how you learn the box itself died.

## Its log

JSON lines in `<state_dir>/huxley-watch.log`, the server's shape:
`watch.cycle` (open items, events read, skipped lines, walkie online,
server alive, bench windows, quiet hours, pending alerts, failures in a
row, ping, db, duration), `watch.item_opened`, `watch.item_cleared`,
`watch.alert_sent`, `watch.alert_held_quiet_hours`, `watch.alert_failed`,
`watch.alert_dropped`, `watch.ping_failed`, `watch.step_failed`,
`watch.db_unavailable`, `watch.logs_missing`, `watch.bench_line_skipped`,
`watch.bench_window_capped`,
`watch.config_invalid`, `watch.secrets_insecure`, `watch.timezone_fallback`,
`watch.telegram_off`, `watch.ping_off`, `watch.book_budget_off`,
`watch.cycle_failed`. Errors are logged by type, never by message: a
message can quote a token or the ping URL.

## Not in v0

- The archive one click away (day, conversation, audio and transcript):
  the recorder is in main now (`recorder.segment` lines); the archive is
  next.
- Charging: waits for the 5V divider on IO8 (W7); then the tile can say "charging".
- The server's `/health` (T1.43 W1): until it exists, a server that hangs
  without exiting looks quiet, not down (its process is still there).
- Rollback, update probation, Access token and 4G SIM expiry: no signal yet.

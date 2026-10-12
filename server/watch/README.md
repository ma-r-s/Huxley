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
  (same box only), or its `/health` once it has one;
- the recorder's Ogg Opus files (`<persona data dir>/recordings/YYYY/MM/DD/`,
  T1.43 W3), for the archive's players (see "The archive").

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
2026-10-12). It renders only from the data `/status.json` serves (the
archive's list, from the data `/day/YYYY-MM-DD.json` serves).

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
  Huxley speaking (bottom half: the model's audio, from
  `coord.audio_start`, never a song that holds the speaker); music, books,
  radio and calls. Test windows hatched ("bench" grey, "test" amber), a
  dashed "now" line, a tooltip on everything. A day picker above it and the
  day's list under it: the archive (below).
- **Last 7 days**: stacked hours of music, books, radio and calls, presses
  under each day, days before the logs begin as dashed "no logs".
- **Audiobook hours**: this month's seconds against `books.hours_per_month`
  ("allowance not set" without one).
- **Audio gaps per half hour**: the last 12 hours, every half hour a slot
  (nothing played: an empty slot), the 5 % threshold dashed, bars over it
  in amber. Same arithmetic as `clients/firmware/tools/dev.py link`.
- **Calls**: the week's total and one row per person.

Charts are HTML and CSS, so labels stay readable at phone width; light and
dark follow the system. An inline script shows tooltips on hover or tap,
and runs the archive (the picker, the panels, the players). Stream kinds
come from the skill's own line, logged right after the
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

## The archive

Each call and its recording, each thing said, and when something played,
one tap from the day strip (Mario: "see each call and hear its
recording"). One list per day in time order, read straight from that
day's logs: nothing is paired with anything, so nothing can be paired
wrong.

- **Day picker** above the strip: previous and next day (stepping over
  days with no logs) and Today, from the oldest line any log file holds.
  The strip redraws for that day and its list opens; the tiles, cards,
  week and charts stay about today.
- **A tap on a block** (or between blocks: the nearest within about
  14 px) opens the day's list under the strip, without leaving the page,
  scrolled to that time with the rows there marked. A tap on a bench or
  test band goes to its rows.
- **The rows**, each at its own time: the time, an icon, then
  - what was said: `transcript` lines, him or Huxley, the text (the only
    prose on the page);
  - a tool the model called: `session.rx.tool_call`, its name and its
    arguments kept short;
  - a recording: a `recorder.segment` line whose file still exists, a
    play button, a plain progress bar (tap to seek) and its length, marked
    mic (his press), model (Huxley) or call; a call's two legs (his side,
    the other side) are two play rows at the call's time;
  - what played: one row per stream (music, books, radio: title, end,
    length) and per call (who, end, length), paired as on the strip.
  A recording sits at its time next to whatever was said then.
  Transcripts have no turn yet (remote-operation W3), and a time-based
  join was tried and dropped: late transcripts (19 s after the press, in
  the 2026-10-12 log) and out-of-order ones would pair the wrong text.
- **Bench**: rows inside a bench window fold into one "bench" row with a
  count, opened with a tap, never mixed into his. A "link test" window
  hides nothing.
- **Gaps**: a thin marker with the length where nothing happened for 10
  minutes or more.
- **Recordings** exist since recording was turned on (2026-10-12 18:31Z);
  earlier days have the words and the tools, no play rows. Only the
  walkie counts, as on the strip: its connections' recordings, and the
  rest while it was connected.

Reading a day (`daylog.py`): only the files whose lines reach that day
(each file's first and last time are indexed, cached by size and mtime),
and in them only the day's lines, plus what was still open at midnight
from the start of its run (the walkie's connection, a stream, a call) and
the ends of streams, calls and recordings up to 6 h after. Time and event
name are read from a line's bytes before any JSON is decoded. A run can
last weeks in one file, so where a day starts in each file is kept, with
what was open there (a handful of lines, not the run's history): asking
for today again reads only today's lines. It is keyed by the file's first
line and dropped past the file's end, so a file rewritten in place (the
runtime reopens `huxley.log` with "w" after a failed rotation) is read
again.
Past days are kept built (8 at most, one entry each) until their files,
recordings or settings change; today is rebuilt each time.

`/day/YYYY-MM-DD.json` is that day as the page draws it: the strip's
markup (the same renderer as today's), the list's markup, and the picker's
fields (date, label, previous and next day). The rows and the strip's
numbers behind them are `Archive.view()` (tests, tools). Gzipped when the
browser asks. A day before the logs, after today, or not a date: 404.

`/audio/<YYYY/MM/DD/file.ogg>` serves a recording read-only, as
`audio/ogg`, with byte ranges (Safari needs them). It resolves only to an
existing `.ogg` file under the recordings dir (`huxley.recordings_dir`,
default `recordings/` next to the persona DB): `..`, an absolute path, a
backslash, an empty part, a `.ogg.part` and a symlink leading out of it are
404. Players use the browser's audio element: Ogg Opus plays in Chrome,
Edge and Firefox, and in Safari 17+ on macOS 14+ (and iOS 17+); on an older
Safari the play button turns red.

The page used to reload itself with a meta refresh; its script now reloads
it when idle (nothing playing, and the list closed on today, or no touch
for 15 minutes), so a recording or a past day is never cut short. A tap
while a recording plays leaves the list as it is. Audio paused or resumed
by the phone itself is followed. The "Stopped checking" check runs every
30 s, and asks `/status.json` before saying so: an old page is not a
stopped watcher. Without JavaScript the meta refresh still applies.

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
`watch.cycle_failed`, `watch.archive_day` (a day built: files read,
rows, rows by kind, bytes, duration), `watch.archive_read_failed`
(and `watch.step_failed step=archive_day` with the day and where). Errors
are logged by type, never by message: a message can quote a token or the
ping URL.

## Not in v0

- Words and recordings side by side, not joined: once `transcript` lines
  carry a turn (remote-operation W3), a press's text can sit on its play
  row. Call legs are not transcribed.
- Charging: waits for the 5V divider on IO8 (W7); then the tile can say "charging".
- The server's `/health` (T1.43 W1): until it exists, a server that hangs
  without exiting looks quiet, not down (its process is still there).
- Rollback, update probation, Access token and 4G SIM expiry: no signal yet.

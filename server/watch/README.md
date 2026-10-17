# huxley-watch

The caregiver's view of a Huxley deployment (T1.42, W6 of
[`docs/remote-operation.md`](../../docs/remote-operation.md)): one page you
read at a glance, alerts that reach your phone once when something
needs you and once when it's fixed, and a phone app of how he uses the
walkie (see "The phone app").

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
  T1.43 W3), for the archive's players (see "The archive");
- with an admin key, OpenAI's Admin API, at most once an hour: what was
  billed and the monthly spend limit (see "OpenAI budget").

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
the alert channel (an ntfy topic or a Telegram bot), the dead-man ping URL and the OpenAI admin key go in a
separate [`secrets.toml`](./secrets.example.toml) that must be `chmod 600`.
Any missing optional piece (the alert channel, the ping, the OpenAI admin key or
limit, the audiobook allowance, the database) turns just that piece off,
with one log line saying so.

The page (and the phone app, `/app/`) binds to `127.0.0.1:8780`. To read it from a phone on the
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
  server (up, or "not ready" when its `/health` says so; uptime, the walkie's firmware; "N restarts today, M unclean" when any start today had no stop before it: power cuts or crashes), battery (percent and volts from `client.system.battery`; "not wired" or "not reported" until a reading),
  OpenAI (this month's budget left, see "OpenAI budget"; without a limit,
  today's spend, or the balance you entered).
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
- **OpenAI budget** (full width, under the gauge): the month's budget left
  as a battery, then daily spend. See "OpenAI budget".
- **Battery, last 24 h** (full width): the walkie's own readings, one
  point per 5 minutes, the line broken where the walkie was offline over
  15 minutes between two readings (a quiet stretch between readings is
  normal: it reads every 20 minutes). The 20 % line dashed, the latest
  reading labelled, a tip per half hour with % and volts.
- **Audio gaps per half hour**: the last 12 hours, every half hour a slot
  (nothing played: an empty slot), the 5 % threshold dashed, bars over it
  in amber. Same arithmetic as `clients/firmware/tools/dev.py link`.
- **Calls**: the week's total and one row per person.
- **Books** (full width, T1.45): his books from the Spotify skill's
  per-book summaries (`spotify:book:summary:<id>` in the persona DB):
  reading now (a bar for the chapter he's on, "ch 5 of 20", how long
  since), finished (a heart if he said he liked it), dropped (started,
  not finished, not the latest, not heard for 21 days), and what the
  curated shelf (`<persona>/books/shelf.json`) still has to offer, a
  chip per category with the titles folded under each. "no shelf yet"
  without the file; titles are data, so they may run past five words.

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

## The phone app

`/app/` (T1.46, redesigned 2026-10-17): his conversations with the walkie,
and how he uses it, as an iPhone home-screen app. No device controls. One
HTML file (`src/huxley_watch/static/app.html`): inline CSS and JavaScript,
no build step, nothing from the internet, so it works on the tailnet
without one. Colours "Ink & paper" (warm paper, deep navy for him, white
for the walkie, burnt orange for what it did), with a dark version that
follows the phone.

**Add it to the iPhone**: on the tailnet, open
`http://<the box's Tailscale address>:8780/app/` in Safari, Share, Add to
Home Screen. It opens full screen as "Walkie", light or dark with the phone.

The header, on every tab: "Abuelo", online or offline since, what's
playing, and the battery (from `/app.json`, every 30 s). Four tabs:

- **Chat** (opens here): the day as a conversation, from
  `/chat/<YYYY-MM-DD|today>.json` (`chat.py`), oldest at the top, opening
  on the latest at the bottom; today is fetched again every 5 s while the
  app is on screen and new messages are added below (a "New messages"
  button when you've scrolled up, "Live" at the end otherwise). Him on the
  right in navy, the walkie on the left in white; each bubble holds the
  words and the recording (play inside it, with its length and time);
  what the walkie did sits above its answer as plain sentences ("Turned on
  Caracol Radio", "Played Leo Dan on Spotify", not tool names); music, a
  book, the radio and calls sit between messages with their times (a call
  with both sides' recordings; the radio pausing for a question and coming
  back is one listening). A press nobody heard says "Didn't catch it" with
  an amber edge; the walkie speaking by itself (a reminder, a call
  announced) is labelled so. The top of a day links to the day before;
  a past day has "Back to today". `#chat/<day>@<minutes>` opens a day on
  one moment, highlighted (the Summary's struggles link there).
- **Summary**: last used, used today against his usual range, struggles
  today (each opens the chat at that moment), playing now, people, the
  week's minutes, favourites, when he usually starts, and every day's
  conversations (each opens that day's chat).
- **Books**: reading now, finished, left halfway, what's left on the shelf.
- **Walkie**: the device's health from `/status.json` (all good or what
  needs you, then walkie, battery, Wi-Fi, server, audio gaps, OpenAI) and
  a link to the full page at `/`.

`chat.py` pairs by time, the way the server logs them: his press starts a
turn with its recording; his words join it up to 30 s later (they're
transcribed after the release, sometimes after the walkie's answer); the
tools, the walkie's words and its recording join within 90 s, unless the
walkie already answered over 20 s before (then it is speaking on its own,
a turn of its own). Built on each ask (about 50 ms for a busy day on the
Pi), never kept: neither the server nor the phone stores a conversation
(the service worker never touches `/chat/`, `/day/` or `/audio/`).

The older details, still true for the Summary:

- **Today** (now the Summary's top): "last used", big ("40 min ago"), with
  the time and what he asked (his words, else the tool and its query, or
  "not heard": a hallucinated transcript is never shown as his words);
  **used today** against his usual range by this time of day, a band and
  a mark, never red, and "walkie offline N% of the day so far" once that
  is 5 % or more of today's daytime; **people**: each person's last call,
  its time and length ("talked 2 d ago", or "called" when it lasted under
  20 s: it didn't connect).
- **The week**: minutes used each of the last 7 days; a day the walkie was
  mostly offline in the daytime is hatched and labelled "walkie offline",
  never drawn as a short bar; the day the logs begin, "logs begin"; days
  before, "no logs". Today's bar says "so far". His usual day as a band
  behind the whole days, never behind today's. **Favourites, 30 days**:
  the artist (minutes of his tracks, from `spotify.track`), the book (with
  its chapter) and the station. **Usually first uses it at**: the median
  first press over the days his usual is read from (the last 28 with full
  logs and the walkie mostly online), once 5 of them have a press.

**Honesty rules** (the design critic's, binding):

- **Little data**: his usual needs 14 days with the walkie mostly online
  (half the daytime, 07:00-22:00, measured from where the logs begin,
  bench windows left out) and full logs,
  from the last 28. Until then "Learning his routine, day N of 14", never a
  range or a percentage. The range is the middle half (25th to 75th
  percentile) of those days' minutes by this time of day.
- **Quiet is not unwell**: each day carries whether the walkie was mostly
  offline and its lowest battery; today carries the device. He is compared
  only with his own range. Nothing infers a mood.
- **Struggles**: a request is a press that reached the model, or one
  dropped as silent (a tap too short to send is none). It is a struggle
  when:
  - **tool error**: a tool raised (`coord.tool_error`) or a skill said it
    couldn't (`spotify.unavailable`, `spotify.book_not_found`,
    `spotify.search_failed`, `spotify.start_failed`,
    `spotify.start_refused`, `telegram.contact_not_found`,
    `telegram.send_contact_not_found`, `telegram.place_call_failed`,
    `telegram.send_failed`, `radio.unknown_station`, `radio.stream_error`,
    `audiobooks.stream_error`, `news.fetch_failed`, `search.error`), within
    60 s of the release and before his next press;
  - **not heard**: a silent press, a commit that came back empty
    (`input_audio_buffer_commit_empty`), his words one of Whisper's
    hallucinations ("Subtitulos ... Amara.org", "Gracias por ver el
    video": shown as "transcribed as ...", never quoted as his words), or
    his words empty with nothing answering within 20 s (the model hears
    the audio, not the transcript: an answer means it heard him);
  - **no answer**: no model audio for his turn (`coord.audio_start
    owner=user`: an injected reminder speaking is not his answer), tool
    call, stream or call within 20 s of the release (a new press first
    counts);
  - **corrected**: his next request begins with "no," ("no!", "no.", a
    bare "no"), or says "esa no" or "ese no", within 60 s of this one's
    last answer ("no tienes otros?", "otra vez" or "otro libro" are asking
    for more, not saying it was wrong);
  - **asked again**: his next request calls the same tool with a similar
    non-empty query (the same words, one inside the other, or 75 % alike)
    within 90 s; with no query on either side (the time, the battery) it is
    two questions, not a repeat.

  Skipping songs (`music_control` next or previous) and `set_volume` repeat
  by design: never "asked again", and no correction is read before or
  after one. His words: transcripts carry no turn, so each of his goes to
  the oldest press still waiting for words; a press waits only once its
  audio was sent (`session.tx.commit`: one input item, transcribed in
  order), and the queue empties when the session or the process starts
  again (`session_connected`, `app.reconnect_succeeded`, `huxley_starting`),
  so a press whose words will never come can't take the next one's. On the
  2026-10-12 logs this paired 304 of 304 presses (1.2 s after the release
  at the median, 19.3 s at most). Every press counts in that pairing, the
  bench's and other clients' too; only the walkie's outside bench windows
  are his. The corrections are words, not meaning: "no, gracias" to an
  offer counts too.
- **Books** (T1.45): **reading now**, each with its author, a chapter bar
  ("ch 3 of 32") and when he last heard it, a heart if he liked it;
  **finished**, a count and a chip each (a heart: liked; struck through:
  didn't); **left halfway** (started, never finished, not the latest, not
  heard for 21 days: never offered again); **on the shelf**, "131 of 134
  left" and a bar per category with what's left, tapping a category opens
  its titles (kept open across the minute's redraw). The same sorting as
  the page's Books card (`views.bookshelf`), so the two never disagree.
- **Timezone**: his (the persona's). When the phone's own zone differs, the
  header says whose ("Bogota time").
- **Privacy**: a day's words are fetched each time (`cache: no-store`; the
  server says `no-store`) and dropped when the day is closed; recordings
  are left to `/` (their players cache the audio). A service worker is
  registered only in a secure context (iOS runs none over plain http on
  the tailnet, and the app needs none); it keeps the app shell and a copy
  of the last `/app.json` with his words taken out (`last_used.said`, each
  struggle's `said` and `detail`), never `/day/` or `/audio/`.

`/app.json`, `schema: 1`, is `app.AppStatus` (TypedDicts in `app.py`, every
field commented there), built each cycle from the page's own inputs and
segments (no second reader of the logs; `talks.py` for the requests):

- `checked_at` (ISO UTC), `checked` (his `HH:MM`), `interval_s`, `stale`
  (as `/status.json`), `timezone`, `utc_offset_min`;
- `today`: `date`; `last_used` (`at`, `clock`, `said`, `heard`, `tool`, `query`) or
  null; `used` (`minutes`, `state` "learning" or "range", `days`, `needed`,
  `learning_day`, `by`, `lo`, `mid`, `hi`, `position` below / within /
  above, `offline_pct`); `people` (`name`, `last_at`, `clock`, `days_ago`,
  `minutes`, `talked`, `calls_30d`); `enjoying` (`kind`, `title`, `by`, `chapter`,
  `chapter_index` from 1, `chapters`, `playing`, `since`, `until`, `at`)
  or null; `device` (`online`, `since`, `battery_pct`, `battery_at`,
  `battery_stale`, `battery_low`, `battery_low_pct`);
- `trends`: `days` (7, oldest first: `date`, `label`, `today`, `logs` full
  / partial / none, `minutes`, `online_share`, `offline`, `battery_min`,
  `requests`, `struggles`); `usual_day` (`lo`, `mid`, `hi`, `days`) or
  null; `favourites` (`days`, `artist`, `book`, `station`: `name`,
  `minutes`, the book's chapter fields); `first_use` (`clock` or null,
  `days`, `needed`);
- `talks`: `requests_today`; `struggles` (today's, oldest first: `at`, `t`
  minutes since his midnight, `said` (null for a hallucination), `tool`,
  `query`, `reasons`, `detail`);
  `days` (as `trends.days`, today first, back to the logs' first day, 40 at
  most);
- `books`: `reading`, `finished`, `dropped` (each `title`, `author`,
  `liked`, `disliked`, `ago`; reading rows also `chapter` from 1,
  `chapters`, `pct`) and `shelf` (`total`, `left`, `categories`: `name`,
  `total`, `left` titles with authors) or null without a shelf file. Added
  to schema 1 (a new key: older app files ignore it).

If it can't be built, `/app.json` is `{"schema": 1, "error": "<type>",
"checked_at": ...}` (and `watch.step_failed step=app`); the page and the
alerts don't depend on it. The files: `/app/` (`text/html`),
`/app/manifest.webmanifest` (`application/manifest+json`), `/app/sw.js`,
`/app/icon-{180,192,512}.png` (from `scripts/watch_app_icons.py`, run once
with `uv run --with pillow`; the 180 px apple-touch-icon is full bleed, iOS
rounds it). They are package data under `src/huxley_watch/static/`, which
hatch's `packages` setting ships with the code.

## The rules

Each a pure function in `rules.py`, with tests. The Telegram alert carries
the long form (a sentence, why it matters, what to do); the page only the
card.

| Item                   | Opens when                                                                                                     | Clears when                                           |
| ---------------------- | -------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| `walkie.offline`       | the walkie (client.device shell=firmware) away 15 min of daytime (07:00-22:00), or 2 days at all; not after a battery cutoff | it connects                                     |
| `battery.low`          | the walkie's last `system.battery` reading is under 20 % (critical under 10 %)                                 | back at 30 % (an old reading keeps it open)           |
| `battery.empty`        | the walkie's last reading carried `cutoff: true` (F-0030) and it's offline; critical, replaces `walkie.offline` | it connects                                           |
| `audio.gaps`           | gaps 5 % or more of the speaker's playing time over 3 h (at least 2 min played)                                | under 60 % of that threshold                          |
| `server.down`          | the server's process is gone, or its last line is a stop with no app ready after it, for 5 min                 | it runs                                               |
| `server.restarts`      | 3 starts within an hour                                                                                        | fewer in the last hour                                |
| `server.unclean`       | a start with no stop since the previous one (crash, kill, power cut), within the last hour                     | an hour later                                         |
| `skill.<name>`         | `skill.setup_failed` or `disabled_skills` in the running persona                                               | the persona starts it                                 |
| `openai.down`          | 3 failed connects in a row (`connection_failed`; an HTTP 429 counts here, as a rate limit)                     | `session_connected`                                   |
| `openai.quota`         | "Credit gone": `insufficient_quota` / `credit_balance_exhausted` (prepaid credit can run out before the limit)  | a billed answer (`cost.response_done`)                |
| `openai.limit`         | "Budget hit": `organization_spend_limit_exceeded` / `project_spend_limit_exceeded` / `organization_usage_limit_exceeded` / `billing_hard_limit_reached` | a billed answer, or the month turns (UTC)             |
| `openai.budget`        | "Budget runs out": at the pace, this month's budget reaches 0 before it resets                                 | it lasts 20 % past the reset                          |
| `openai.balance`       | only with no monthly limit known: the balance you entered minus the spend since is under USD 5 or 7 days at the last week's pace | 20 % past both                                        |
| `openai.spend_today`   | the server's `bug_canary` or `kill_switch` cost threshold today                                                | local midnight                                        |
| `books.hours`          | under 2 h of audiobook time left this month, or `spotify.book_hours_out` / `start_refused cause=book_no_hours` | 20 % past it; 30 s of a book playing; the month turns |
| `spotify.down`         | Spotify's setup failed, Soloist unreachable 10 min, or the player logged out (a refused play or the skill's `spotify.not_logged_in` at once; a bare logged-out answer after 10 min) | connected and logged in; a server restart doesn't clear it until the new server reaches the player |
| `telegram.down`        | Telegram's setup failed or its credentials are missing                                                         | `telegram.transport.connected`                        |
| `disk.full`            | the disk holding the logs is 80 % full                                                                         | under 75 %                                            |
| `watch.logs`           | the logs can't be found or have nothing in the lookback                                                        | they can                                              |
| `watch.alerts_failing` | 3 alert deliveries failed in a row                                                                             | one goes through                                      |

Every threshold is in `[thresholds]`. With the server down, "walkie
offline" is not said (nothing can connect; the server is the problem).
Battery alerts come from the walkie's own readings (`client.system.battery`, F-0025: at each hello, at each press, every 20 min; every 2 min under 10 %). With no reading there is no alert.

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

A push to the phone through [ntfy](https://ntfy.sh) when `ntfy_topic` is
set (free app, no account: subscribe to the topic in the app; the topic is
the secret, so make it long and random). Else Telegram's Bot API when
`telegram_bot_token` and `telegram_chat_id` are set, otherwise only the
watcher's log (`watch.alert_sent channel=log`). An ntfy push is titled
"Walkie: needs you" (high priority) when something opened, "Walkie:
fixed" otherwise, and tapping it opens this page (over Tailscale). The
texts name skills and states, never people.
Once when an item appears ("Needs you: ..."), once when it clears
("Cleared: ..."), never repeated. Between 22:00 and 07:00 (`quiet_hours`)
they wait and go out together in the morning; something that appeared and
cleared before its alert went out becomes one "Came and went" line.
Messages go in chunks the channel accepts (Telegram 4096 characters, ntfy
3500 to stay under its 4096 bytes); each leaves the queue only once its
chunk is sent. A failed send stays queued for the next cycle, the reason
is logged (never the token or the topic), and after 3 in a row the page
shows "Alerts failing". The open items, the queue and the failure count
are kept in `<state_dir>/alerts.json`, so a restart doesn't send anything
twice. The service re-reads it every cycle (`watch.state_reloaded` when
someone else changed it), so a `--once` check run beside it doesn't make
it repeat what that check already sent; a file it can't read is ignored
and its memory kept.

If `healthchecks_url` is set, every cycle pings it: healthchecks.io tells
you when the pings stop, which is how you learn the box itself died.

Cycles are `interval` minutes apart, but while the walkie is marked
offline the wait asks the server's `/health` every 15 s whether a client
is connected, and the next cycle runs as soon as one is
(`watch.walkie_back_early`): a four-minute visit fell between two cycles
and the page never showed it (2026-10-17).

## Its log

JSON lines in `<state_dir>/huxley-watch.log`, the server's shape:
`watch.cycle` (open items, events read, skipped lines, walkie online,
server alive, bench windows, quiet hours, pending alerts, failures in a
row, ping, db, the month's budget left, its limit and where it came from,
spent, the spend's source, the pace, duration), `watch.item_opened`, `watch.item_cleared`,
`watch.alert_sent`, `watch.alert_held_quiet_hours`, `watch.alert_failed`,
`watch.alert_dropped`, `watch.ping_failed`, `watch.step_failed`,
`watch.db_unavailable`, `watch.logs_missing`, `watch.bench_line_skipped`,
`watch.bench_window_capped`,
`watch.config_invalid`, `watch.secrets_insecure`, `watch.timezone_fallback`, `watch.state_reloaded`,
`watch.telegram_off`, `watch.ping_off`, `watch.book_budget_off`,
`watch.openai_admin_off`, `watch.openai_admin_read` (days billed, the
limits read, `limit_error`, duration), `watch.openai_costs_failed` (the
error's type: one line per hourly attempt), `watch.openai_cache_unreadable`,
`watch.openai_cache_write_failed`,
`watch.cycle_failed`, `watch.app_json_built` (debug, every cycle: requests
and struggles today, the usual's state and days, people, days listed,
books being read, bytes, duration), `watch.archive_day` (a day built: files read,
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

## Audiobook hours

Spotify has no API for the hours left (the Web API's audiobook routes are
catalog only; the number is on the account page, in whole hours). So the
page keeps its own count, set once in `[books]`: `hours_per_month` (the
add-on's 15) and `renews_day` (the billing day). Hours left = allowance
minus the book audio the walkie played since the last renewal, from the
logs (`spotify.stream_ended` `audio_ms` of book streams, plus a book still
playing): Spotify bills 1x content time and replays, the same thing.
Bench runs count (a test that plays a book spends real hours). A
confirmed refusal (`spotify.refusal_explained cause=book_no_hours`) forces
0 and opens the card; a bare `start_refused` never does. The count is
exact only if that Spotify account plays nowhere else.


## OpenAI budget

Mario's ask: open the page and see whether OpenAI needs money, like a
battery. OpenAI has no API for the prepaid balance (only the billing page
shows it), but since 2026-07 an account can set a hard monthly spend limit
(organization or project; at the cap the API answers 429
`organization_spend_limit_exceeded` / `project_spend_limit_exceeded`). So
the battery is **this month's budget left: the limit minus what was spent
since the 1st**. Nothing is typed in by hand.

- **The month** is a calendar month in UTC: OpenAI's limits reset "with the
  next monthly cycle" and its Costs API bills in UTC days; its docs name no
  other zone. In Bogota the budget resets at 19:00 on the last day of the
  month, and the chart's days are UTC days.
- **The spend**, per day, says where it came from (the tooltip):
  - *OpenAI billed*: with an admin key (`openai_admin_key` in
    `secrets.toml`, an `sk-admin-...` key from Organization settings >
    Admin keys), `GET /v1/organization/costs` (1-day buckets, the last 60
    days, grouped by project) once an hour, cached in
    `<state_dir>/openai_admin.json` (never the key). Used for every day
    OpenAI has settled (read 6 h or more after the day ended: the route
    reports late); it is the whole organization's spend (or the
    project's, see below), so other apps on the account count too.
  - *server estimate*: the sum of `cost_usd` over the server's
    `cost.response_done` lines that day. Every day without billing. A day
    not yet settled (today, or one that just ended) is *billed +
    estimate*: the larger of what was billed at the read plus the estimate
    since, and the day's whole estimate. Bench runs count: tests spend real
    money.
  - Days the logs don't reach and nothing billed: "no logs" (dashed), and
    the big number says "or less".
- **The limit**: the organization's from `GET
  /v1/organization/spend_limit` (`threshold_amount` in cents, only while
  `enforcement.status` is `enforcing`); with `[openai] project_id`, that
  project's too (`/v1/organization/projects/{id}/spend_limit`, its spend
  from the same buckets), and the one with less left wins (it bites
  first). If the API can't say (no admin key, an error, no limit
  enforced), `[openai] monthly_limit_usd` (Mario's: 30), taken as the
  organization's: against its whole spend. Each limit is read on its own,
  so one failing keeps the other's fresh answer.
- **The pace**: the average of the last 7 finished days that had spend;
  days of nothing don't dilute it, so it errs toward "runs out sooner".
  Today joins once it is already over that average (a day that has barely
  begun never lowers it). The projection runs from now at that pace.
- **A failed read** (HTTP 404, which some accounts get on the costs route;
  401; a timeout; an odd answer) never stops a cycle: the last good answer
  stays, days after it fall back to the estimate, the limit to the config,
  and one `watch.openai_costs_failed` line with the error's type (never its
  text) goes in the log each hourly attempt. The card's header says "Admin
  API HTTP 404" in red. A damaged cache file is dropped and read again. The
  key is never logged, served or cached, and it is sent as an unredirected
  header (urllib copies the others onto a redirect, to any host).

The card: the budget left (big, "of $30, resets 1 Nov", "or less" when
days are missing), used, the pace, and "hits $0" with its date, "hit $0"
with the day it did, or "left at reset"; the chip "Raise limit or wait"
while the projection reaches 0 before the reset (the same numbers as
`openai.budget`, which also keeps a 20 % clearing margin) or `openai.limit`
is open, "Top up" while `openai.quota` is. Under it, the battery: the budget left over the
month from the limit on the 1st, falling with each day's spend, dashed
from now at the pace to the reset, or to "$0 on <date>" if that comes
first; the days ahead shaded. Under that, each day's spend (one series)
with the pace dashed over the days ahead. A tip on every day: the day,
left, spent, and the source. `/status.json` `credit` has every number.

Without a limit (no config value, nothing read), the card shows the
month's spend and the bars, no battery. The older balance mode (`[openai]
balance_usd` and `balance_at`, the balance read on the billing page) still
feeds the tile and `openai.balance`, only while no limit is known.

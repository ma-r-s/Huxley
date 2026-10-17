# Firmware debugging

How to diagnose a problem on a running ESP32-S3 without flying to
Colombia.

## Log conventions

Every log line is one structured event. Format:

```
<LEVEL> (<millis_since_boot>) <component>: <event>.<sub_event> key1=val1 key2=val2
```

Concrete examples:

```
I (6101) hux_net: ws.connected uri=ws://192.168.68.83:8765/
I (6296) hux_app: ws.rx.hello protocol=2 (expected=2)
I (6297) hux_app: state WAITING_HELLO -> READY
W (141020) hux_net: ws.rx.binary len=128 (unexpected)
E (141200) hux_net: ws.error
```

**Rules** (copied from the project-level
[`../../docs/observability.md`](../../docs/observability.md) and
[`../../CLAUDE.md`](../../CLAUDE.md) so firmware work respects the
same convention):

- **Component tag** is the C file's short name (`hux_net`,
  `hux_app`, …). Matches the `TAG` constant in the file.
- **Event name** is dotted: top-level domain first (`ws`, `state`,
  `net`, `audio`), sub-event second. Pick names a future `grep`
  call could match across months of logs.
- **Key-value extras** go after the event name, space-separated,
  `key=val` (no quotes unless the value contains spaces). Numbers
  and constants are printed raw.
- **Transitions log.** Every state change is a single line with
  `from_state` and `to_state` (or the arrow form `A -> B`). If you
  changed state and didn't log, that's a bug.
- **Decisions log.** Every branch that depends on remote state
  (server said X, so we did Y) logs the input + the decision.
- **Unhandled things log.** If a message arrives we don't handle,
  log `ws.rx.<kind> (unhandled)` — silence is a defect.
- **Errors log once, not per retry.** If the WS reconnect loop
  fails 200 times in a row, we want one `ws.reconnect_failed` +
  a counter, not 200 identical lines.

## Two places logs land

### Serial (USB-CDC)

The primary channel. Every log line always hits serial regardless of
level. Connect:

```sh
tools/serial-capture.sh          # background, into /tmp/huxley-debug
tail -f /tmp/huxley-debug/serial-latest.log
echo info > /tmp/huxley-debug/console.fifo    # a console command
```

Don't attach `idf.py monitor`, `cat`, or a terminal program to the
board's port: they toggle DTR/RTS, and on the S3's USB-Serial/JTAG an
RTS reset doesn't re-read the boot pins, so the chip can land in
download mode and stay there (2026-10-12, inside the closed case). The
repo's loggers hold the lines still. Decode a backtrace by hand:
`xtensa-esp32s3-elf-addr2line -pfiaC -e build/huxley-firmware.elf <addrs>`,
or read the coredump (below).

### Stuck in download mode

Symptom: silent, no Wi-Fi, no server connection; the console prints
`rst:0x15 (USB_UART_CHIP_RESET),boot:0x0 (DOWNLOAD(USB/UART0))` and
`waiting for download`. Root cause on this board: GPIO0 (the boot strap)
is wired to the TCA9555 expander's INT# (R15, 0 ohm); INT# stays low
after an input change until the firmware reads that port, so a reset
while it's low boots into download mode. The firmware now reads both
ports every 10 ms (`hux_button`), which keeps GPIO0 high. Also: a host
can reset the chip over USB with the boot pin overridden low, and every
later RTS or JTAG reset keeps the latched download mode (esptool docs: the USB-Serial/JTAG's RTS is a
core reset that "does not re-sample the boot strapping pins"). Fix,
from the Mac: `tools/flash.sh --unstick` (esptool's watchdog reset, a
full system reset that re-reads them). A power cut fixes it too. The
S3 has no switch to refuse a host's reset (later Espressif chips do);
`RTC_CNTL_USB_RESET_DISABLE` doesn't do it.

### Huxley server log (via WebSocket)

When the WS is healthy, firmware `WARN` and `ERROR` lines are
streamed to the server as:

```json
{
  "type": "client_event",
  "event": "huxley.firmware_log",
  "data": {
    "level": "W",
    "tag": "hux_net",
    "line": "ws.rx.binary len=128 (unexpected)",
    "ts": 141020
  }
}
```

The server logs them as `client.huxley.firmware_log` with all `data`
fields as structured kwargs (per the generic `client_event` handler
in [`../../docs/protocol.md`](../../docs/protocol.md)), except that the
board's `level` arrives as `client_level` (the log line's own `level`
is the server's, always `info`). Search the server's structlog output
for `event=client.huxley.firmware_log` to pull every firmware event
from a session, and `client_level` for the board's warnings and errors.

**Limitations**:

- **Nothing is lost to a disconnect** (F-0022). Lines logged while
  the WS is down (and the whole boot, before the first connect) wait
  in a 256-line queue in PSRAM and arrive after the reconnect, late:
  the server's `timestamp` is when it got the line, `ts` is the
  board's uptime in ms when it happened. Past 256 lines the newest
  are dropped and counted (`log.dropped`). ISR lines stay on serial.
- **A reset still loses the queue**, so warnings and errors also go to
  a 4 KB black box in RTC memory (`hux_blackbox.c`, host-tested), which
  a panic, a watchdog reset or a restart leaves alone (a power cut
  doesn't). After the next hello the board sends the previous boot's as
  `client_event device.blackbox {lines, dropped}` (in the server log as
  `client.device.blackbox`; `dropped` = lines the 4 KB ring overwrote)
  and logs `app.blackbox bytes= dropped= sent=`. Each line is
  `<uptime_ms> <W|E> <tag>: <text>`. Kept until a send succeeds.
- The current dev build sets the threshold to `INFO` (in `main.c`:
  `hux_log_set_remote_level('I')`) — the server sees every firmware
  log line. In prod we'll bump back up to `WARN` to bound bandwidth.
- A deny-list prevents logs from `websocket_client` /
  `transport_*` / `esp-tls` / `mbedtls` from streaming — the
  net-layer's own errors would recurse through the sender otherwise.
  Read those on serial.

## Boot banner — what every session starts with

```
I (100) huxley: boot version=<git-hash> mac=XX:XX:XX:XX:XX:XX reset=POWERON heap=237KB psram=7MB
```

Fields:

| Field     | Meaning                                                                                                |
| --------- | ------------------------------------------------------------------------------------------------------ |
| `version` | `git describe --dirty` as of build; `unknown` if no git                                                |
| `mac`     | The STA MAC address                                                                                    |
| `reset`   | ESP32 reset reason — `POWERON`, `BROWNOUT`, `SW_RESET`, `PANIC`, `INT_WDT`, `TASK_WDT`, `DEEPSLEEP`, … |
| `heap`    | Free internal SRAM at boot                                                                             |
| `psram`   | Free PSRAM at boot (only present if PSRAM is up)                                                       |

If you see `reset=PANIC`, the previous boot crashed. Check:

1. RTC-memory panic stash — printed on the next line after the
   boot banner as `last_panic: <reason> pc=0xXXXXXXXX`.
2. Coredump — see below.

## The minute line: `device.health` (F-0027)

Every 60 s while connected the board sends `client_event device.health`
(in the server log as `client.device.health`; fields in
`docs/protocol.md`): memory floors (`heap_internal_min`, the largest
internal block, `psram_min`), the link (`rssi`, `channel`, `bssid`,
`ws_rx_age_ms`), and counts since the previous line that reached the
server: `wifi_disconnects` and `tls_errors` as `{code: count}`,
`reconnects`, `underruns`, `underrun_ms`, `discarded_ms`; then
`ring_used_ms`, `volume`, `quiet_left_s`, `ota_state` and `battery
{mv, pct}` once wired. A line that can't be sent folds into the next
(`health.unsent` once, `health.sent after_unsent=` when one gets
through), so the counts always sum. Read these for trends before
scrolling the log.

## The supervisor: hangs end in a restart (F-0027)

Nobody at the home can restart the walkie, so `hux_super` (priority 10)
feeds the task watchdog only while every part keeps finishing work, and
the watchdog panics (`CONFIG_ESP_TASK_WDT_PANIC`, 10 s): a core dump of
every task, then a restart. A part is stalled when it has work waiting
and finishes none past its limit (`hux_super_check.c`, host-tested), not
when its loop stops turning: a wedged decoder leaves the speaker loop
writing silence.

| Part        | Work done                                   | Waiting          | Limit |
| ----------- | ------------------------------------------- | ---------------- | ----- |
| `app`       | loop wakes (1 s queue timeout)              | always           | 30 s  |
| `esp_timer` | a 1 s periodic timer's ticks                | always           | 10 s  |
| `ladder`    | its 5 s ticks (it blocks in a stuck stop)   | always           | 60 s  |
| `uplink`    | items taken off the mic queue               | queue depth      | 15 s  |
| `speaker`   | successful codec writes (every 20 ms)       | always           | 10 s  |
| `mic`       | reads, or idle frames while off             | always           | 10 s  |
| `decoder`   | items taken off its queue                   | queue depth      | 10 s  |
| `button`    | successful expander reads (every 10 ms)     | always           | 10 s  |

A stall logs `super.stalled part= age_ms= done= pending= enforced=`
(in the black box before the reset), and the watchdog's interrupt adds
`twdt.fired tasks=`. The next boot logs `super.boot reset= last_stall=
at_s= early_in_a_row= degraded=`. Three stalls in a row within 10 min of
boot make a **degraded** boot: the audio parts only log, the app, the
timer task and the ladder (the repair path) stay enforced, so a part
that wedges on every boot can't keep an update out. A WebSocket with no
event for 90 s while the server is up (the client pings every 10 s)
logs `ws.silent` and counts as lost for the ladder.

Console: `super` (each part's done/pending, the stall history); `hang
app|spk` blocks that part for good (bench only: it restarts the walkie);
`coredump erase`. The dump is never overwritten
(`CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE`: a reset loop would wear its
sectors), so read it (`dev.py coredump`) and erase it, or the next
crash leaves none.

## Coredump — reading a crash

When the board panics with coredumps enabled, it writes a binary
image to the `coredump` flash partition before resetting. Over Wi-Fi,
case closed:

```sh
. ~/esp/esp-idf/export.sh
python3 tools/dev.py coredump
```

It fetches the partition (`GET /coredump`) and decodes it with the ELF
of the build that crashed: the dump carries that ELF's SHA-256 (9 hex
chars), and `dev.py flash` keeps every ELF it sends in `build/elf/` as
`<version>-<sha9>.elf`. That matters after a rollback, when the image
that crashed is not the one running. Over USB, for a board off the
Wi-Fi:

```sh
python -m esp_coredump info_corefile --port /dev/cu.usbmodem1101 \
  build/huxley-firmware.elf
```

The output shows the faulting task, the backtrace with source-line
numbers, and the register state at the moment of panic. A build flashed
from another checkout has no ELF here: `--elf PATH`.

## Over Wi-Fi: the developer door

`hux_dev` (F-0021) is an HTTP server on port 80, Tasmota-style, behind
the `X-Huxley-Token` header (`HUX_DEV_TOKEN` in `main/secrets.h`; without
one it doesn't start, `dev.off`). `tools/dev.py` wraps it:

| Endpoint         | `dev.py`             | What                                                        |
| ---------------- | -------------------- | ----------------------------------------------------------- |
| `GET /info`      | `info`               | version, uptime, reset reason, heap, PSRAM, ring, volume, RSSI, slot and its state, `last_invalid` (the image it rolled back from) |
| `POST /cmd`      | `cmd <line>`, `flash` | one console command (same as on USB); replies `ok`, results go to the log |
| `GET /coredump`  | `coredump`           | the core dump partition, raw                                |
| `POST /clips`    | `clips`              | `build/clips.bin` (tools/clips.py) into the storage partition, for `say <n>`; no restart |

The board's log already streams to the server (`client.huxley.firmware_log`),
so `dev.py cmd` and `dev.py log` print the board's own lines from
`server/runtime/logs/huxley.log`; that is also how it finds the board's
IP. Update events: `ota.start`, `ota.image`, `ota.progress kb=`,
`ota.refused`, `ota.failed`, `ota.not_started` (busy, or in use), `ota.done`;
after the reboot `ota.boot`, `ota.last_invalid` and `ota.pending`
(they arrive with the first connect), then `ota.confirmed slot=
uptime_s=` when `dev.py flash` sends `ota confirm` (refused before 30 s:
`ota.confirm_refused`). A rollback shows as the old version reconnecting
(`client.device`) without `ota.confirmed`, `last_invalid` in `dev.py
info` naming the image that crashed (`dev.py flash` prints it), and a
crash dump waiting (`dev.py coredump`). `ota rollback` (console or
`dev.py cmd`) boots the other slot by hand: `ota.rollback to slot=`.
Wrong token: `dev.denied uri=`.

### The console through the server (F-0028)

At the home the port-80 door won't be reachable, so the server can send
`server_event dev.cmd {id, line}`: the app task runs the line like the
serial console (`hux_console_run`) and answers `client_event
dev.cmd_result {id, output, truncated}`, `output` being what the command
logged on that task (lines from other tasks, like a `hang` or the
speaker's, go only to the log). Logs: `app.dev_cmd id=`,
`app.dev_cmd_result id= bytes= truncated= sent=`. The server's side is
`GET /admin/dev-cmd?line=` (loopback and Tailscale only, and in production
the admin token: `ssh mario@100.75.152.70 "~/huxley/scripts/pi/admin.sh
dev-cmd 'line=info'"`; also `admin.sh firmware-offer version=<v>`,
`firmware-offer withdraw=1`, `firmware`; scripts/pi/README.md). Every connection
also carries `Authorization: Bearer <HUX_DEVICE_TOKEN>` on the upgrade
(`ws.token present=1` at boot; the value is never logged), and the hello
is now `device.hello` (`app.device_hello sent= time_to_hello_ms=`). After a reconnect the hello also says what the walkie played out of its
ring after the link dropped (`played_after_drop_ms`), what it still had
queued (`ring_left_ms`, thrown away then: the server's state is newer)
and when it dropped (`drop_uptime_ms`); the server repeats a cut
narration only if it wasn't heard (T1.44; `app.after_drop played_ms=
discarded_ms=`). A press during the outage fixes both figures at that
press: it ends the playback, and the spoken "no connection" it gets
isn't the server's audio. A connection replaced with no drop seen (a
`server` switch, the ladder restarting the client) counts from the moment
the new one connects. `ring_left_ms` also counts the packets the decoder
still had queued when the connection ended; tones never count as played;
`received_ms_last_conn` is all the audio the dropped connection delivered
(`app.after_drop ... queue_ms= received_ms= frozen=`).

### Updates through the server (F-0028)

The server offers an image with `server_event firmware.update {version,
url, force}`; `url` is relative to the server in use (`/firmware/<v>.bin`
on the LAN address or the tunnel alike). The walkie refuses below 40 %
battery, and while it is in use (the lever held, a press or an answer in
flight, a call, anything in the speaker ring or played in the last
800 ms, the mic running), unless forced. Such an offer waits on the
walkie, in RAM (F-0038): `ota.deferred version= why=busy|battery_low
use=press|call|model_speaking|playing|decoding|just_played|mic pct=
again= idle_needed_s=120` and `phase: deferred` (the server doesn't count
it as a failure); while it waits, `ota.waiting version= held_s= idle_s=
use= pct= updating= connected=` every 10 min says what still blocks it.
Once the walkie has been idle, connected and at 40 % or more for 2 min
without a break, at any hour, it logs `ota.ready version= idle_ms=
held_s= sent=` and sends `phase: ready` (a send that fails waits 2 min
and asks again); the server decides again from its
current offer (`firmware.update_sent trigger=idle`, or
`firmware.update_skipped`), so an offer withdrawn meanwhile is never
installed. A restart drops the wait (the next hello offers again).
Otherwise it downloads with `Authorization: Bearer
<device token>` (https via the CA bundle), checks the image is this
project and the offered version, and restarts into it once nothing plays
or listens for 1.5 s (`ota.restart_waiting why=in_use ring_ms= mic=
quiet_ms=`, `ota.restart_waited
ms=`, at most 10 min: a reply or reminder the server started during the
download isn't cut), reporting
`client_event firmware.status {version, phase, error}`: `downloading`,
`verifying`, `rebooting`, or `failed` with `begin|image|download|verify|
busy|bad_url|battery_low`. The new image boots pending: after its first
hello and 20 s of running it runs a **silent** self-test (no supervisor
stall, both codecs and the expander answering on I2C, internal heap
floor over 40 KB; `selftest dac_id= adc_id= expander= heap_min=`) and
sends `phase: selftest` with the first failed check as `error`, then GETs
`/firmware/manifest.json` with the same client settings and checks its
`version` and `elf_sha` (`ota.manifest status= match=`). While the image
is pending it repeats both on every hello (`ota.verify selftest=
manifest=`), so a server restart costs one round, not a reboot. The server
closes the connection once (1012); after the reconnect's hello it sends
`ota.accept {version}`, and the walkie keeps the image only if all of
that held (`ota.accepted`, `phase: accepted`; else `ota.accept_refused
reason=version|not_pending|selftest|manifest|no_reconnect` and `phase:
failed`). Without the accept, the next restart goes back to the previous
image. The developer door's push (`dev.py flash`, `ota confirm`) still
works on the LAN.

### Moving the server (F-0027)

The address in `secrets.h` is compiled into every image; a saved one in
NVS (`hux_net` / `srv_uri`) takes over from it, and is saved only once
the server there sent its hello, so a typo can't strand the board.

| Console (`dev.py cmd ...`) | What |
| -------------------------- | ---- |
| `server`                   | logs `ws.server now= home= compiled= mode=` |
| `server <uri> [min]`       | temporary (tests, proxies): back after `min` (15) or a reboot, never saved |
| `server keep <uri> [min]`  | moves: saved at the first hello there (`ws.server_saved`, `ws.server_proven mode=keep`); no hello within `min` (15) and it goes back (`ws.server_revert to= why=timeout home_proven=`) |

An address is `ws://` or `wss://`, a host, a port and a path; a missing
port becomes the scheme's (443 for `wss://`, 80 for `ws://`) and a
missing path `/` before anything else (`hux_link_uri_complete`), since
the library keeps the old port or path when a new address leaves one
out. Anything else is refused (`ws.server_refused why=invalid`): a saved
address it can't parse would fail every boot. `wss://` checks the
server's certificate against the Mozilla CA bundle (the Cloudflare
tunnel, T1.43). One it refuses anyway leaves the old one in place
(`why=set_uri`). A saved address that is invalid or fails at boot is
erased and the compiled one used (`ws.server_saved_invalid`,
`ws.server_saved_unusable`). Only a hello from a connection opened
after the switch proves an address (`ws.hello_stale` otherwise).
| `server reset`             | forgets the saved address, back to the compiled one |

At boot, `ws.server boot= source=nvs|compiled`. A saved address is
watched: its hello logs `ws.server_proven mode=watch source=nvs`; no
hello for 5 min and it hands over to the compiled one, alternating
every 5 min until one answers (`ws.server fallback to= no_hello_s=`,
then `mode=watch source=compiled`). A fallback never overwrites the
saved address, and the next boot tries it first again. A revert that a
hello or another switch overtook logs `ws.server_revert skipped
why=stale`.

### Without the server: backoff and the ladder (F-0027)

Nobody at the home can restart the walkie, so it climbs by itself.
A disconnect logs `ws.backoff why=disconnected|closed|finished
attempt= next_in_ms=` (it replaced `ws.disconnected`): 2 s doubling,
jittered 75 to 125 %, 45 to 60 s at the cap (`hux_link.c`,
host-tested); a hello resets it, and a new IP while the server is lost restarts the client at once
(`ws.retry_fast why=new_ip`, then `ws.start why=new_ip`: the library
sleeps half its retry delay between checks, so a lower delay alone
reconnected 25 s after the IP). Trust `ws.backoff`, not the library's own
`Reconnect after N ms` (it prints the delay from before the change). Only the first three attempts of an
outage and every tenth log (`ws.error` too): the lines wait in the
256-line queue until the server is back. The first disconnect of an
outage logs `ladder.lost why=disconnected|closed|finished`; then,
still without a hello: `ladder.step step=ws` at 2 min (client stopped
and started), `step=wifi` at 10 (the next known network heard, or the
same one afresh: `esp_wifi_stop`/`start`, a new association and DHCP
lease; again every 10 min while the server stays lost), `step=reboot` at 60 (no lock: a hung
holder can't stop it; an image still pending its OTA confirm goes back
to the other slot). Each once per outage; a step that can't get the
link lock in 10 s logs `ladder.lock_busy` and retries. Every client
start logs `ws.start why=first_ip|resume|switch|ladder err= uri=`. The hello that ends it logs `ladder.found lost_s= steps=`
(the last step taken). An eviction (`net.ws.evicted`, another client
took the server) pauses the clock until the next press. Lines from
the outage reach the server log only after it reconnects; the
reboot's never do (the black box, F-0027, will carry them).

### The Wi-Fi list (F-0027)

The walkie knows up to 3 compiled networks (`HUX_WIFI_SSID`, optional
`HUX_WIFI2_*` / `HUX_WIFI3_*` in secrets.h: home, a phone hotspot, the
place it lives once an update carries it) and up to 4 added on site over
USB. Boot: `wifi.list compiled= saved= good=` and `wifi.init ssid=
source=`; it joins `good` (the network that last reached the server,
`wifi.good ssid=` when it changes) or the first compiled one. Without an
IP for 2 min it scans and moves to the best known network heard
(`wifi.choose why=no_ip heard= candidates= current= pick=`, then
`wifi.join ssid= source= why=`); it never moves to one it doesn't hear.
With an IP but no server, the ladder's Wi-Fi step and every 10 min after
move to the next one heard (`why=ladder|no_server`). Every `ws.error`
says why now: `type= tls_err= tls_stack= sock_errno= http_status=` (an
HTTP 530 is the tunnel down; a DNS or socket error, the network's
filter; the TLS and socket fields are filled for `type=1`, a transport
error, and 0 otherwise: the library leaves them unset). The driver keeps no password in flash (`WIFI_STORAGE_RAM`).

Adding the place's network, on site: plug the walkie into the laptop
with a data cable and run `tools/wifi.py add` in your own terminal (the
password is asked with no echo). It lists what the walkie hears, sends
`wifi addhex <name hex> <password hex>`, and reports: `wifi.add
ssid_len= pass_len=` (never the password), then `wifi.add saved` (it
gave an IP; kept) and `server_reached` or `no_server`, or `wifi.add
failed why=wrong_password|auth_failed|security_too_weak|enterprise_login|not_heard|no_ip`
and back to the previous network, or `wifi.add refused
why=lengths|busy|full|second_in_use|link_busy|nvs|bad_hex`. A name it
already knows takes the password as its second one (`wifi.add second`,
or `wifi.add same` when it has it already). Then it checks
https://walkie.ma-r-s.com from the laptop and restarts the walkie to see
it come back by itself. `wifi list` (also `tools/wifi.py list`) shows the
networks, which is good and active, and the MAC; `wifi forget <name
hex>`. Bench: `wifi join <name hex>` joins a known network now, heard or
not: on one it can't hear, the field case in reverse, it should find
another within about 2 min (`wifi.choose why=no_ip`); a scan the radio
refuses while a connect winds down is retried (`wifi.scan busy attempt=`).

From afar (F-0036), with the walkie online: `scripts/pi/wifi.sh add
"<name>"` (the password asked twice with no echo, sent on stdin end to
end), `forget "<name>"`, `list`. The server's `dev.cmd` is the only
remote door; the LAN developer door (plain HTTP) refuses both
(`console.wifi_addhex refused`). The walkie refuses while in use or
updating (`app.dev_cmd refused why=press|call|playing|...|updating|pending_verify`).
A new name is saved **staged** (`wifi.add staged ... queued`: s0..s3 in
NVS, which an older image doesn't read, so a rollback never joins it)
and tried once at once only if a scan hears it (`wifi.staged_try start`):
it must reach the server within 90 s (`wifi.staged_try reached_server`,
then `wifi.confirmed what=staged`: it moves to w0..w3) or the walkie goes
back to the network it was on and keeps the entry staged
(`wifi.staged_try failed why=... kept staged`); not heard
(`wifi.staged_try skipped why=not_heard`): the ranking joins it when it
is heard, e.g. once the old router is gone. A trial takes the walkie off
the server for up to 90 s (in daytime he hears `lost` at 20 s): stage at
night. A known name, from afar or on USB, takes the password as its
**second** one (p0..p6 in NVS; never replacing the first, the one that
reached the server): after 3 wrong-password failures in a row on the
first (`wifi.auth_failed ssid= run= second=1`) it switches
(`wifi.password_switch to=second`, `wifi.join ... password=second`), and
straight back after 3 more (a weak signal can fail three handshakes on the
right one), then at most twice per 10 min; the one that reaches the
server becomes the first and the old one is dropped (`wifi.confirmed
what=second`), so an older image after a rollback joins with it and no
later switch lands on a dead password. A staged name sent again with
another password (a typo) replaces it and is tried again (`wifi.add
restaged`); `wifi join` never takes a staged one blind (`why=staged`). A compiled name keeps one stored
password: once that one reached the server, a new one is refused
(`why=second_in_use`). `wifi forget` from afar takes any saved or staged
entry but the one in use (`wifi.forget refused why=active`). `wifi list`
says `source=compiled|saved|staged second=0|1 using=first|second`.
Console lines are up to 255 bytes; a longer one is refused
whole (`console.line_too_long`), never cut, and so is a cut `dev.cmd`
(`app.dev_cmd refused why=line_too_long`); the console's echo and the
developer door log `<redacted>` for `wifi addhex`. NVS is only a bridge:
the first update after the visit compiles the place's network in
(`HUX_WIFI3_*`), since NVS is erased if it ever runs out of pages.

### The walkie's own phone alert (F-0032)

With an IP but no server for 11 min (counted from the last time it was
up 2 min on one connection, so a flapping server counts; a test switch,
`server <uri>`, never does), the walkie pushes "Walkie: needs you" to
Mario's phone through ntfy.sh, and "Walkie: fixed" once it's back (only
after a down went out; never repeated, the state in RTC memory and NVS;
a down 22:00-07:00 Bogota is held until 07:00, a fixed goes at once,
silent; a failed send retried after 2 min, doubling to 30). Logs:
`alert.started sntp= down_sent=` at boot, `alert.server_lost since=
lost_s=`, `alert.held why=no_ip|quiet_hours|retry_wait`, `alert.sent
kind=down|fixed|test status= err= heap_internal_min= largest= stack_free=`
(warnings: in the black box), `alert.disabled why=no_topic|topic_invalid sntp=`
without a usable `HUX_NTFY_TOPIC` (secrets.h; the clock still syncs). Console `alert test` sends
one test push end to end.

### Connection sounds (F-0033)

The walkie says its connection state itself with four sounds
(`components/hux_prompts/prompts/{startup,searching,connected,lost}.hxp`,
from `server/personas/_shared/sounds/device/`), decided by `hux_status`
(pure, host-tested) and played by the app loop once a second at most.
Power on: `startup`, then a `searching` tick every 3 s for 2 min, every
30 s after, none after 17 min. `lost` only after 20 s without the server
(a deploy's restart is a silent blip), then the ticks again from the
start. `connected` after a power on or a `lost`, never after a blip. An
outage ends only with a connection that lasts 2 min: a server in a crash
loop (up seconds, down again) goes on quietly on the first schedule, one
`lost` and one `connected` in all. A boot after the battery park counts as
a power on; any other boot (an update, a crash, the ladder's hourly
restart) comes back silent until a new drop, except a `connected` still
owed from before it (kept in RTC memory). "Connected" means the server's
hello arrived (or another client took the server on purpose: evicted is
not an outage). Nothing plays over a reply, a tone, a sentence, a held
lever (offline too), a call, or within 800 ms of the speaker's last
sound; a `connected` still blocked 3 s after the connection is let go
(the server is already talking). A clip enters the decoder whole: the
WebSocket's packets wait for it (`hux_opus_clip_begin/end`). 22:00-07:00
Bogota, once the clock is known (SNTP starts at boot even without an
alert topic): `lost`, the ticks and `connected` are held silent;
`startup` and a press's offline sentence still play. A sound while the
link is down fixes the next hello's after-drop figures first, like a
press (it isn't server audio).
Logs: `sound.status.init power_on= owed= reset=` at boot,
`sound.status.phase <from>-><to> app=<state> outage=`, `sound.status
kind=startup|searching|connected|lost played= n= app=`, `sound.status
kind=held_night|let_go_busy sound= n=`, `sound.status.ticks_done n=`. The
ticks log only the first and every tenth (`n=` counts them; the tick's own
`prompt.play` is at debug), so an outage's 256-line log queue keeps room.
Console `status poweron` starts the sounds over as after a power on (the
bench can't cycle the power; `battery park <mv> 1` tests a real boot as one).

### A press during an update (F-0037)

A press the walkie can't answer because of an update says so: "Me estoy
actualizando. Intenta otra vez en un minuto." (`prompt updating`, 5 s, the
persona's voice at -20 LUFS) instead of "No tengo conexión", which sounds
broken. When: its own update downloading or verifying (the press doesn't
reach the server: the restart would cut the answer), a new image booted
and not yet accepted (for 3 min after boot at most), or the server's
restart close in the last 90 s until the next hello (`1012`, or `1001`
with no reason; `ws.rx.close code= reason= eviction= restart=`). Logs:
`press.offline_prompt which=updating|no_wifi|no_internet|offline why=ota|
ota_pending|server_restart|none have_ip= link_fail=` next to
`app.ptt.ignored_press ... cue=`. Otherwise the press says where the path
breaks (F-0039), so grandpa, or whoever he asks, knows what to check: no IP
(his phone's hotspot off or out of reach) -> `no_wifi` "No encuentro el
internet de tu teléfono. Revisa que el teléfono tenga batería y que la zona
wifi esté prendida, o pídele ayuda a alguien." (10.5 s); an IP but the last
server attempt broke below HTTP (DNS, TCP or TLS to Cloudflare, which always
answers when there's internet) -> `no_internet` "Tu teléfono no me está
dando internet. Revisa que tenga datos, o pídele ayuda a alguien." (6.9 s);
an HTTP answer (the tunnel's 530) or the server closing -> "No tengo
conexión" (Mario's phone gets that alert). `ws.fail_kind internet|server`
logs each change of verdict; a hello resets it.

### The lever bouncing (F-0040)

The lever's switch can bounce open and shut again well past the button's
20 ms debounce: on 2026-10-14 a re-press 71 ms after a release (silent,
900 ms) interrupted his own request, "Quiero oír últimas noticias", just
committed (5 of 808 release-to-press gaps in a week were under 100 ms). So a
release ends the press only after the lever has stayed up 100 ms
(`hux_lever`, host-tested): a press inside that window is the same press
going on, logged `app.ptt.rebound gap_ms=` (a warning) with no second
`app.ptt.pressed` and no interrupt. `button.talk press|release` still logs
every edge the button saw; `app.ptt.released` (and the `ptt_stop`) comes
100 ms after the last `button.talk release`. That 100 ms is the cost: each
answer starts 100 ms later. The window is measured between the button's
own edge times (carried in the event), so an app task held up by a prompt
or a `dev.cmd` never merges two real presses: a press after the window
whose release wasn't committed yet logs `app.ptt.restart why=late_release
gap_ms=` (the first request ends, the second starts), and a press with no
release before it (an `edge_dropped` release) logs `app.ptt.restart
why=missed_release`.

### Lights under the clear back (F-0034)

The board's 7 WS2812B (GPIO38, RMT with DMA, one buffer for a whole frame so
no refill interrupt can be late) light only for people around the walkie
(cleared at every boot, before the battery park). The data chain doesn't
follow the ring: GPIO38 feeds U9 at 102.9 deg first, then clockwise, so
the bottom LED (U14, board +x) is the chain's third; the animations work
in positions and `hux_led_chain` reorders them (`HUX_LED_CHAIN_OF`,
host-tested). `led walk` lights each position alone for 0.6 s, bottom
first, to check it by eye (`led.anim kind=walk`). The lights: `Rainbow` (every LED its own colour, turning round the ring, 1.6 s) when
the startup sound reaches the speaker (the played-audio counter moves, plus the I2S queue's 60 ms;
started anyway after 1 s), so it follows the sound's boot rule; while the
lever is held `Voice` (white, all seven together, their intensity
following the mic level: dark at -50 dBFS RMS and under, so silence stays
dark, full at -15 dBFS); a press the walkie can't act on (offline) stays
dark. Mario's rule (2026-10-14): light only at power on and while the lever
is down and someone talks. The board's green charge LED next to USB-C is
the charger chip's (ETA6098 STAT), not ours: it lights while charging. The four
LEDs behind the battery holders get a gain (`led gain <x>`, saved, 3.0 by
default; in `led.started hidden_gain=`) so the ring reads even through the
case. The frames are Mario's
mockup ported number for number (`hux_led_anim`, host-tested against it).
Peak `led bright <pct>` (saved, 25 % by default), a third of it 22:00-07:00.
Logs: `led.started gpio= dma= pct= hidden_gain=`, `led.anim kind=boot mode=rainbow
wait_ms= heard= night= pct=` (heard=0: the sound never moved, started on
the timeout), `led.anim kind=press mode=voice|dark night= pct= demo=`,
`led.anim.end kind=press mode= ms= v_max_pct=` (the press's loudest level:
tune the mic mapping from it), `led.refresh_failed tries=` (a frame retried
~0.5 s). Console `led demo rainbow|voice` (voice: 3 s of the mockup's
made-up sentence) shows them without a press. The frame logic (`hux_led_engine_step`) is pure and
host-tested; the task only reads its inputs and writes pixels.

### Calls: latency and the walkie's own echo (F-0031)

During a call (`input_mode skill_continuous`) the mic streams while the
speaker plays the far end. `audio.call on rebuffer_ms= echo= depth_db=`
at its start. The speaker's buffer is a call's: after a dropout it refills to
`calljb` (240 ms; streams keep 1 s), and above that + 160 ms (a stall's
backlog arriving at once) it trims back, in a quiet chunk if one comes
within 2 s (`spk.call_trim dropped_ms= ring_ms= quiet= waited_ms=`).
The mic's echo: the ES7210's channel 0 is the speaker's own output,
sampled with the mic. `echo off|shadow|on` (NVS; on by default: the mic
goes down by the depth while the speaker is active, half duplex; shadow:
measure, change nothing), bench `call on|off` (call mode with the mic read for the gate only, no
uplink: measure the gate on the speaker), `echo depth <dB>` (NVS, 20-70, 50 by default:
the first real call measured the echo 23 dB over the near end's voice,
-16 dBFS against a -67 floor, so 30 dB left it audible). Every 5 s:
`audio.echo mode= depth_db= frames= active= spk_ms= release_ms_last= max=
ratio_db_p10= p50= p90=` (`spk_ms`: the speaker's audio in the window;
`release`: from its last active frame to the mic open again)
(mic over speaker power, 300 Hz up, while the speaker is active: the
echo's strength; near-end speech raises the upper percentiles)
`ref_floor_db= mic_peak= ref_peak= mic_clips= ref_clips= vol= pa=
ring_ms_min= max= trims= jb_ms=`. The far end's view: `uv run --no-project
--with numpy python scripts/call_echo.py <date/HHMMSS-conn>` correlates
the recorder's two legs (an echo shows as a peak after 0 ms).

## Choppy audio or dropped connections: measure both ends

Every second while audio flows, the server logs what it handed over
and the board logs what it got:

```
server.tx.audio_stats window_ms=1001 audio_ms=1000 send_wait_ms=0 write_buffer=0 sock_unsent=0
hux_net: ws.rx.stats window_ms=1003 ... audio_ms=1000 max_gap_ms=180 ... rssi=-45
```

Server `audio_ms` near `window_ms` while the board's falls short, with
`sock_unsent` (bytes the kernel still holds for that socket) or
`send_wait_ms` climbing: the network between them is slow. The server's
own `audio_ms` short: the source (a skill's stream, the model) was late.
`spk.underrun silent_ms=` is each gap the speaker played as silence;
with `rebuffered` it was a deliberate pause: the ring ran dry mid-reply,
so the speaker waited for 1 s of audio (`why=buffered`), the end of the
reply (`reply_end`) or 4 s (`max_wait`) before playing on, instead of
stuttering. In the test rig the server's backlog sits in the Mac's
loopback, so `sock_unsent` stays flat there; in the field (board on
Wi-Fi) the backlog is in the server's socket and `sock_unsent` grows.

The uplink side of a press: `mic.tx.stats` every 500 ms (`q_waiting` is
the backlog in the 10 s queue), `app.ptt.final_stats` at the release,
and `mic.tx.done` when `ptt_stop` actually goes out behind the last
frame, with the press's totals (`enq` = frames captured, `tx_ok` =
sent, `enq_drop` = lost to a full queue). `final_stats` also has
`rms_dbfs` (loudness of the whole press) and `clipped` (samples at full
scale): a peak of 0 dBFS alone can't tell one click from a clipped
question. Clipping is normal for him and harmless at the 30 dB mic gain:
checked 2026-10-17 over 755 presses (10-12 to 10-17), 75-90% reach 0 dBFS
(median about 20 samples), and the presses with over 500 clipped samples
(worst 7333, rms -8 dBFS) were missing a transcript 3% of the time against
6% for unclipped ones; their transcripts read right. Loud speech, not a
fault: don't lower the gain for it.

Over days of real use: `python3 tools/dev.py link [hours]` prints, per half
hour, the seconds the speaker played, the gaps it played as silence
(`spk.underrun`, rebuffering included) and their share, the median rssi
and the server's largest backlog, from every server log (every archive
and `logs/keep/`). Wi-Fi itself: `wifi.associated` says the channel, its
width (`second`, `bw_mhz`) and the PHY; `dev.py cmd wifiscan` lists every
AP the board hears; `dev.py cmd wifibw 20|40` tries a width until restart
(20 is the default).

To reproduce instead of waiting for bad Wi-Fi: `python3 tools/bench.py
--wifi --steps stall,slowdown` routes the board through the bench's own
proxy (the console's `server <uri> [min]`, back by itself after 15 min
or a reboot) and stops the uplink in the middle of a question, then
again with a WebSocket ping injected mid-stall (`stall`), or throttles
the downlink in the middle of a stream (`slowdown`, which prints the two
columns above side by side). The proxy's sockets have fixed 8 KB
buffers so a stall reaches the board as it would over the air; Toxiproxy
was tried first and doesn't do that on macOS without root (its buffers
autotune to megabytes and swallowed the stall).

## Diagnosing WS / TCP flaps

When the board connects, works briefly, then drops the WS connection,
the tool-chain below is what isolated the v0.2.2 flap (TCP send buffer
too small → `esp_transport_poll_write(0)` timeout → `esp_websocket_client`
reconnect) to a sysconfig bug in <15 minutes.

1. **Start serial capture** in one terminal:

   ```sh
   firmware/tools/serial-capture.sh
   tail -f /tmp/huxley-debug/serial-latest.log
   ```

2. **Start wire capture** in another (needs sudo):

   ```sh
   sudo firmware/tools/tcpdump-ws.sh
   ```

3. **Enable DEBUG on the WS transport stack** by uncommenting the
   `esp_log_level_set("websocket_client", ESP_LOG_DEBUG)` block in
   `main.c`. Flash and reproduce.

4. **Read serial first** — it names the failure inside the library:

   ```
   E transport_ws: Error transport_poll_write(0)
   E websocket_client: esp_transport_write() returned 0 ...errno=0
   ```

   Means the TCP socket isn't writable within the caller's timeout —
   typically send buffer full.

5. **Confirm with tcpdump**: open the pcap in Wireshark or read with
   `tcpdump -r /tmp/huxley-debug/ws-latest.pcap -ttt -n`. Look for:
   - tiny `win=...` from the board (undersized receive window)
   - bursts of data ending with no matching ACKs
   - RSTs or graceful FINs in the window preceding the WS close

6. **sdkconfig gotcha**: edits to `sdkconfig.defaults` **only apply
   on first configure**. If the generated `sdkconfig` already exists
   with old values, `idf.py build` keeps the old values. To force a
   reload:

   ```sh
   rm sdkconfig
   idf.py reconfigure
   grep -E "LWIP_TCP_SND_BUF|LWIP_TCP_WND|LWIP_WND_SCALE" sdkconfig
   ```

   The grep is mandatory — verify the value you set actually made it
   into the active config before flashing.

## Runtime log levels

Default: `INFO`. Bump a single component to `DEBUG` without
rebuilding:

- **At compile time**: `sdkconfig.defaults` sets the default; a
  per-developer override goes in a local `sdkconfig` (gitignored).
- **At boot**: add `ESP_LOG_LEVEL_SET("<tag>", ESP_LOG_DEBUG)` at
  the top of `app_main` and reflash.
- **From the server** _(planned)_: once `server_event` lands, the
  server can push `{"type":"server_event","event":
"huxley.firmware_log_level","data":{"tag":"hux_net","level":"D"}}`
  at runtime. Tracked in [`roadmap.md`](./roadmap.md).

## Common failure modes and what to look for

### Board boots, Wi-Fi fails

```
W (N) hux_net: wifi.disconnected retry=5/5
E (N) hux_net: wifi.retries_exhausted
```

Check: SSID / password in `main/secrets.h`, router is 2.4 GHz (S3
has no 5 GHz radio on this board), MAC not blocked.

### Wi-Fi up, WS refuses

```
E (N) esp-tls: [sock=54] delayed connect error: Connection reset by peer
E (N) hux_net: ws.error attempt=1
W (N) hux_net: ws.backoff why=disconnected attempt=1 next_in_ms=2214
```

Check: server is running (`pgrep -af huxley`), bound on `0.0.0.0`
not `localhost` (`HUXLEY_SERVER_HOST=0.0.0.0` in the server's
`.env`), board and Mac are on the same subnet (the board prints its
IP in the boot banner — compare to `ifconfig`).

### WS connects, hello mismatch

```
I (N) hux_app: ws.rx.hello protocol=1 (expected=2)
E (N) hux_app: protocol mismatch — server is incompatible
```

Server is on an old protocol version. Rebuild and restart the
server; the board will reconnect automatically.

### Serial is silent

- USB cable is power-only (common). Swap for a known-data cable.
- Board is in download mode. Tap `RESET`.
- Wrong port. `ls /dev/cu.usbmodem*` — should be exactly one.

### Board resets every few seconds

- Brownout: USB port can't supply ~500 mA transient during Wi-Fi TX.
  Plug into a powered hub or the Mac's built-in port, not a passive
  dock.
- Stack overflow in a task: look for `***ERROR*** A stack overflow in
task <name> has been detected` — bump that task's stack in its
  `xTaskCreate` call.
- Watchdog: a task held the CPU too long. Look for `Task watchdog got
triggered. The following tasks did not reset the watchdog…` with
  the offender named.

## The bench, with no hand and no sound

The serial console (`components/hux_console/include/hux_console.h`)
takes commands typed on the USB port: `talk` / `release`, `say <n>` (a
whole turn: clip n from flash replaces the mic; `tools/clips.py`),
`selftest` (a tone on the DAC read back electrically on the ES7210's
echo-reference channel), `tone`, `prompt <name>` (a sentence or sound stored on
the walkie, `offline`, `battery_empty`, `updating`, `no_wifi`, `no_internet`,
`startup`, `searching`,
`connected` or `lost`; `prompt.play name= ms= queued=`), `status poweron`
(the connection sounds as after a power on, F-0033), `led bright <pct>` /
`led demo rainbow|voice` / `led walk` (the lights, F-0034), `opus yield on|off` / `opus flood <s>` (the decoder's
guard, which gives a core up after 100 ms of work without waiting,
`opus.yield run_ms= waiting= count=`; and a silent flood to test it, amp
off first, F-0035),
`vol`, `pa off|on` (silent bench),
`wifi off|on`, `wifistat` (the Wi-Fi driver's buffer and RX/TX
counters; they reach the server too, under the `wifi` tag), `wifiscan`
(every AP the board hears: `wifi.scan.ap` lines with channel, width and
signal, to see who shares its channel), `wifibw 20|40` (the station's
channel width, rejoining the AP; back to the default on a restart),
`info` (heap, PSRAM, every task's smallest free stack, ring,
volume and the ES8311 register, RSSI, time since the last inbound data).
`battery` (is the cell's divider wired to GPIO1 or GPIO6? one
`battery.probe` line per pin, then `battery.level` with the current
reading, F-0025), `battery cal <mv>` (a multimeter's reading of the
cell: every later reading is scaled to match, saved in NVS,
`battery.calibrated cal_ppm=`). The level task finds the wired pin once
(`battery.wired gpio=`, or `battery.not_wired` once and a look every
10 min), then reads the median of 32 samples at 6 dB at boot, every
20 min while the speaker is quiet (anyway after 5 min with no quiet
moment), and at each press, and sends `system.battery {mv, pct}` when
the percentage changes or a press asked (`battery.level mv= pct= pin=
raw_mv= loaded= why=changed|press`); the app also sends the last one
after each hello.

**The low-battery cutoff (F-0030)**, an extra layer above the cells'
protection boards. Under 10 % (3.61 V) the level task reads every 2 min
(`battery.zone mv= quiet= in_zone_s= loaded_low=`). After 10 min in that
zone, a resting reading (the speaker quiet 30 s, no press under way) under 3.40 V, or three
loaded ones under 3.25 V a minute apart, is the cutoff
(`battery.cutoff_due why=quiet|loaded`); readings under 2.9 V, or with
the calibration off by more than 5 %, never count. The walkie says
"Batería agotada. Ponme a cargar.", sends `system.battery {cutoff: true}`
(`battery.cutoff`), arms the park in RTC memory and restarts. At boot,
right after the expander and the ADC and before anything else, an armed
park, or a resting reading under 3.45 V (a power cycle loses RTC memory),
parks it: the amp off and both codecs in their power-down state (a CPU
reset leaves them powered), light sleep, a wake every 60 s to read the
cell (`battery.parked`), the lever as a wake source. A press says the
sentence (`battery.park_press`, at most once per 30 s, the lever read
twice 50 ms apart); holding the lever 10 s boots anyway
(`battery.park_escape`). Only a press that began after the lever was seen
up in this park counts: a lever stuck down (or a shorted wire) gets
timer-only wakes, no sentence and no escape. At 3.60 V, or a reading that
can't be believed, it boots (`battery.resume
why=charged|unreadable|calibration|escape|test_over parked_s= wakes=
lever_wakes= presses= min_mv=`). It never parks an update not yet kept
(its restarts would roll it back; `battery.park_skipped
why=update_pending`). These lines are warnings, so the black box brings
them with the next hello. The remote switch: `battery cutoff off|on`
(NVS, also through `admin.sh dev-cmd`; `battery.cutoff_inactive` at boot
while it is off or the calibration is more than 5 % out). Bench: `battery
park <mv> <minutes>` parks as if the cell read `<mv>` and boots by itself
after that many wakes (refused during an update). While parked the USB
serial drops each minute (light sleep switches its pad off) and no
console runs, so a test park can't be cancelled from the Mac; a USB
reset is a CPU reset, so a test's RTC state survives a USB flash.
`tools/bench.py --reset` runs the whole check list and keeps the board's
log in `/tmp/huxley-debug/bench-*.log`.

Opening the serial port can reset the board (macOS toggles DTR/RTS):
expect a boot when a monitor or `bench.py` attaches.

Lines that watch the audio path while a reply plays:

- `ws.rx.stats` (once a second while audio arrives): bytes, messages,
  audio received (`audio_ms`; above 1000 per second = faster than real
  time), the longest gap between socket reads, time spent in our handler.
  Gaps of ~250 ms mean lwIP is retrying on its TCP timer (F-0015).
- `spk.underrun silent_ms=N`: the ring ran dry mid-reply, an audible
  dropout. `spk.alive` counts them and shows how long the network side
  waited for room (`push_wait_max_ms`).
- `spk.hold on` / `spk.hold off by=audio_clear held_ms=N`: a barge-in
  and how long the interrupted reply's tail took to clear (~0.2-0.6 s).
- `ws.rx.close code=N reason=...` / `ws.closed` / `ws.finished`: the
  server closed the socket; an eviction pauses the client until the next
  talk press (`net.ws.evicted`).

## JTAG (the board's built-in USB-JTAG)

```sh
. ~/esp/esp-idf/export.sh
openocd -c "set ESP_FLASH_SIZE 0" -f board/esp32s3-builtin.cfg &
xtensa-esp32s3-elf-gdb -batch -x tools/jtag_snapshot.gdb build/huxley-firmware.elf
python3 tools/jtag_lever.py 0.5      # press the case's lever (GPIO7) electrically
```

- `ESP_FLASH_SIZE 0` matters: without it OpenOCD resets the chip on the
  first GDB attach (to lift memory protection for flash breakpoints),
  destroying the state you attached to look at. Reads and RAM writes
  work without flash support.
- `jtag_snapshot.gdb` prints the WebSocket's TCP control block (window,
  out-of-order segments queued, retransmits) and every task's
  backtrace, then resumes. It found F-0015 (32 out-of-order segments
  holding every Wi-Fi RX buffer) and F-0016 (no `websocket_task` left).
- Never `call` a function from GDB on this target: an inferior call on
  the halted chip double-faulted it on resume.
- With a debugger attached a panic halts instead of rebooting:
  `targets` in OpenOCD's telnet (port 4444) shows `halted`, and GDB's
  `bt` shows where.
- The console and JTAG share the USB cable; both work at once.

## Adding a new log line — the checklist

Before merging a feature, ask: **if this breaks in production, what
log line would I need to diagnose it?** If the answer is "none of
the existing ones," add it before shipping. Same discipline as the
main project's
[`../../docs/observability.md`](../../docs/observability.md).

- Does every new decision branch log its input + chosen path?
- Does every new external call (I2C, WS send, file write) log
  success / failure once?
- Is the component tag in the log line `grep`-friendly?

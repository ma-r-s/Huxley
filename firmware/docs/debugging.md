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
# With idf.py (needs a real terminal):
. ~/esp/esp-idf/export.sh
cd firmware
idf.py -p /dev/cu.usbmodem2101 monitor
```

Or without:

```sh
stty -f /dev/cu.usbmodem2101 115200 cs8 -cstopb -parenb raw -echo
cat /dev/cu.usbmodem2101
```

`idf.py monitor` is strictly better — it decodes panic backtraces to
source lines and handles resets gracefully. Use `cat` only when
scripting.

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
in [`../../docs/protocol.md`](../../docs/protocol.md)). Search the
server's structlog output for `event=client.huxley.firmware_log` to
pull every firmware event from a session.

**Limitations**:

- Streaming is **best-effort**. During Wi-Fi / WS disconnect the
  stream stops; those events land only on serial.
- The current dev build sets the threshold to `INFO` (in `main.c`:
  `hux_log_set_remote_level('I')`) — the server sees every firmware
  log line. In prod we'll bump back up to `WARN` to bound bandwidth.
- **Early-boot lines are serial-only.** Anything that fires before
  `ws.connected` (boot banner, NVS init, `hux_app` startup) queues
  but fails to send — the WS isn't open yet. Read the first ~3
  seconds on serial. Queued-replay for early boot is on the v0.2
  roadmap.
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

## Coredump — reading a crash

When the board panics with coredumps enabled, it writes a binary
image to the `coredump` flash partition before resetting. After the
board reboots (or while it's halted), pull the dump:

```sh
. ~/esp/esp-idf/export.sh
cd firmware
python -m esp_coredump info_corefile \
  --port /dev/cu.usbmodem2101 \
  build/huxley-firmware.elf
```

The output shows the faulting task, the backtrace with source-line
numbers (because we have the matching ELF in `build/`), and the
register state at the moment of panic.

If you don't have the ELF (a field device), you need the ELF that
was flashed — save `build/huxley-firmware.elf` alongside every
release binary.

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
E (N) hux_net: ws.error
W (N) hux_net: ws.disconnected
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

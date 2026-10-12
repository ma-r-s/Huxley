# firmware/tools/

Reusable helpers for bringing up, flashing, and debugging the firmware
without cluttering the repo with ad-hoc scripts.

**Captures land in `/tmp/huxley-debug/`** — outside the repo, auto-
cleared on reboot. Never commit captures.

## Scripts

| Script              | What it does                                                                                           | Needs          |
| ------------------- | ------------------------------------------------------------------------------------------------------ | -------------- |
| `dev.py`            | The board over Wi-Fi, no cable: `flash` (serves `build/` and has the board update itself, with rollback), `info`, `cmd <console command>`, `log`, `coredump` (decoded with the matching ELF, kept per flashed build in `build/elf/`). Finds the board in the server log. | server up, `HUX_DEV_TOKEN` |
| `flash.sh`          | Flashes `build/` and boots it with a watchdog reset (`--clips` adds the bench clips; `--unstick` boots a board stuck in download mode). Use it instead of `idf.py flash`. | ESP-IDF env |
| `serial_log.py`     | Logs the console without resetting the board (DTR/RTS held still), follows it across USB sockets, sends commands from a FIFO. | pyserial (ESP-IDF env) |
| `serial-capture.sh` | Runs `serial_log.py` in the background into `/tmp/huxley-debug/serial-<ts>.log`; commands via `/tmp/huxley-debug/console.fifo`. | ESP-IDF env |
| `serial-stop.sh`    | Kills any active `serial-capture.sh` capture.                                                          | —              |
| `tcpdump-ws.sh`     | Packet-captures the server's WS traffic to `/tmp/huxley-debug/ws-<ts>.pcap` for Wireshark analysis.    | `sudo` (macOS) |
| `smoke.sh`          | Reboots the board through its console (or flashes it first), waits for READY.                         | ESP-IDF env    |
| `bench.py`          | The bench check list, PASS/FAIL per step: boot, self-test, a turn, volume, barge-in, silent press (not committed), offline + recovery, eviction, headroom. No hand, no sound (`--loud` keeps the amp alone). `--wifi` runs it over Wi-Fi; opt-in steps: `stall`/`slowdown` (bad Wi-Fi through its own proxy, with injected pings), `resume` (music back after a question, stopped by "para la música"), `hearing` (yesterday's failed requests, checked by the tool call each produces; needs Spotify). Log in `/tmp/huxley-debug/bench-*.log`. | ESP-IDF env (pyserial) or `--wifi`, server up |
| `clips.py`          | Renders the Spanish requests `say <n>` plays into the mic (macOS `say -o`, nothing is played) into `build/clips.bin`; `dev.py clips` puts them on the board over Wi-Fi. | macOS, ffmpeg  |
| `jtag_lever.py`     | Presses the case's lever (GPIO7) electrically over JTAG.                                               | OpenOCD        |
| `jtag_snapshot.gdb` | TCP control block + every task's backtrace from a running board, no reset.                             | OpenOCD        |

OpenOCD for the two JTAG tools, in its no-reset mode (see
[`../docs/debugging.md`](../docs/debugging.md#jtag-the-boards-built-in-usb-jtag)):

```sh
. ~/esp/esp-idf/export.sh
openocd -c "set ESP_FLASH_SIZE 0" -f board/esp32s3-builtin.cfg &
```

Typical debugging session:

```sh
# Terminal 1 — board-side logs
./firmware/tools/serial-capture.sh
tail -f /tmp/huxley-debug/serial-latest.log

# Terminal 2 — wire-level WS traffic (needs sudo)
./firmware/tools/tcpdump-ws.sh

# Now press K2 on the board (or type `talk` / `release` on the serial
# console, or run tools/bench.py), reproduce the issue.
# Stop:
./firmware/tools/serial-stop.sh
# tcpdump stops with Ctrl+C in Terminal 2.

# Analyse:
tail -100 /tmp/huxley-debug/serial-latest.log
tshark -r /tmp/huxley-debug/ws-latest.pcap  # or open in Wireshark
```

## Why `/tmp/huxley-debug/` instead of `./build/` or similar?

- **Auto-cleanup**: macOS clears `/tmp` on reboot. No accumulating
  multi-GB captures.
- **Out of git**: nothing in `/tmp` can end up in a commit.
- **Shared across checkouts**: multiple clones or worktrees see the
  same captures without cross-contamination.

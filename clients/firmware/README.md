# huxley-firmware

ESP32-S3 client for Huxley: the walkie. Hold the talk lever, speak,
let go, hear the answer. Speaks the WebSocket protocol defined in
[`../../docs/protocol.md`](../../docs/protocol.md), the same contract as
the PWA dev client in [`../pwa/`](../pwa/). Its case (HX-1) lives in
[`enclosure/`](enclosure/README.md).

Prototyped in this repo until end-to-end voice works; will be extracted
to its own `huxley-firmware` repository once the architecture is
settled. See [`../docs/clients.md`](../docs/clients.md) for the
long-term client-repo strategy.

## Target hardware

[Waveshare ESP32-S3-AUDIO-Board](https://www.waveshare.com/wiki/ESP32-S3-AUDIO-Board)
— ESP32-S3 + 8 MB PSRAM + 16 MB flash + ES8311 codec + ES7210 mic ADC

- TCA9555 I/O expander. The on-board LCD, camera, and TF slot are not
  used by the firmware today and may never be.

Physical device: a 3D-printed walkie case around the board, its speaker,
two 18650s in holders and a spring-loaded push-to-talk lever. Concepts, measurements from
Waveshare's STEP, and the fit-checked CAD live in
[`enclosure/`](enclosure/README.md).

## Architecture (at a glance)

```
main/          app_main: NVS, event loop, bring-up in dependency order
components/
  hux_proto/   Pure JSON <-> typed message marshalling (host-testable)
  hux_app/     Single state owner + event queue (actor model): PTT, mic
               ownership, server state, volume
  hux_net/     Wi-Fi STA + WebSocket client; the audio data plane
               (audio, audio_clear) and fragment reassembly (hux_ws_frag)
  hux_audio/   I2S1 duplex: ES7210 mic (24 kHz) and ES8311 speaker with a
               4 MB PSRAM playback ring (a whole reply); bench self-test
  hux_board/   I2C bus + TCA9555 expander (buttons, speaker PA)
  hux_button/  Talk input: GPIO7 (the case's lever) or K2, debounced
               (hux_debounce, host-testable)
  hux_log/     Log hook: serial + streamed to the server as client_event
  hux_console/ Bench commands on the USB serial console (talk, say, info…)
  hux_ota/     Updates over Wi-Fi (esp_https_ota) + rollback self-test
  hux_dev/     Developer door over Wi-Fi: /info, /cmd, /coredump (token)
  hux_opus/    Opus both ways when the server agrees (xiaozhi-esp32's
               contract): encoder for the mic, decoder task for the speaker
```

Invariants (see [`../CLAUDE.md`](../CLAUDE.md) for the full project
standards):

1. **One state owner** — `hux_app`'s task. Every other task posts events;
   never mutates shared state directly. No mutexes on business state.
2. **Protocol is pure** — `hux_proto` has no I/O; compiles against cJSON
   alone so the wire layer is unit-testable on a host.
3. **Pre-allocated audio path**: ring buffers sized at init in PSRAM;
   no `malloc` per frame.
4. **Dual-OTA partitions** reserved from day 1, used since F-0021:
   updates go over Wi-Fi into the other slot, and a new image that
   crashes in its first 30 s boots the old one again.

## Wiring the talk button

The case's lever (or any push button) closes two wires: one to **GND**,
one to **IO7** (GPIO7). Both are on the 2 x 9 pin header at the board's
edge, at opposite ends. Board face up, USB-C at the top, the labels
printed above the header read, left to right:

```
IO7  IO6  IO5  IO4  IO3  IO20 IO19 GND  5V
EX3  EX2  EX1  IO9  IO8  SDA  SCL  GND  3V3
```

Each column is two pins, one per label line. **GND**: either pin of the
second column from the right (both are GND). **IO7**: the far-left
column, whose other pin is EX3 (an expander input: touching it to GND is
harmless). Which way round doesn't matter: it's a switch. **Never the
far-right column** (5V, 3V3): a button there shorts the battery or puts
5 V on the chip. Check it: the serial log says `button.talk press
src=lever` on a press, and `talk.gpio7 low at boot` if IO7 is held or
shorted. Source: Waveshare's pinout (`enclosure/reference/pinout.jpg`:
GPIO7 = header pin 2, GND = pins 15 and 16).

## Prerequisites

- ESP-IDF **v5.5** installed. Clone + install:
  ```sh
  git clone --depth 1 -b release/v5.5 --recursive \
    https://github.com/espressif/esp-idf.git ~/esp/esp-idf
  cd ~/esp/esp-idf && ./install.sh esp32s3
  ```
- Each shell that builds / flashes must source the activate script:
  ```sh
  . ~/esp/esp-idf/export.sh
  ```

## First-time setup

```sh
cd clients/firmware
cp main/secrets.example.h main/secrets.h
# Edit main/secrets.h — fill in your Wi-Fi SSID, password, and the
# server URI (ws://<mac-lan-ip>:8765/).
```

## Build, flash, monitor

Once the board has this firmware and is on the Wi-Fi, it never needs the
USB cable again (F-0021). The server must be running (that's how the
tool finds the board and reads its log):

```sh
idf.py build
python3 tools/dev.py flash     # over Wi-Fi: ~10 s, then it reboots into it
python3 tools/dev.py info      # version, uptime, heap, RSSI, slot
python3 tools/dev.py cmd selftest   # any console command
python3 tools/dev.py coredump  # fetch and decode the last crash
```

`flash` refuses a build with the version already running. The version
is `git describe` as of the last cmake configure, so a commit gets a new
one but an uncommitted test build keeps the old one: `--force` sends it
anyway. The new image stays pending until `flash`, 30 s later and over
Wi-Fi, confirms it; if it crashes or restarts before that, the
bootloader boots the previous one, and `coredump` shows why. The board
refuses an update while it's playing or listening (`--force`).
The token for all of this is `HUX_DEV_TOKEN` in `main/secrets.h`. USB is
for the first flash, the bootloader, the partition table and the bench
clips.

Over USB:

```sh
. ~/esp/esp-idf/export.sh
cd clients/firmware
idf.py set-target esp32s3      # first time only
idf.py build
tools/flash.sh                 # flash and boot it (not `idf.py flash`)
tools/serial-capture.sh        # log the console without resetting it
```

**Flash with `tools/flash.sh`, never `idf.py flash`; don't use `idf.py
monitor` or a plain terminal on the board's port.** On the S3's built-in
USB the host's RTS line resets the chip without re-reading its boot
pins, so a chip that slipped into download mode stays there ("waiting
for download", silent, dead to the user) until a power cut. `idf.py
flash` ends with exactly that reset, and terminals toggle the lines when
they open the port. `flash.sh` ends with esptool's watchdog reset (a
full reset that re-reads the pins); the repo's loggers and `bench.py`
hold the lines still and restart the board through its console
(`reboot`). If it ever happens anyway: `tools/flash.sh --unstick`, from
the Mac, case closed. On battery or a plain charger nothing drives those
lines, so a walkie in use can't get there.

The port name follows the USB socket (`usbmodem1101`, `usbmodem2101`,
…): `ls /dev/cu.usbmodem*`; `tools/` finds it.

`dependencies.lock` (gitignored) pins the managed components; copy the
one from a checkout that has run on the board (esp_codec_dev 1.4.0,
esp_websocket_client 1.6.1, IDF 5.5.4) rather than letting the
component manager pick newer ones.

If the board doesn't enumerate as `/dev/cu.usbmodem*` at all, hold
**BOOT**, tap **RESET**, release BOOT: the S3 enters the download-mode
bootloader, and `tools/flash.sh` gets it out with a new image.

## Tests

Three tiers, cheapest to most expensive:

```sh
# 1. Host unit tests — pure-C modules, no hardware needed (~1 s)
cd clients/firmware/tests && cmake -B build && cmake --build build --target check

# 2. Server-side wire-contract tests: firmware <-> server message shapes,
#    both directions (from the repo root)
uv run --package huxley pytest server/runtime/tests/unit/test_firmware_contract.py

# 3. End-to-end smoke — boots the board, waits for READY on serial
clients/firmware/tools/smoke.sh              # uses currently-flashed firmware
clients/firmware/tools/smoke.sh --flash      # reflash + smoke

# 4. Bench check list: real board + real server, no hand, no sound:
#    boot, electrical self-test, a turn, volume, barge-in, offline cue +
#    Wi-Fi recovery, eviction, heap and stack headroom (~3 min)
python3 tools/clips.py && esptool.py --chip esp32s3 -p PORT write_flash 0x420000 build/clips.bin  # once
python tools/bench.py --reset
python3 tools/bench.py --wifi                        # the same, case closed
python3 tools/bench.py --wifi --steps stall,slowdown  # bad Wi-Fi on purpose (its own proxy)
python3 tools/bench.py --wifi --steps resume,hearing  # media resume; yesterday's requests
```

Run (1) and (2) before every commit that changes firmware C or
server protocol. Run (3) before every commit that changes
`hux_net`, `hux_app`, `main.c`, or `sdkconfig.defaults`, and (4) before
any change to the audio path, the network settings or the WebSocket
client. Each prints PASS/FAIL; no step needs an ear.

## Server-side note

The Huxley server binds to `localhost` by default. For the firmware to
reach it from the LAN, set `HUXLEY_SERVER_HOST=0.0.0.0` in
`../../server/runtime/.env`. One client at a time: a second connection
evicts the first, so close the PWA tab while testing the board.

## What works today

- Wi-Fi STA (power save off, strongest AP, retries forever), WebSocket
  connect, keep-alive, reconnect after any drop or server close; after
  an eviction by another client it waits for the next talk press
- `hello` with protocol check (2); `session_ready=false` is logged
- Talk: GPIO7 (the case's lever, header pin 2 to GND) or K2 →
  `ptt_start`, 20 ms mic frames as `audio`, `ptt_stop`; a press clears
  the speaker and drops the interrupted reply still on its way
  (barge-in); a press while offline plays a low two-tone cue
- Speaker: inbound `audio` (reassembled when fragmented) into a 4 MB
  PSRAM ring that holds a whole reply (it pushes back on the server only
  past 87 s), played through ES8311; `audio_clear` empties it in order
- `set_volume` (the system skill), `input_mode` (mic continuous during
  a skill's claim, e.g. a Telegram call), `state` tracked and logged
- Logs to serial and to the server (`client_event: huxley.firmware_log`);
  says who it is on every connect (`client_event: device`: version, reset
  reason, RSSI, volume)
- Bench console on USB serial: `talk`, `release`, `say <n>`, `selftest`,
  `tone`, `vol`, `pa on|off`, `wifi on|off`, `wifistat`, `wifiscan`,
  `wifibw 20|40`, `info`, `battery`, `reboot`, `ota <url>` (`components/hux_console/include/hux_console.h`)
- Over Wi-Fi (`tools/dev.py`): updates with rollback, the same console
  commands, health, crash dumps

## What's next

- Echo cancellation during calls (the speaker leaks into the mic)
- State LED via the onboard WS2812
- See `docs/triage.md`

## First flash after a long gap

1. Server running, on the LAN (`HUXLEY_SERVER_HOST=0.0.0.0`), PWA tab
   closed. `main/secrets.h` has the Wi-Fi and `ws://<server-ip>:8765/`:
   check the IP is the server's today (`ipconfig getifaddr en0`); DHCP
   moves it (a reservation in the router stops that). If `ws.connected`
   never shows, this is the first suspect.
2. `tools/flash.sh`, then `tools/serial-capture.sh` (or `tools/smoke.sh
   --flash`, which waits for READY).
3. Serial should show, in order: `boot version=…`, `button_task started
   … talk=k2+gpio7`, `wifi.got_ip`, `ws.connected`, `ws.rx.hello
   protocol=2`, `state WAITING_HELLO -> READY`, then `ws.rx.state … ->
   CONVERSING`.
4. Hold K2 (or touch GPIO7 to GND): `button.talk press src=k2|lever`,
   `app.mic on`, `mic.tx.stats …`; let go: `app.ptt.final_stats …`. The
   answer plays; `spk.alive … audio=` climbs.
5. Say "pon el volumen al 90", then "al 40": `spk.volume level=…`, and
   you hear the difference (it boots at 50, today's loudness). A `talk.gpio7 low at
   boot` warning means GPIO7 is held or shorted.
6. Or, with nobody at the board: `python tools/bench.py --reset` (see
   Tests).

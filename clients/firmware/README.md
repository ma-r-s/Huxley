# huxley-firmware

ESP32-S3 client for Huxley: the walkie. Hold the talk lever, speak,
let go, hear the answer. Speaks the WebSocket protocol defined in
[`../../docs/protocol.md`](../../docs/protocol.md), the same contract as
the PWA dev client in [`../pwa/`](../pwa/). Its case (HX-1) lives in
`clients/firmware/enclosure/` on the `enclosure-hx1-revd-r4` branch (not
merged yet).

Prototyped in this repo until end-to-end voice works; will be extracted
to its own `huxley-firmware` repository once the architecture is
settled. See [`../docs/clients.md`](../docs/clients.md) for the
long-term client-repo strategy.

## Target hardware

[Waveshare ESP32-S3-AUDIO-Board](https://www.waveshare.com/wiki/ESP32-S3-AUDIO-Board)
— ESP32-S3 + 8 MB PSRAM + 16 MB flash + ES8311 codec + ES7210 mic ADC

- TCA9555 I/O expander. The on-board LCD, camera, and TF slot are not
  used by the firmware today and may never be.

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
               512 KB PSRAM playback ring
  hux_board/   I2C bus + TCA9555 expander (buttons, speaker PA)
  hux_button/  Talk input: GPIO7 (the case's lever) or K2, debounced
               (hux_debounce, host-testable)
  hux_log/     Log hook: serial + streamed to the server as client_event
```

Invariants (see [`../CLAUDE.md`](../CLAUDE.md) for the full project
standards):

1. **One state owner** — `hux_app`'s task. Every other task posts events;
   never mutates shared state directly. No mutexes on business state.
2. **Protocol is pure** — `hux_proto` has no I/O; compiles against cJSON
   alone so the wire layer is unit-testable on a host.
3. **Pre-allocated audio path**: ring buffers sized at init in PSRAM;
   no `malloc` per frame.
4. **Dual-OTA partitions** reserved from day 1 — adding OTA is additive,
   not a flash-layout migration.

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

```sh
. ~/esp/esp-idf/export.sh
cd clients/firmware
idf.py set-target esp32s3      # first time only
idf.py build
idf.py -p /dev/cu.usbmodem2101 flash monitor
```

`dependencies.lock` (gitignored) pins the managed components; copy the
one from a checkout that has run on the board (esp_codec_dev 1.4.0,
esp_websocket_client 1.6.1, IDF 5.5.4) rather than letting the
component manager pick newer ones.

If the board doesn't enumerate as `/dev/cu.usbmodem*`, hold **BOOT**,
tap **RESET**, release BOOT — this puts the S3 into the download-mode
bootloader.

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
```

Run (1) and (2) before every commit that changes firmware C or
server protocol. Run (3) before every commit that changes
`hux_net`, `hux_app`, `main.c`, or `sdkconfig.defaults`.

## Server-side note

The Huxley server binds to `localhost` by default. For the firmware to
reach it from the LAN, set `HUXLEY_SERVER_HOST=0.0.0.0` in
`../../server/runtime/.env`. One client at a time: a second connection
evicts the first, so close the PWA tab while testing the board.

## What works today

- Wi-Fi STA (power save off), WebSocket connect, keep-alive, reconnect
- `hello` with protocol check (2); `session_ready=false` is logged
- Talk: GPIO7 (the case's lever, header pin 2 to GND) or K2 →
  `ptt_start`, 20 ms mic frames as `audio`, `ptt_stop`; a press clears
  the speaker (barge-in)
- Speaker: inbound `audio` (reassembled when fragmented) into a 512 KB
  PSRAM ring, played through ES8311; `audio_clear` empties it in order
- `set_volume` (the system skill), `input_mode` (mic continuous during
  a skill's claim, e.g. a Telegram call), `state` tracked and logged
- Logs to serial and to the server (`client_event: huxley.firmware_log`)

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
2. `idf.py -p /dev/cu.usbmodem2101 flash monitor` (or `tools/smoke.sh
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

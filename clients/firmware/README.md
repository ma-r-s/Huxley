# huxley-firmware

ESP32-S3 client for Huxley. Speaks the WebSocket protocol defined in
[`../docs/protocol.md`](../docs/protocol.md) — same contract as the
browser dev client in [`../web/`](../web/) and the future
[`huxley-web`](../docs/clients.md#what-huxley-web-is) PWA.

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
main/          app_main — NVS, event loop, app + net bring-up
components/
  hux_proto/   Pure JSON <-> typed message marshalling (host-testable)
  hux_app/     Single state owner + event queue (actor model)
  hux_net/     Wi-Fi STA + WebSocket client
```

Invariants (see [`../CLAUDE.md`](../CLAUDE.md) for the full project
standards):

1. **One state owner** — `hux_app`'s task. Every other task posts events;
   never mutates shared state directly. No mutexes on business state.
2. **Protocol is pure** — `hux_proto` has no I/O; compiles against cJSON
   alone so the wire layer is unit-testable on a host.
3. **Pre-allocated audio path** (coming) — ring buffers sized at init in
   PSRAM; no `malloc` per frame.
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
cd firmware
cp main/secrets.example.h main/secrets.h
# Edit main/secrets.h — fill in your Wi-Fi SSID, password, and the
# server URI (ws://<mac-lan-ip>:8765/).
```

## Build, flash, monitor

```sh
. ~/esp/esp-idf/export.sh
cd firmware
idf.py set-target esp32s3      # first time only
idf.py build
idf.py -p /dev/cu.usbmodem2101 flash monitor
```

If the board doesn't enumerate as `/dev/cu.usbmodem*`, hold **BOOT**,
tap **RESET**, release BOOT — this puts the S3 into the download-mode
bootloader.

## Tests

Three tiers, cheapest to most expensive:

```sh
# 1. Host unit tests — pure-C modules, no hardware needed (~1 s)
cd firmware/tests && cmake -B build && cmake --build build --target check

# 2. Server-side wire-contract tests — firmware ↔ server message shapes
uv run --package huxley pytest packages/core/tests/unit/test_firmware_contract.py

# 3. End-to-end smoke — boots the board, waits for READY on serial
firmware/tools/smoke.sh              # uses currently-flashed firmware
firmware/tools/smoke.sh --flash      # reflash + smoke
```

Run (1) and (2) before every commit that changes firmware C or
server protocol. Run (3) before every commit that changes
`hux_net`, `hux_app`, `main.c`, or `sdkconfig.defaults`.

## Server-side note

The Huxley server binds to `localhost` by default. For the firmware to
reach it from the LAN, set `HUXLEY_SERVER_HOST=0.0.0.0` in
`../packages/core/.env` (already done in this checkout).

## What works today

- Wi-Fi STA association with retry/backoff
- WebSocket connect + reconnect
- Parse + log server `hello` (validates protocol version matches)
- Log every other inbound message by kind (no handlers yet)

## What's next

- `hux_audio` component — I2S + ES8311/ES7210/TCA9555 drivers, PSRAM
  ring buffers, PCM16 @ 24 kHz mono
- Button (`K2`) → `ptt_start` / `ptt_stop`
- Mic → base64-in-JSON `audio` frames while PTT held
- Inbound `audio` → speaker playback queue
- State LED via the onboard WS2812

Tracked in `docs/triage.md` once the item is filed.

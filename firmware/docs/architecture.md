# Firmware architecture

How the ESP32-S3 client is put together. Source of truth — keep in
sync with the actual code. If this doc and the code disagree, fix
whichever is wrong in the same commit.

## 10-second summary

```
                      ┌─────────────────────────────────┐
  Board peripherals   │         app_main                │
    I2C TCA9555       │  NVS -> netif -> event loop ->  │
    I2S ES7210 (mic)  │  hux_app_start -> hux_net_start │
    I2S ES8311 (spk)  └──────────────┬──────────────────┘
    GPIO buttons                     │
    WS2812 LED                       │
                                     ▼
  ┌────────────┐  events  ┌─────────────────────┐
  │  hux_net   │────────▶ │     hux_app         │
  │  Wi-Fi STA │          │  (single state      │
  │  WS client │          │   owner — actor)    │
  └──────▲─────┘          └──────────┬──────────┘
         │ outbound                  │
         │ (audio, PTT,              │ reads proto
         │  client_event)            ▼
         │                ┌─────────────────────┐
         │                │     hux_proto       │
         │                │  pure JSON <-> msg  │
         │                └─────────────────────┘
         │
  ┌──────┴─────┐
  │  hux_log   │ remote log sink (WARN+ streamed as client_event)
  └────────────┘

  (hux_audio, hux_button — future; same event pattern into hux_app)
```

## Components

| Component               | Owns                                                                     | Never does                                                    |
| ----------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------- |
| `hux_proto`             | JSON ↔ typed-message marshalling                                         | Any I/O, any logging, any blocking call                       |
| `hux_app`               | Session state + event queue + dispatch                                   | Talks to the network or hardware directly                     |
| `hux_net`               | Wi-Fi STA bring-up, WebSocket connect/reconnect, text frame send/receive | Holds business state, logs to the wire (delegated to hux_log) |
| `hux_log`               | `vprintf` hook, log ring buffer, drain task, remote sink callback        | Knows about the protocol envelope (delegated to hux_net)      |
| `hux_audio` _(future)_  | I2S + ES7210 + ES8311 + TCA9555 + PSRAM ring buffers                     | Decides when audio is allowed (that's hux_app)                |
| `hux_button` _(future)_ | GPIO debounce, press/long-press classification                           | Decides what a press means (that's hux_app)                   |

**The dependency graph flows downward only.** `hux_app` requires
`hux_proto`; `hux_net` requires `hux_app` + `hux_proto`; `hux_log`
requires `hux_net` for the sink callback. No cycles. No component
reaches sideways into another component's internals — only through
the header.

## Tasks and priorities

FreeRTOS tasks, in priority order (higher = more important):

| Task                       | Created by           | Prio | Stack | What it does                                      |
| -------------------------- | -------------------- | ---- | ----- | ------------------------------------------------- |
| `hux_app`                  | `hux_app_start`      | 5    | 6 KB  | Dequeues events, runs the state machine           |
| _esp_wifi_                 | IDF internal         | 23   | —     | Wi-Fi driver                                      |
| _websocket_client_         | esp_websocket_client | 5    | —     | WS RX + reconnect loop; invokes our event handler |
| `hux_log_drain` _(future)_ | `hux_log_init`       | 2    | 4 KB  | Pops log ring, calls `hux_net_send_log`           |
| `hux_audio_mic` _(future)_ | `hux_audio_start`    | 8    | 4 KB  | I2S RX, base64-encode, send `audio` frames        |
| `hux_audio_spk` _(future)_ | `hux_audio_start`    | 8    | 4 KB  | Pop PCM ring, I2S TX                              |
| `hux_button` _(future)_    | `hux_button_start`   | 4    | 2 KB  | GPIO debounce + event emit                        |

**Audio tasks are prio 8 — higher than `app`.** If the state machine
starves audio we get glitches; if audio starves the state machine we
only get slightly-delayed decisions. Audio wins.

## Event flow (inbound message → UI effect)

```
  WS DATA (text)  ─▶ hux_net ─▶ forward_ws_text
                                    │
                                    │ malloc + copy
                                    ▼
                               hux_app_post_event(WS_MESSAGE)
                                    │
                                    ▼
                               hux_app event queue
                                    │
                                    ▼
                               app_task.dispatch
                                    │
                                    ├──▶ hux_proto_parse
                                    │         │
                                    │         ▼ typed hux_msg_t
                                    │
                                    ├──▶ transition(state)    (log)
                                    │
                                    └──▶ side effects
                                           (future: spk ring push,
                                            state LED update, etc.)
                                    │
                                    ▼
                               free(ws_message.data)
```

**Ownership invariant**: the producer heap-allocates the message
buffer. Ownership transfers to the queue on successful post. The
consumer (app_task) is responsible for freeing after dispatch.
Producers never retain references.

## Event flow (outbound, future)

```
  GPIO falling edge on K2
        │
        ▼
  hux_button ISR ─▶ hux_app_post_event(PTT_PRESSED, from_isr=true)
                        │
                        ▼
                   app_task: state.owns_mic? yes -> send ptt_start

  I2S RX (mic_task)
        │ PCM frame (20 ms @ 24 kHz = 960 samples × 2 bytes = 1920 B)
        ▼
  base64-encode (2560 chars)
        │
        ▼
  hux_proto_build_audio  →  hux_net_send_text
```

## Memory map (16 MB flash, 8 MB PSRAM, 512 KB SRAM)

```
Flash (16 MB):
  0x000000  bootloader + secondary bootloader   (~50 KB)
  0x008000  partition table                     (~3 KB)
  0x009000  nvs                                 (24 KB)
  0x00f000  otadata                             (8 KB)
  0x011000  phy_init                            (4 KB)
  0x020000  ota_0 app slot                      (2 MB)
  0x220000  ota_1 app slot                      (2 MB)
  0x420000  storage (SPIFFS)                    (1.75 MB)
  0x5e0000  coredump                            (64 KB)      — planned
  0x5f0000  (free)                              (~10 MB free for future)

PSRAM (8 MB):
  Audio ring buffers (mic + speaker + earcons)  (~200 KB planned)
  Wi-Fi / LWIP buffers                          (~100 KB via SPIRAM_TRY_ALLOCATE_WIFI_LWIP)
  Future: TFLM wake-word models (~1–2 MB)

Internal SRAM (512 KB):
  FreeRTOS kernel + task stacks
  ISR handlers
  All allocations < 16 KB (enforced via SPIRAM_MALLOC_ALWAYSINTERNAL)
  Hot audio paths (ring read/write cursors)
```

## Invariants

These must not be broken by any future commit. If a change requires
breaking one, write a new ADR first.

1. **One state owner.** `hux_app_task` is the only task that mutates
   session state. Producers post events; they never touch state.
2. **No mutexes on business state.** Synchronisation is via the event
   queue. Locking belongs to device drivers only (I2C bus mutex,
   etc.).
3. **`hux_proto` is pure.** No I/O, no tasks, no globals. Compiles
   against cJSON alone. Host-testable.
4. **Pre-allocate the audio path.** Audio ring buffers are allocated
   once at init from PSRAM. No `malloc` per frame.
5. **Dual-OTA slots reserved.** Partition table never drops
   `ota_0` / `ota_1`. Shipping OTA later must not require a field
   reflash.
6. **Every inbound wire message has a log line.** Unhandled kinds
   log `ws.rx.<kind> (unhandled in v0)` — silence is a bug.
7. **Every state transition logs.** Transition logs carry
   `from_state` and `to_state`. Skipping a log is a defect — see
   [`../../CLAUDE.md`](../../CLAUDE.md)'s logging-first debugging
   rule.

## What lives in `main/` vs. a component

`main/` holds exactly three things:

- `main.c` — the `app_main` entry point: init each subsystem in
  dependency order.
- `secrets.h` / `secrets.example.h` — per-developer credentials,
  gitignored.
- `idf_component.yml` — managed-component declarations (the registry
  file ESP-IDF reads to pull external components like
  `espressif/esp_websocket_client`).

Anything with real logic goes in a component. `main.c` stays tiny so
the bring-up order is legible and the logic is testable in isolation.

## Adding a new component

1. `components/hux_<thing>/include/hux_<thing>.h` — public API only.
2. `components/hux_<thing>/hux_<thing>.c` — implementation.
3. `components/hux_<thing>/CMakeLists.txt` — `idf_component_register`
   with `REQUIRES` listing header deps (public), `PRIV_REQUIRES` for
   implementation-only deps (log, freertos, etc.).
4. If the component exposes new events into `hux_app`, add the event
   kind to `hux_app.h` and a dispatch case in `hux_app.c`.
5. Write an ADR in `decisions.md` if the component makes a load-
   bearing choice (e.g., "use lock-free ring buffer X").
6. Update this file's component + task table.

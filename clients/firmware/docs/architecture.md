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
| `hux_audio`             | I2S + ES7210 + ES8311 + PSRAM speaker ring, volume                       | Decides when audio is allowed (that's hux_app)                |
| `hux_board`             | I2C bus + TCA9555 (buttons, speaker PA)                                  | Knows what a pin means beyond its name                        |
| `hux_button`            | Talk input (GPIO7 or K2), polled and debounced                           | Decides what a press means (that's hux_app)                   |
| `hux_console`           | Bench commands on the USB serial console (talk, say, selftest, info…)    | Anything the product needs: it is a test seam                 |
| `hux_ota`               | Updates over Wi-Fi into the other slot; keeps it on `ota confirm` (30 s+) | Decides when to update (a developer asks)                     |
| `hux_dev`               | Developer door over Wi-Fi: `/info`, `/cmd` (console lines), `/coredump`  | Answers without the token                                     |
| `hux_opus`              | Opus (esp_audio_codec): mic encoder, speaker decoder task + ordered queue | Decides the format (the server's hello does)                  |

**The dependency graph flows downward only.** `hux_app` requires
`hux_proto`; `hux_net` requires `hux_app` + `hux_proto` + `hux_log`
(the `hux_log_entry_t` struct that the sink callback takes). `hux_log`
itself has NO compile-time dependency on `hux_net` — the sink is
injected at runtime in `main.c` via `hux_log_set_sink(hux_net_send_log)`.
No cycles. No component reaches sideways into another component's
internals — only through the header.

## Tasks and priorities

FreeRTOS tasks, in priority order (higher = more important):

| Task                       | Created by           | Prio | Stack | What it does                                               |
| -------------------------- | -------------------- | ---- | ----- | ---------------------------------------------------------- |
| `hux_app`                  | `hux_app_start`      | 5    | 6 KB  | Dequeues events, runs the state machine                    |
| _esp_wifi_                 | IDF internal         | 23   | —     | Wi-Fi driver                                               |
| _websocket_task_           | esp_websocket_client | 5    | 6 KB  | WS RX + reconnect loop; runs our handler (the data plane)  |
| `hux_log_drain`            | `hux_log_init`       | 2    | 6 KB  | Pops the log queue (256 lines, PSRAM), sends each; offline it holds the line and retries; drop heartbeat |
| `hux_mic`                  | `hux_audio_init`     | 8    | 6 KB  | I2S RX, extract Mic1, hand frames to hux_app's queue       |
| `hux_audio_tx`             | `hux_app_start`      | 6    | 40 KB (PSRAM) | The uplink lane, in order: `ptt_start`, mic audio (60 ms Opus packets, or 20 ms base64 PCM to an older server), `ptt_stop` (10 s queue) |
| `hux_opus_dec`             | `hux_opus_init`      | 7    | 24 KB (PSRAM) | Decodes received Opus packets into the speaker ring; runs queued `audio_clear` in order |
| `hux_spk`                  | `hux_audio_init`     | 8    | 6 KB  | Pop the PCM ring (or silence), I2S TX; services clears     |
| `hux_button`               | `hux_button_start`   | 4    | 4 KB  | Poll GPIO7 + K2 every 10 ms, debounce, post press/release  |
| `hux_console`              | `hux_console_start`  | 3    | 5 KB  | Poll the USB RX FIFO every 20 ms, run bench commands       |
| _httpd_ (`hux_dev`)        | `hux_dev_start`      | 3    | 8 KB  | Developer door; runs `/cmd` console lines on this task     |
| `hux_ota`                  | `hux_ota_start`      | 3    | 8 KB  | Only during an update: download, write, verify, reboot     |

The console's `info` prints each task's smallest free stack so far
(and the heap and PSRAM minimums); `tools/bench.py` fails below 512 B.

**Audio tasks are prio 8 — higher than `app`.** If the state machine
starves audio we get glitches; if audio starves the state machine we
only get slightly-delayed decisions. Audio wins.

## Data plane vs. control plane

Two classes of inbound message, two paths:

- **Data plane**: `audio` chunks. At 50 Hz each frame is ~2 KB base64,
  and there are thousands per minute. Going through `hux_app`'s
  32-entry queue with a full cJSON parse per frame would burn
  ~250 mallocs/sec and buffer less than a second of speech before
  dropping. Instead, `hux_net` recognises `audio` messages up front
  (cheap substring probe), parses the envelope inline, base64-decodes
  into a pre-allocated scratch, and hands the PCM view to a
  registered `hux_net_audio_sink_fn`. Zero heap per frame; `hux_app`
  never sees audio. `audio_clear` takes the same path (its own sink,
  `hux_net_audio_clear_fn`), so a clear is applied in order with the
  audio around it: through `hux_app`'s queue it could land after the
  next reply's first chunks and wipe them. The speaker ring is reset by
  its own reader (`spk_task`); `hux_audio_spk_clear` asks and waits
  (one 20 ms pass), the read-out of the stale bytes follows.
  The ring (4 MB, 87 s) holds a whole model reply, which the server
  sends 5-6x faster than real time. When it is full, the sink waits for
  room: the WS task stops reading, TCP pushes back on the server
  (F-0015). A talk press while the server is speaking (`model_speaking`)
  holds the speaker: inbound audio is dropped until the server's
  `audio_clear`, so the interrupted reply's tail, already on its way,
  never plays over the user (F-0017).
- **Control plane**: everything else (`state`, `status`, `input_mode`,
  `stream_started`, `claim_started`, …). Low-rate, latency-tolerant,
  needs the state machine. These flow through `hux_app`'s event
  queue as before — cheap given the traffic. `hux_app` acts on
  `hello` (protocol, `session_ready`), `state` (logged; a talk press
  while the server isn't CONVERSING says so), `set_volume` (speaker
  gain) and `input_mode` (the mic runs while talk is held, or
  continuously while a skill owns it, e.g. a call).

## Event flow (control plane)

```
  WS DATA (text)  ─▶ hux_net ─▶ forward_ws_text ─┬─▶ looks_like_audio? ─▶ yes
                                                 │                       │
                                                 │                       ▼
                                                 │                  dispatch_audio
                                                 │                  (cJSON parse,
                                                 │                   base64 decode,
                                                 │                   sink callback)
                                                 │
                                                 └─▶ no ─▶ malloc + copy
                                                           │
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
                                                                  (future: state LED,
                                                                   stream label, …)
                                                           │
                                                           ▼
                                                    free(ws_message.data)
```

**Ownership invariant**: the producer heap-allocates the message
buffer. Ownership transfers to the queue on successful post. The
consumer (app_task) is responsible for freeing after dispatch.
Producers never retain references.

## Event flow (outbound)

```
  talk input low: GPIO7 (the case's lever) or K2 (on the board)
        │  polled every 10 ms, 2-sample debounce (hux_debounce)
        ▼
  hux_button task ─▶ hux_app_post_event(PTT_PRESSED)
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
  0x020000  ota_0 app slot                      (2 MB; updates alternate)
  0x220000  ota_1 app slot                      (2 MB)
  0x420000  storage (SPIFFS)                    (1.75 MB)
  0x420000  (clips for the bench `say`, tools/clips.py, in storage)
  0x5e0000  coredump                            (64 KB)
  0x5f0000  (free)                              (~10 MB free for future)

PSRAM (8 MB):
  Speaker ring                                  (4 MB: a whole reply, 87 s)
  WS reassembly + audio decode scratch          (384 KB + 288 KB, EXT_RAM_BSS)
  Mic uplink queue                              (480 KB: 10 s, a slow uplink delays a question, never cuts it)
  Log queue                                     (58 KB: 256 lines kept while offline)
  Opus: packet queue + decoder/encoder state    (~72 KB queue, codec state, two task stacks)
  Wi-Fi / LWIP buffers                          (via SPIRAM_TRY_ALLOCATE_WIFI_LWIP)
  Free at boot after all that                   (~3.4 MB, `info psram=`)

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

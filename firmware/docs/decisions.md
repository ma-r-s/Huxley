# Firmware ADR log

Firmware-specific decisions. Wire-protocol and framework-side decisions
live in [`../../docs/decisions.md`](../../docs/decisions.md); this file
is for things that only matter on-device.

Each entry: the decision, when it was made, the alternative we
rejected, and the reason. Keep entries short. Revisit by writing a new
ADR that supersedes the old one — don't rewrite history in place.

---

## 2026-04-24 — ESP-IDF, not Arduino

**Decision**: Build the firmware on ESP-IDF v5.5 with `idf.py`, not on
Arduino-ESP32 in the Arduino IDE.

**Alternatives**:

- Arduino-ESP32 (Espressif's Arduino core). Faster to first blink,
  bigger library ecosystem, simpler on-ramp.

**Why ESP-IDF wins for this project**:

- The voice pipeline needs tight control over I2S timings, task
  priorities, PSRAM allocation, and WebSocket streaming. Arduino's
  abstractions hide those levers.
- ESP-IDF is the production path. Arduino can't ship OTA, partition
  tables, or coredump the way we'd need later.
- The Waveshare demos ship in both flavors; we lift the parts of the
  ESP-IDF demo we need and drop the rest.

---

## 2026-04-24 — Prototype in-repo under `firmware/`; extract to

`huxley-firmware` later

**Decision**: `firmware/` lives in the main Huxley repo during the
prototype phase. Extract to its own `huxley-firmware` repo once
end-to-end voice works.

**Why**: The wire protocol is still moving. Keeping firmware next to
the server lets protocol + client changes land in the same commit.
Separation kicks in when both sides stabilize — see
[`../../docs/clients.md`](../../docs/clients.md) for the long-term
client-repo strategy.

---

## 2026-04-24 — Actor model with a single state owner

**Decision**: `hux_app`'s task is the only code that mutates session
state. Every other task (`net`, future `mic`/`spk`/`button`) emits
events into `hux_app`'s queue. No mutexes on business state — ever.

**Alternative rejected**: shared-state structs with fine-grained mutexes
(conventional embedded pattern).

**Why**: Eliminates an entire class of concurrency bugs. Adding a new
task means adding an event kind + a dispatch case — no analysis of
which locks to take in which order.

---

## 2026-04-24 — 24 kHz PCM16 mono at the codec

**Decision**: I2S runs at 24 kHz. Both ES7210 (mic ADC) and ES8311
(speaker codec) are clocked to match the wire protocol.

**Alternative rejected**: 16 kHz at the codec + server-side resample
to 24 kHz (or vice versa).

**Why**: Protocol is 24 kHz PCM16 mono. Running the codecs at the same
rate means no on-device resampler, no server-side resampler, no
quality loss at either end. Both codecs support 24 kHz natively — no
cost.

---

## 2026-04-24 — Dual-OTA partition layout from day 1

**Decision**: `partitions.csv` reserves two 2 MB app slots (`ota_0`,
`ota_1`) + `otadata` from the first commit.

**Alternative rejected**: single `factory` partition, migrate to OTA
when needed.

**Why**: Migrating from factory to OTA later means a full reflash for
every device in the field. Reserving the slots costs 2 MB of flash
(we have 16 MB) and zero code. Enabling OTA later is a pure additive
change — drop in `esp_https_ota` or a custom updater, no partition
rewrite.

---

## 2026-04-24 — Remote log streaming via `client_event`

**Decision**: Firmware WARN/ERROR log lines are streamed to the Huxley
server as `{"type":"client_event","event":"huxley.firmware_log",
"data":{"level","tag","line","ts"}}`. The server logs them as
`client.huxley.firmware_log` per the existing generic event channel —
no server-side change required.

**Alternative rejected**: serial-only logging. Mario tails `idf.py
monitor` during every session.

**Why**:

- Debugging grandpa's device in production can't require physical
  access. Streaming puts firmware events in the same log file as
  server events for correlation.
- `client_event` is already first-class in the protocol and the
  `huxley.*` namespace is reserved for framework/client telemetry
  (see [`../../docs/protocol.md`](../../docs/protocol.md) +
  [`../../docs/io-plane.md`](../../docs/io-plane.md) §Namespace
  convention). Zero protocol churn.
- Streaming is best-effort: when the WS is down the log line goes
  only to serial. Network logs don't replace serial — they
  supplement it for the 99% case when the WS is healthy.

Levels above INFO stream by default; INFO/DEBUG stay serial-only to
keep bandwidth bounded during reconnect storms. Threshold is
runtime-tunable via `hux_log_set_remote_level()` (exposed over the
wire later when a `server_event` path opens up).

See [`debugging.md`](./debugging.md) for how to read the stream.

---

## 2026-04-24 — Coredump partition from day 1

**Decision**: Dedicated `coredump` partition in the layout; panic
handler configured to dump there on crash.

**Why**: A crashed board that loses its coredump is a crash we can't
debug. Reserving the partition now (same reasoning as dual-OTA)
costs 64 KB of flash and lets us pull traces with `esp-coredump
info_corefile` after a reboot. No board-side tooling to add later.

---

## 2026-04-24 — PSRAM for large buffers, internal RAM for small

**Decision**: `SPIRAM_USE_MALLOC=y` + `SPIRAM_MALLOC_ALWAYSINTERNAL=16384`
— allocations under 16 KB land in internal SRAM, larger go to PSRAM.

**Why**: PSRAM has measurably higher access latency than internal
SRAM. Small high-frequency allocations (FreeRTOS task state, queue
items, small strings) stay fast; only audio ring buffers and other
kilobyte-scale payloads pay the PSRAM tax.

---

## 2026-04-24 — `K2` = PTT, `K1`/`K3` reserved

**Decision**: The middle button (`K2`) is the push-to-talk trigger.
`K1` and `K3` are wired but do nothing in v0.

**Why**: One button is enough for v0. Reserving the other two means
a future skill or persona-level feature (e.g., `calls.panic_button`
from [`../../docs/triage.md`](../../docs/triage.md) item on hardware
button) can claim one without renumbering anything. Default is to do
nothing rather than guess what the right behavior is.

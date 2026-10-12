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

Default threshold in the component is WARN. The dev build overrides
to INFO in `main.c` via `hux_log_set_remote_level('I')` so every
firmware event lands in the server log during development. Prod
default is WARN — flip back in `main.c` when the firmware is no
longer a prototype. DEBUG always stays serial-only (the vprintf
hook's tag-specific DENY_TAGS keeps recursion-prone tags off the
wire regardless of threshold; see `hux_log.c`).

Threshold is runtime-tunable via `hux_log_set_remote_level()` (will
be exposed over the wire once a `server_event` path opens up).

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

## 2026-04-24 — LWIP TCP send buffer sized for 50 Hz audio bursts

**Decision**: Override the IDF default `CONFIG_LWIP_TCP_SND_BUF_DEFAULT`
(5760 B) and `CONFIG_LWIP_TCP_WND_DEFAULT` (5760 B) to 32 KB each, and
enable `CONFIG_LWIP_WND_SCALE` with `CONFIG_LWIP_TCP_RCV_SCALE=4`.

**Why this matters:**
The mic pipeline emits 1306-byte JSON audio frames at 50 Hz = 65 KB/s.
The 5760 B LWIP send buffer holds ~4 of these. Under momentary
ACK lag from the server (OpenAI upload jitter, Python websockets
scheduling), the buffer fills within ~80 ms. `esp_websocket_client`'s
write path calls `esp_transport_poll_write(timeout=200ms)`; if the
socket isn't writable in that window, it returns 0, which the library
treats as fatal and tears the connection down. Symptom: the WS
flapped ~500 ms after every PTT press, with exactly 16–25 frames
received server-side before the drop.

**Diagnosis path** (recorded in
[`debugging.md`](./debugging.md#diagnosing-ws--tcp-flaps)):

1. Serial capture of board-side logs via `firmware/tools/serial-capture.sh`.
2. `CONFIG_LOG_MAXIMUM_LEVEL_DEBUG=y` in sdkconfig.defaults +
   `esp_log_level_set("websocket_client", ESP_LOG_DEBUG)` at boot —
   exposed the exact error line from inside esp_websocket_client.
3. `sudo firmware/tools/tcpdump-ws.sh` captured the wire-level pattern
   for cross-reference.

**Gotcha worth writing down**: `sdkconfig.defaults` edits only apply
on first configure. If the generated `sdkconfig` already exists with
old values, `idf.py build` keeps them silently. Delete `sdkconfig`
and run `idf.py reconfigure`, then `grep` the new values to confirm.
Burnt ~30 minutes on this in the v0.2.2 debug cycle.

**Alternative rejected**: client-side rate limiting / batched frames /
custom backpressure on top of esp_websocket_client. All fight the
symptom instead of the root cause and add code. A per-socket
`setsockopt(SO_SNDBUF)` via an esp_websocket_client hook would also
work but requires patching the managed component.

**Revisit** if: a future traffic pattern (video frames, larger chunks)
blows past 32 KB. Bump further — the lwip ceiling is 64 KB.

---

## 2026-04-24 — Audio data plane bypasses the control-plane queue

**Decision**: Inbound `audio` messages are recognised in `hux_net`
and dispatched to a registered `hux_net_audio_sink_fn` without ever
touching `hux_app`'s event queue. Only lifecycle messages (`state`,
`status`, `input_mode`, `stream_*`, `claim_*`, `transcript`,
`set_volume`, `model_speaking`, `dev_event`) flow through the
control plane.

**Alternative rejected**: route everything through `hux_app`'s
queue and let it dispatch. Symmetric, tidy — and does not survive
a 50 Hz audio feed. Rough math: 2 KB base64 per frame × 50 Hz =
250 KB/s of heap-copied, cJSON-parsed churn through a 32-entry
queue that would buffer < 640 ms of speech. A single `app_task`
stall drops words.

**Why the seam matters**: `hux_net`'s WS RX task already has the
raw bytes — decoding on the spot and handing a scratch-buffer view
to the sink is zero-copy and zero-malloc per frame. The sink
(owned by `hux_audio` in v0.3) writes straight into its PSRAM
speaker ring. The data plane never touches the control plane.

**Implementation sketch**: `looks_like_audio()` does a cheap
substring probe on the first ~64 bytes of the JSON envelope —
fast path for the common case. If the probe misses (server starts
sorting keys, unlikely), the message falls through to the control
plane and is logged as `ws.rx.audio (unhandled in v0)` — correct
but slow, visible in the stream.

See [`architecture.md`](./architecture.md#data-plane-vs-control-plane).

---

## 2026-10-11 — Talk on GPIO7 (the case's lever) or K2

**Decision**: The talk input is GPIO7 (header pin 2, internal pull-up,
low = talk) OR the on-board K2; either one held is talk, and they post
the same press/release events. The HX-1 case's lever closes a switch
from GPIO7 to GND (the enclosure README on its branch); K2 stays for
the bare board on the bench. A K2 read that fails on the I2C bus now
counts as not pressed (it used to read as pressed, since the button is
active-low): an unreadable button must never open the mic.

**Why GPIO7**: the LCD is unused, so header GPIO3 to GPIO9 are free;
GPIO7 is pin 2, next to GND on 15/16. Polled with K2 in the same 10 ms
loop rather than on an interrupt: one debounce, one code path.

---

## 2026-10-11 — Act on `set_volume`, `input_mode`, `audio_clear`

**Decision**: The firmware now applies the server's `set_volume`
(ES8311 gain), follows `input_mode` (mic continuous during a skill's
claim, such as a Telegram call), and drops queued speaker audio on
`audio_clear`, in order with the audio (data plane). Before this, all
three were logged and ignored: "sube el volumen" did nothing, a call
heard silence from the device, and an audiobook seek played the old
tail first.

**Not done**: echo cancellation during calls (the ES7210's reference
channel is there for it; esp-sr's AFE would be the way), and speaking
`status` text (no on-device TTS; the server's earcons and spoken
replies carry it).

---

## 2026-10-11: Receive side sized to the Wi-Fi RX buffers (supersedes part of 2026-04-24 LWIP)

**Decision**: `TCP_WND` 32 KB (was 64 KB), `TCP_OOSEQ_MAX_PBUFS` 8 (was
0, no cap), Wi-Fi dynamic RX buffers 40 (was 32), `TCP_RECVMBOX_SIZE` 32
(was 6). The send buffer stays 64 KB.

**Why**: measured on the board (triage F-0015). Every segment lwIP holds
pins a Wi-Fi RX buffer; with no cap on out-of-order segments one lost
frame let 32 of them take every buffer (JTAG: `ooseq` = 32), so the
socket went deaf. The 6-entry receive mailbox, not the window, set how
much lwIP could queue for the app, and its 250 ms retry timer throttled
replies to 0.65x real time. Espressif's own rules (Kconfig help, Wi-Fi
guide): window <= dynamic RX buffers, mailbox = window / MSS + 2.

**Alternative rejected**: more RX buffers alone, keeping 64 KB. Without
the OOSEQ cap the wedge only moves; with it, 32 KB is still ~10x the
audio rate at a 50 ms round trip.

---

## 2026-10-11: The speaker ring holds a whole reply; full means wait

**Decision**: 4 MB PSRAM ring (87 s). When it is full, the audio sink
waits for room (TCP then pushes back on the server) instead of
dropping. A clear marks the ring stale and reads it out over the next
20 ms passes.

**Why**: the server forwards a reply as fast as the model makes it
(5-6x real time once the socket was fixed). Dropping lost 60 s of an
80 s story. Pushing back alone lets macOS grow the server's socket
buffer to 4 MB, its keepalive ping waits behind the backlog, and the
server drops the connection after 20 s. Holding the reply makes both
rare: nothing waits on the server below 87 s. Radio and audiobooks
arrive at real time (ffmpeg `-re`), so only model replies burst.

**Alternative rejected**: pacing on the server (bounded `SO_SNDBUF`):
the right long-term bound, but it changes the turn coordinator for every
client; filed as main triage T1.32 for Mario.

---

## 2026-10-11: An evicted walkie waits for its talk button

**Decision**: when the server closes the socket with 1001 "Replaced by
new client" (another client connected), the firmware stops its
WebSocket client and reconnects on the next talk press (with the
offline cue on that press). Any other close reconnects at once.

**Why**: the server serves one client and evicts the older one. The PWA
already defers an evicted hidden tab until it is shown; a walkie that
reconnected at once would evict that client, which reconnects in 2 s:
the two trade places forever. A press is the walkie's "shown".

**Open**: a slow periodic retry, so reminders reach the walkie again
after someone else's session ends, would evict that session every few
minutes. A product call (which client wins), not taken here.

---

## 2026-10-11: Volume: no PA headroom compensation; 50 stays put

**Decision**: ES8311 `hw_gain` with equal PA and DAC voltages (0 dB);
curve 0 / 50 / 100 = -50 / -11.5 / 0 dB.

**Why**: the default (5 V PA, 3.3 V DAC) adds 3.6 dB to every level, so
100 was +3.5 dB of digital gain and clipped a full-scale reply. -11.5 dB
at 50 is the loudness of every hardware test so far (the old boot level),
so nothing sounds different at boot. Read back from the codec register.

---

## 2026-10-11: Bench seams in the product firmware

**Decision**: a serial console (`hux_console`), an electrical loopback
self-test and clip injection ship in the firmware, always on.

**Why**: the board can be tested end to end with no hand on it and no
sound in the room (`tools/bench.py`, nine checks, ~3 min). Without them
every audio or network change waits for someone to hold a button and
listen. They answer only to bytes on the USB port, so a walkie on its
battery never sees them. Revisit in the pre-release sweep (F-0008).

---

## 2026-04-24 — `K2` = PTT, `K1`/`K3` reserved

**Decision**: The middle button (`K2`) is the push-to-talk trigger.
`K1` and `K3` are wired but do nothing in v0.

**Why**: One button is enough for v0. Reserving the other two means
a future skill or persona-level feature (e.g., `calls.panic_button`
from [`../../docs/triage.md`](../../docs/triage.md) item on hardware
button) can claim one without renumbering anything. Default is to do
nothing rather than guess what the right behavior is.

# Firmware roadmap

Milestones, in order. Each phase is self-contained and useful on its
own. Ship one, verify end-to-end on real hardware, move to the next.

Not a commitment — the phases below reflect the current best guess,
not a frozen contract. Scope drifts as we learn from each phase.
[`triage.md`](./triage.md) is the live tracker for what's actually
happening; this file is the target.

---

## v0 — Handshake ✅ _2026-04-24_

Board boots, joins Wi-Fi, opens the WebSocket to the Huxley server,
receives `hello`, validates protocol version, transitions to `READY`.

**Ships**:

- `hux_proto`, `hux_app`, `hux_net` components
- Dual-OTA partition layout
- `secrets.h` / `secrets.example.h` pattern

**Verified**: full boot → `ws.rx.hello protocol=2` → `READY` in ~6 s
on a Waveshare ESP32-S3-AUDIO-Board.

Shipped in commit `9472663`.

---

## v0.1 — Debugging infrastructure _(in progress)_

Before any audio work, make sure we can see what the board is doing
from the server. Every subsequent phase benefits from this
compounding.

**Ships**:

- `hux_log` component: `vprintf` hook, log ring, drain task
- Remote log streaming: firmware `WARN`+ → server as
  `huxley.firmware_log` `client_event`
- Boot banner: git hash, MAC, IP, reset reason, free heap + PSRAM
- Coredump partition + `esp_coredump` docs
- RTC-memory "last panic" stash, logged on next boot
- `firmware/docs/` tree: `architecture.md`, `decisions.md`,
  `debugging.md`, `roadmap.md`, `triage.md`

**Verified when**: forcing a panic on the board produces a coredump
we can decode on the host, AND server log shows matching
`client.huxley.firmware_log level=E` entries timestamp-aligned with
serial.

---

## v0.1.x — Polish of the debugging track (ad-hoc, not its own phase)

Nice-to-haves on top of v0.1 that compound during later phases but
don't block mic/speaker work. Pulled in when cheap.

- **Pre-connect log replay queue** — hold boot-time log lines in a
  small ring (say 64 entries) and drain them the moment the WS
  becomes available. Closes the "early boot = serial only" gap.
- **Tag allow-list for streaming** — the opposite of the current
  deny-list; useful for reducing firmware-log noise during later
  phases while keeping critical tags visible.
- **Reset `boot_counter` on `server_event` request** — lets a developer
  zero the counter after a known-good boot cycle.

---

## v0.2 — Mic

Push-to-talk turns with mic capture. The board becomes useful as a
voice client (output still via the web dev client's speaker for now).

**Ships**:

- `hux_audio` component: ES7210 mic ADC init (via I2C), I2S RX at
  24 kHz mono, PSRAM-resident ring buffer
- `hux_button` component: `K2` GPIO debounce, long-press classifier
- `hux_app` states extended for `CONVERSING` + mic owner tracking
- Outbound wire messages: `ptt_start`, `audio` (base64-in-JSON at
  ~20 ms framing), `ptt_stop`, `wake_word`
- Codec control via TCA9555 I/O expander (MIC_EN pin)

**Verified when**: holding `K2`, speaking "hola" into the board's
mic, and seeing the transcript appear in the web dev client and in
the server log. Pressing for < 133 ms produces the
"too short" nudge (server-side gate from `docs/protocol.md`).

---

## v0.3 — Speaker

Full voice loop: board speaks and listens. No more web client in the
path.

**Ships**:

- `hux_audio` extended: ES8311 codec init, I2S TX, PSRAM playback
  ring buffer sized for ~1 s of audio
- Inbound `audio` → decode base64 → push to ring → I2S TX at 24 kHz
- Respect `audio_clear`: flush the ring on command
- Respect `model_speaking` + `stream_started/stream_ended` for LED
  state (once we add an LED)
- Volume control: ES8311 gain register driven by `set_volume`
- Graceful behavior when the ring underruns (fill silence, not
  garbage)

**Verified when**: full turn works — hold `K2`, speak, release,
hear response through the board's speaker. Interrupt mid-response by
pressing `K2` again and watch `audio_clear` reset playback cleanly.

---

## v0.4 — Status UX

The board needs a presence-of-mind signal for a blind user: something
audible happens when the session state changes, a steady LED for
sighted developers. This is the first persona-aware firmware feature
— AbuelOS needs _audible_ feedback; other personas may not.

**Ships**:

- LED state machine using the onboard WS2812 (1 addressable LED):
  off / blue=connecting / green=idle / cyan=listening /
  magenta=model_speaking / yellow=skill_continuous / red=error
- Earcon playback on session state transitions (source files ship
  with the persona, not the firmware — firmware just plays bytes
  received from the server)
- Boot chime so the user knows when the device has come up

**Verified when**: watching the LED through a full conversation
turn matches server state, and a cold boot produces the boot chime.

---

## v0.5 — `client_event` bidirectional + `server_event`

Catch up to protocol Stage 4 so skills can target the hardware.

**Ships**:

- Emit `client_event` for hardware buttons beyond `K2`
  (`huxley.button_k1`, `huxley.button_k3` — let skills name their
  own subscriptions on top)
- Advertise capabilities in the `hello` response once the protocol
  supports client-side hello (spec change, not shipped yet)
- Receive `server_event` and dispatch to handlers in `hux_app`
- Runtime log-level tuning via `huxley.firmware_log_level`
  `server_event`

---

## v1.0 — Tactile-in-the-room client

The v0-series becomes a product: the device grandpa can hold. Not a
single milestone — everything below is unlocked once v0.4 is stable.

**Candidates** (pulled when real):

- OTA updates via `esp_https_ota` against a local HTTP server
- Battery monitor + low-battery earcon / shutdown behavior
- Power management: deep-sleep when idle for N minutes, `K2` wake
  (may not be compatible with always-streaming logs — design
  trade-off)
- Enclosure fit — GPIO pin stability review, antenna clearance
- Wake-word via on-device TFLM model for the hands-free mode
  (optional; push-button stays the canonical input)

---

## Out of scope forever (until a real need lands)

Items that would make sense on a different project but would bloat
this one today. Not deferred — actively refused. Each new "need"
should re-justify itself.

- Bluetooth audio output. Speaker on-board is enough.
- Onboard LCD UX. The target user is blind; a screen is wasted cost
  and wasted code. Developers use the web dev client for visual
  state.
- Camera. Same — no product need, and the hardware is a distraction.
- TF card storage on the board. Audio streams live on the server;
  offline is not a v1 requirement.
- `wss://` to the server. LAN only; TLS adds cost with no safety
  delta on a trusted home network. Revisit when the device ever
  talks to anything outside the LAN.
- Multi-server / device-pairing UX. One Huxley instance per user
  (see [`../../docs/clients.md`](../../docs/clients.md)). A second
  server means a second build — not a config flag.

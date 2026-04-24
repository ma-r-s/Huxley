# Firmware triage

Firmware-only work tracker. Cross-cutting items that touch the
framework or the wire protocol live in
[`../../docs/triage.md`](../../docs/triage.md) — put them there, not
here.

Items move through the same five gates documented in the main triage
doc:

1. **Validate** — reproduce the problem; capture evidence.
2. **Design** (+ critic if non-trivial).
3. **Implement** — code + regression proof.
4. **Document** — update firmware docs in the same commit.
5. **Ship** — commit reference; entry annotated with commit hash.

Keep entries tight. Link to serial logs, screenshots, or server-log
excerpts via relative paths (store evidence under
`docs/evidence/<id>/` if it's worth checking in).

---

## Active

### F-0001 — v0.1 debugging infrastructure

**Status**: in_progress · **Started**: 2026-04-24

Remote log streaming, boot banner, coredump partition, boot counter
in RTC memory, docs tree. See [`roadmap.md`](./roadmap.md#v01--debugging-infrastructure-in-progress)
for the full deliverables list.

**Ship criterion**: forcing an `ESP_LOGW` on the board produces a
matching `client.huxley.firmware_log level=W` line in the server log,
timestamp-aligned with serial.

---

## Queued (next up, not started)

### F-0003 — v0.3 speaker path

Inbound `audio` → I2S TX. See
[`roadmap.md`](./roadmap.md#v03--speaker).

**Inherits a problem from v0.2.2**: the server sends large audio
replies (observed: 41 KB in one WS message) that arrive fragmented
across multiple TCP segments. Current `hux_net.c:forward_ws_text`
drops any fragmented frame (`ws.rx.fragmented ... — dropped`).
Speaker work must land WebSocket fragment reassembly before it can
play anything — either by bumping `WS_BUFFER_SIZE` above the max
server reply size (ceiling: server-side config), OR by collecting
fragments into a PSRAM scratch keyed on `payload_offset`.

Unblocked by F-0002.

---

## Deferred

### F-DEFER-01 — Resolve Waveshare vendor-driver licensing

Waveshare's demo ZIP ships no LICENSE / COPYING / NOTICE. We vendored
four driver sources under `components/hux_audio/vendor/waveshare/` and
documented the absence in that directory's README. Internal use for
prototyping is fine; **before this repo (or the spun-out
`huxley-firmware` repo) goes public or ships a binary to anyone other
than Mario's household, contact Waveshare and get explicit permission
or find drop-in alternatives** (Espressif `esp-bsp`, Arduino cores,
write-from-scratch against the component datasheets).

**Why not now**: not blocking any v0.x milestone; the right resolution
depends on what the eventual distribution story looks like, which
isn't decided. Revisit before the firmware leaves this repo.

---

## Done

_Moved here with commit hash after ship._

### F-0002 — v0.2 end-to-end mic path → (pending commit)

Mic capture (ES7210 at 24 kHz 4-ch TDM → Mic1 extraction) + K2 PTT +
outbound base64 audio frames → OpenAI transcription verified:
`"Hola, ¿se escucha?"` → response text. 105 frames committed per
~2 sec press; WS stable; no dropped frames on the outbound path. See
[`decisions.md`](./decisions.md) LWIP buffer entry for the WS flap
debug story. Shipped 2026-04-24.

### F-0001 — v0.1.x debugging infra → [`e051258`](../../firmware/), [`158ce6a`](../../firmware/), [`5282dcc`](../../firmware/), [`49711b1`](../../firmware/)

Remote log streaming, boot banner, coredump, RTC boot counter, docs
tree, critic polish, audio data-plane seam, vendored Waveshare
drivers. Shipped 2026-04-24.

### F-0000 — v0 handshake → [`9472663`](../../firmware/README.md)

First working boot → Wi-Fi → WS → hello cycle. Shipped 2026-04-24.

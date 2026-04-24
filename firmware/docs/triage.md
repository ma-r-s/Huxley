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

### F-0002 — v0.2 mic path

Mic capture + PTT button + outbound `audio` frames. See
[`roadmap.md`](./roadmap.md#v02--mic).

Unblocked by F-0001.

### F-0003 — v0.3 speaker path

Inbound `audio` → I2S TX. See
[`roadmap.md`](./roadmap.md#v03--speaker).

Unblocked by F-0002.

---

## Deferred

Nothing yet. Deferred items get a line here with a one-sentence "why
not now" so they don't need to be rediscussed every session.

---

## Done

_Moved here with commit hash after ship._

### F-0000 — v0 handshake → [`9472663`](../../firmware/README.md)

First working boot → Wi-Fi → WS → hello cycle. Shipped 2026-04-24.

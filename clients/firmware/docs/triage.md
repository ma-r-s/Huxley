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

### F-0010: Walkie enclosure (HX series) · in_progress 2026-10-10

3D-printed case for the board, its speaker, an 18650 and a spring-loaded,
very tactile push-to-talk. Code and docs:
[`../enclosure/`](../enclosure/README.md).

**Validation (Gate 1)**: Mario has the bare board and the bare speaker (the
original Waveshare case is gone), so the device has no housing, no battery
and no real talk button. Every dimension now comes from Waveshare's official
STEP (found on docs.waveshare.com; the wiki does not link it), recorded in
[`../enclosure/MEASUREMENTS.md`](../enclosure/MEASUREMENTS.md) and asserted
against the STEP in `enclosure/tests/test_hardware.py`.

**Design (Gate 2)**: four concepts (HX-1 Classic, HX-2 Baton, HX-3 Radio,
HX-4 Can), each a build123d model that passes an automated fit check (board
clearances, speaker, cell bay, MX switch stack vs every printed part). Renders
and a 3D viewer are published as a private artifact for Mario's pick.

**Critic notes (Gate 2)**: a cold reviewer ranked HX-1, HX-2, HX-4, HX-3.
Findings that apply to all four: add a charging dock located by magnets
(pogo pins to the header's `USB_5V`, bench-verify first) so a blind user
never hunts for USB-C; expose physical volume buttons (the board's spare
KEY1/KEY3); add touch markers (dot on the bar, ring around the mic ports);
pick a 60 to 70 g tactile or clicky switch; real fasteners and cell
contacts. Per concept: HX-1's antenna sits under the thumb (rotate the
board), HX-2's trigger pivot is on the wrong end, HX-3 presses PTT when
carried, HX-4 tips and gets gripped onto the bar.

**HX-1 Duo (2026-10-10)**: Mario picked HX-1 and asked for two 18650s with
less empty space. HX-1 Duo (`hx1d_duo.py`): board rotated 90 deg so its
underside plugs sit between two upright cells, speaker under the board with
its magnet nested between the cells, whole interior as the speaker box.
68 × 36.2 × 104 body versus HX-1's 66 × 35 × 125 (11% less volume, 2× cells,
130 cc back volume). PTT became a 49 mm lever on the right side, pinned in a
tilted wall channel because the right cell sits behind that wall (first try,
a tilted external flap, rendered badly and was replaced). Fit check clean; a
test asserts it stays smaller than HX-1 with twice the cells.

**HX-1, rev C (2026-10-10)**: Mario: "let's just call it HX-1", he has the
cells and boards, no keyboard switch, and asked for LED feedback with his
frosted PLA plus tidy back-side cables. The Duo became HX-1 (`hx1.py`; the
one-cell version is "rev A"): PTT now a mouse microswitch clicked by an M2
adjuster screw on the lever, pen-spring return, stop post (overtravel
asserted in a test); frosted light ring on the 7 rear LEDs feeding a frosted
top cap; the channel between the cells is the cable duct (four runs modelled
and fit-checked); 1S2P contact bridges. Section renders (switch, open back)
added for build reference; the 3D viewer's "See inside" now looks like the
x-ray render.

**Parts-only pass (2026-10-10)**: Mario sent photos of his whole inventory
and asked to use nothing else. PTT is now his 12 x 12 tactile switch with a
SciWil 0.4 x 4 x 15 spring in series (lever -> spring -> plunger -> stem):
progressive press then click, tolerance-insensitive, no adjuster screw;
lever pin M2 x 16 + nut; board on M2 x 8 into M2 heat-set inserts. Found
and flagged: the board has no cell protection and his green 18650s look
unprotected, so a 1S protection board is the one missing part; the BAT
socket is 1.25 mm pitch (his kit is 2.5 mm XH). Open: he expected the LED
face toward a frosted back; that conflicts with the mics on the other face
and the cells sharing depth with the board. Three options put to him.

**Frosted back + rocker (2026-10-10)**: Mario: the frosted cap should
continue down the upper two-thirds of the back, no size change (I had
misread it as "board at the back" and first quoted a wrong "few mm" cost
for that; corrected before building). Done: frosted region from z 36 up on
the back shell + cap; cables pulled toward the board in that zone. KCD1-101
moved from the back (would stand 4 mm proud, no depth behind it) to the left
side, fully recessed (test). Protection: Mario skipped it; pocket reserved,
firmware low-voltage shutdown noted in the firmware README.

**Whole holders (2026-10-10)**: Mario found single-18650 holders with two
leads each and will parallel them into the board's charger; asked, he chose
to use the holders whole (not stripped to the springs). Modelled as an
assumed 77 x 21 holder (`hardware.holder()`), replacing the bare cells and
the printed contact bridges; parallel joints off the cells (+ under the
rocker, - at the channel foot); PTT wires rerouted down the lever channel.
Cost: W 68 -> 70, D 36.2 -> 37, back box 127 -> 134 cc. Fit check clean,
25 tests green. Holder size and lead exits still to measure.

**Measurements + fits (2026-10-10)**: Mario measured the holder (76 x 21 x
20.5), the tact switch (7.3 tall, 3.8 mm square stem), the KCD1-101 (18.6
behind the frame), the speaker lead (97) and confirmed top-ported mics. His
rule: everything fits first time and stays put, no glue; sliding fits tuned
for his Bambu P2S (PLA, 0.4). New `huxcase/fits.py` holds every clearance
(per side, print-axis aware; synthesised from published Bambu/PLA tests,
coupon print recommended). Found and fixed while applying it: plunger tube
wall was 0.3 mm (unprintable) -> 1.2 mm, plunger is now a 3.5 mm piston;
the square stem is its own fit-checked envelope; switch pocket has PART
clearance + crush ribs; lever channel and pin hole on `PIVOT`. Found, not
yet fixed: the lever's M2 pin has no lugs and no insertion path; goes into
the case-split design (critic running). Speaker lead route is 22 mm, so the
build cuts it to 45 mm (tested). 28 tests green.

**Rev D: print and assemble, nothing loose (2026-10-10)**: Mario: "all that's
left is for me to print the parts and start assembling... I don't feel or
hear that stuff is moving around", no glue, plastic lever pivot, only his
parts. Two cold critics. The first (case split) found the rev C split could
not take a screw anywhere, the lever's M2 pin had no lugs or way in, and
three printed-part overlaps that `collisions()` never checked (shell vs
shell). The second (buildability) audited every part's location, retention
and assembly step and found the switch could not be inserted (stem in the
tube), cells could jump onto the LEDs in a drop, the lever stop was cut
away by the channel sweep (my order bug), KEY1 scraped the roof, the board
floated 0.45 on its screws, the wires and joints were free, and the
supports. Built:
- two shells (grey faceplate + tub grey/frosted with one filament swap),
  6 x M3 x 8 through the face into M3 x 5 inserts; printed Ø2.4 lever pin
  in two blind seats, captured by closing the case;
- holders on lips + a screwed H-bridge whose front bar stops the cells;
  switch cradle in the tub with a latch finger, tube slot and end plate;
  six crush-ribbed speaker tabs + clamp ring + magnet post; board centring
  posts; spring preload 1.5 mm; KEY1 relief; joint pockets, wire hooks and
  a bridge clip, with the - lead rerouted down the back wall;
- light ring removed (Mario asked why it was needed: the frosted tub
  diffuses);
- `huxcase/printability.py` (overhang check per print pose + posed STL
  export), `tests/test_retention.py` (every bought part has a hard stop
  within 0.5 mm in every direction), `tests/test_printability.py`
  (one solid each, overhang budgets, watertight STLs), shell-vs-shell
  overlap check (`disjoint_shells`). 62 green.
Lessons: a fit check that only proves "no collision" passes floating parts
and unassemblable ones; the one-solid, hard-stop and printability tests
caught what collisions could not. Critic on the plan, then a second critic
on the build, both earned their keep.

**Rev D round 3: a way in for every part (2026-10-10)**: the second
buildability pass moved each part along its insertion path and found rev D
could not be built at all, although every test passed: the holders hit the
z 8 and z 40 screw blocks on the way in (and the cells, the USB boss), the
lever had no path, the grille plug could not pass the lip, the BOOT key
scraped a block while closing. Fixed: four screws instead of six (two
through the face at the top, two up through the floor into faceplate
bosses), so nothing of the tub stands in front of the holders; the USB boss
moved to the faceplate (it travels with the board); full-width lever web
and a spring counterbore so the lever slides in from the front; lip cut on
the plug's path and crush ribs in its window (it stays when the faceplate
is turned over); top blocks cleared along the board's closing path; a web
from the cradle to the roof; the stop joined to the full backing;
printable clip lips. New `tests/test_assembly.py` sweeps every part along
its real path in assembly order and was checked against the previous model
(it catches the holder and lever blocks). 74 green.
Still open from the review: wiring across the halves (F: a Dupont pair or
modelled service loops: Mario's call), joint pockets sized to the splice
with a front lip (G), lever play is held by spring preload only (H), and
the retention test's blind spots (lever, wire runs, a minimum feature
size test).

**Rev D round 4: the seam plug (2026-10-10)**: Mario approved a plug
between the halves and the protection board. The assembly test's bbox
filter was vacuous for the tub (`BoundBox.overlaps` is False on
containment); fixed, it found three blocked paths (switch stem slot, a
protection rail on the right holder's path, the grille flange vs the lip),
all fixed. The wiring went through a design agent: a blind-mate keyed
JST-XH 4-pin from Mario's crimp kit. Changed from its proposal: the
tub-side housing sits in a printed shuttle that floats in a cage, and the
shuttle's chamfered mouth takes the header's box, so alignment rests only
on JST's published outside sizes (`hw.xh_*`, from eXH.pdf), never on the
connector's insides. Board half: B4B-XH-A in a yoke under the two lower
board screws (STEP check: nothing on the board's back there but one rear
LED, 0.5 clear). Later the same day Mario chose his protected 18650s over
a protection board, so the board, its rails and its wiring are gone; the
battery - lead runs from the - joint up the back wall in two clips to the
plug, and the board's own battery plug lead becomes the board pigtails.
Then Mario's readings: the holders' plastic wraps round the cell, so the
lips and the bridge's wall-edge ribs (which assumed flat walls ending at
the cell's centre) are gone; the bridge sits between the holders and crush
ribs on its front bar press the cells, and so the holders, onto the back
wall. The holder envelope now reserves a wrap up to 0.4 mm past the cell's
widest point (more would meet the speaker's magnet: asked). Rocker body
measured 18.9 x 11.8 (assumed 19 x 13, its hole was 1.4 mm too wide); its
panel is 2.8 mm with a 45 deg chamfer so the clips pull it tight wherever
their catch is. Talk input on GPIO7 (header pin 2): the old model
had the talk wires on the GND and 3V3 header pins. Every wire has a cut
length from its route (`WIRE_CUTS`, tested to cover the route plus strip
and crimp). Speaker turned 30 deg, lead 26 mm. Coupon: the peg slides into
every vertical hole, so `SLIDE` 0.20 -> 0.15. A plug coupon (header block,
cage block, real shuttle) tries the mate with Mario's XH parts. 98 green.
Still open: the plug coupon result (the one fit no test proves), the
lever-pin block and the side-hole row, a third buildability review.

**Decisions waiting on Mario**: (resolved: HX-1 is the build, speaker at the
bottom is fine, holders whole, no glue, plastic pivot, protected cells
instead of a protection board, plug between the halves, the board's battery
plug is there); dock yes/no; volume buttons yes/no. Measurements still
wanted (drawn for him): holder wall top edge, rocker body and snap ears,
switch leg spacing; and whether a protected cell fits a holder.

**Firmware impact**: PTT moves from the on-board `K2` (via TCA9555) to an
external switch on GPIO7 (header pin 2; input, pull-up, low = talk; the LCD
is unused).
`hux_button` in F-0003 should read a direct GPIO; keep `K2` as the bench
fallback.

**Docs touched (Gate 4)**: `enclosure/README.md`, `enclosure/MEASUREMENTS.md`,
`firmware/README.md` (enclosure pointer), root `CLAUDE.md` (repo layout).

---

## Queued (next up, not started)

### F-0011 — Talk button on GPIO7 · queued 2026-10-10

**Problem**: the HX-1 case (F-0010) wires its 12 x 12 talk switch to header
pin 2 (GPIO7, other leg to GND through the seam plug); the firmware reads
the board's KEY2 through the TCA9555 (`HUX_TCA_BTN_K2` in
`components/hux_board/include/hux_board.h`).
**Why**: in the case, KEY2 is inside; the lever only reaches the external
switch.
**Solution**: `hux_button` reads GPIO7 directly (input, internal pull-up,
low = talk, the same debounce); keep KEY2 as the bench fallback when nothing
is on GPIO7.
**Effort**: small. Blocked on nothing; test on the bench with a jumper to GND.

### F-0003 — v0.3 speaker path

Inbound `audio` → I2S TX. See [`roadmap.md`](./roadmap.md#v03--speaker).

**Scope (must-haves before the first transcript-to-speaker turn can
play)**:

1. **WebSocket fragment reassembly**. Server replies are observed at
   41 KB in one WS message; our `WS_BUFFER_SIZE` is 32 KB so they
   arrive fragmented across multiple TCP segments. Current
   `hux_net.c:forward_ws_text` drops any fragmented frame
   (`ws.rx.fragmented … — dropped`). Fix: accumulate per-message
   fragments into a PSRAM scratch keyed on `payload_offset` and
   deliver once `payload_offset + data_len == payload_len`. Bumping
   `WS_BUFFER_SIZE` isn't enough — server-side chunks can grow
   unpredictably.
2. **Playback ring in PSRAM via `EXT_RAM_BSS_ATTR`**, sized for
   ~500 ms of 24 kHz PCM16 mono (~24 KB). Not heap-allocated on the
   hot path.
3. **ES8311 codec init** at 24 kHz + TCA9555 P8 (speaker PA) enable
   choreography — 10 ms settle between PA on and first I2S write, per
   Waveshare factsheet.
4. **`audio_clear` dispatch** into `hux_app` — currently in the proto
   enum as `HUX_MSG_UNKNOWN` and logged as `ws.rx.unknown`. Needs to
   drain the playback ring on receipt.
5. **Mic + speaker simultaneous robustness.** The outbound-audio-kills-
   WS flap (v0.2.2) was solved by bumping LWIP send buffer to 32 KB;
   v0.3 doubles bidirectional bandwidth and may re-expose it. See
   F-0004.

Unblocked by F-0002.

---

### F-0004 — WS library fragility under sustained load

**Status**: queued · **Severity**: risk to v0.3 stability

Covers three related observations in `esp_websocket_client` that
v0.2.2 patched around but did not fix:

- `esp_transport_poll_write(0)` timeout → library treats as fatal
  and reconnects. Our LWIP buffer bump makes it rare, not absent;
  any real network hiccup still resets the socket.
- "Could not lock ws-client within 200 timeout" warnings observed
  when inbound audio + log drain + sender all compete for the
  library's single mutex.
- `hux_net_send_text` can't distinguish "short-write, retryable"
  from "socket died, bail" — both return `false`.

**Why defer**: the current setup runs the mic turn cleanly on a LAN
and a flap recovers in ~2 s. Speaker work (F-0003) will stress-test
whether this holds under bidirectional load. If it doesn't, fixes to
evaluate: wrap `hux_net_send_text` with short-write retry semantics,
patch or replace esp_websocket_client for graceful handling, or
coalesce multiple audio frames per send_text call.

---

### F-0005 — hux_app audio-path invariants

**Status**: queued · **Severity**: latent

Cleanup items in `components/hux_app/hux_app.c`:

- `s_b64_buf` + `s_envelope` at file scope are single-writer by
  convention only (comment at line 67). A future concurrent TX
  writer produces torn JSON. Fix: move to sender task stack or
  add a mutex.
- `AUDIO_QUEUE_DEPTH = 10` gives ~200 ms buffer with 200 ms send
  timeout: zero margin. Bump to ≥20.
- Overflow policy is drop-newest. For PTT this truncates the _end_
  of phrases (most load-bearing for the server commit). Consider
  drop-oldest or document the product choice.

---

### F-0006 — String/format contract fragility

**Status**: queued · **Severity**: latent

Several places make fragile assumptions about strings we didn't
define:

- `hux_net.c:looks_like_audio` relies on server key ordering
  (`"type":"audio"` as the first 64 bytes of every audio envelope).
  Python `json.dumps` preserves insertion order today. If the server
  ever prefixes a field, audio falls through to the control plane
  and OOMs the queue.
- `hux_log.c` parses IDF's log-format string back out of vprintf
  output. Format changed between v4 → v5; may change again. Fix:
  use an explicit structured emit path.
- `hux_log.c` recursion guard is one-task-handle-check plus a
  tag-based deny-list. Adding a new network-layer component
  without adding its tag to the list re-opens recursion.

---

### F-0007 — Sender-task + WS-client concurrency

**Status**: queued · **Severity**: structural (manifests under v0.3 load)

`esp_websocket_client` serialises all send/recv through one internal
mutex. Our prio-6 sender at 50 Hz contends with the library's own
ping + RX pump. Observed: the 200 ms lock timeout warnings during the
successful v0.2.2 turn. At v0.3's 2× bandwidth this likely starves
the internal RX path.

**Options to evaluate when v0.3 forces the issue**: coalesce multiple
mic frames into one `send_text` (cuts lock contention N×); replace
esp_websocket_client with a writer-friendlier library; run the
sender task on the opposite core.

---

### F-0008 — Pre-prod sdkconfig sweep

**Status**: queued · **Severity**: cleanup before any public release

Decisions currently tuned for development that must flip before
firmware is distributed:

- `CONFIG_LOG_MAXIMUM_LEVEL_DEBUG=y` compiles DEBUG calls from every
  component into the binary. Saves ~20 KB flash to flip to INFO.
- `main.c` bumps `hux_log_set_remote_level('I')` — prod default
  should be `'W'`.
- `firmware/tools/tcpdump-ws.sh` + serial-capture scripts can stay
  in-repo; they cost nothing at build time.

---

### F-0009 — ES7210 TX-must-be-enabled mystery

**Status**: queued · **Severity**: understood empirically, not at
register level

`hux_audio.c` enables both I2S TX and RX even though v0.2 only uses
RX. Empirical observation: RX-only left ES7210 unlocked
(`esp_codec_dev_read` returned ESP_FAIL). Enabling TX fixed it.
Waveshare's vendored BSP does the same (enables both in its init).

**Theories**:

1. Clocks only flow out of the I2S peripheral once at least one
   direction is enabled, regardless of MASTER role (plausible —
   would explain Waveshare's pattern).
2. `esp_codec_dev_open` on ES7210 has an implicit dependency on the
   TX side being primed (less likely — they're independent handles).
3. Something in the `esp_codec_dev_read` -> `esp_transport_i2s`
   path gates on bidirectional channel state.

**Why defer**: v0.3 enables TX anyway (ES8311), so the "idle TX for
RX lock" concern is moot once the speaker is wired. Worth
re-investigating if v0.3 audio interaction turns out strange.

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

### F-0002 — v0.2 end-to-end mic path → [`0aa7f7a`](../../firmware/) [`3b3b82c`](../../firmware/) [`07a8463`](../../firmware/) [`6ebb87a`](../../firmware/)

Mic capture (ES7210 at 24 kHz 4-ch TDM → Mic1 extraction) + K2 PTT +
outbound base64 audio frames → OpenAI transcription verified:
`"Hola, ¿se escucha?"` → response text. 105 frames committed per
~2 sec press; WS stable; no dropped frames on the outbound path. See
[`decisions.md`](./decisions.md) LWIP buffer entry for the WS flap
debug story. Shipped 2026-04-24 across v0.2.0 / v0.2.1 / v0.2 / v0.2.2.

### F-0001 — v0.1.x debugging infra → [`e051258`](../../firmware/), [`158ce6a`](../../firmware/), [`5282dcc`](../../firmware/), [`49711b1`](../../firmware/)

Remote log streaming, boot banner, coredump, RTC boot counter, docs
tree, critic polish, audio data-plane seam, vendored Waveshare
drivers. Shipped 2026-04-24.

### F-0000 — v0 handshake → [`9472663`](../../firmware/README.md)

First working boot → Wi-Fi → WS → hello cycle. Shipped 2026-04-24.

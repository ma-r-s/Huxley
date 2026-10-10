# Walkie enclosure (HX series)

3D-printable case for the hardware client: the Waveshare ESP32-S3-AUDIO-Board,
its SPK-4020 speaker, two 18650s and a spring-loaded push-to-talk lever. Hold
to talk, release to hear the answer
([`../../../docs/concepts.md`](../../../docs/concepts.md)).

Status: **HX-1 is the build** (rev C, 2026-10-10). Layout, mechanism, light
and cable runs are modelled and fit-checked; case fasteners, battery-holder
details, print orientation and STL export are next. Tracked as F-0010 in
[`../docs/triage.md`](../docs/triage.md).

| Model        | Shape                                  | Talk button                              |
| ------------ | -------------------------------------- | ---------------------------------------- |
| **HX-1**     | two 18650s, 68 × 36 × 104              | 49 mm lever, right side, mouse switch    |
| HX-1 rev A   | one 18650, 66 × 35 × 125 (superseded)  | 2u MX bar + stabiliser, left side        |
| HX-2 Baton   | round head on a grip, 70 × 70 × 148    | hinged trigger on the grip, index finger |
| HX-3 Radio   | landscape box + handle, 128 × 36 × 122 | squeeze bar under the handle rail        |
| HX-4 Can     | cylinder, Ø70 × 89                     | vertical 3u MX bar on the side           |

## HX-1: how two cells end up smaller than one

Mario picked HX-1 (rev A) and asked for two 18650s and less empty space.
Putting a second cell next to the first does not work as-is: the board's
underside has the BAT + SPK plugs (9 mm deep with cables) on one side and the
RTC socket (4.5 mm) on the other, so two side-by-side cells always sit on one
of them. HX-1 moves four things:

1. **Board rotated 90 deg in its plane.** Both plug zones land in the channel
   between two upright cells at x = +/-20.7. The cells hug the side walls.
2. **Speaker under the board, nested.** The speaker's magnet (Ø21.8) fits the
   same channel, so the cells run past the speaker instead of stacking on top
   of it: H drops from 125 to 104. The binding fit is the speaker basket's
   back rim (r 15.8 at 12.3 mm deep) against the cells' inner flanks, which
   sets the cells' depth and D = 36.2. Body volume is 11% below rev A.
3. **No bulkhead.** The whole sealed interior is the speaker's back box:
   about 127 cc versus 62 in rev A.
4. **PTT on the right, as a lever.** The right cell sits right behind the
   side wall, so a bar there cannot travel straight in. It is pinned at its
   bottom end inside a channel in the wall; the channel floor is tilted
   (1 mm of wall left over the cell, deep above it). To make room, the case
   is 68 wide and the internal assembly sits 1 mm left of centre.

Side effects: the ceramic antenna is at the top of the device (out of the
hand); USB-C exits the left side in a deep pocket; KEY1 to KEY3 sit at the top
edge (candidate volume buttons); the bottom 5 mm under the cells is free for
dock contacts.

## The talk switch: a mouse microswitch

Mario has no keyboard switch, so HX-1 uses what every drawer of old
electronics has: the click switch from a mouse (Omron D2F/D2FC class, about
12.8 × 5.8 × 6.5 mm, three legs, wire COM + NO). It clicks crisply but only
moves ~0.5 mm, so the travel comes from the lever:

- **Pen spring** (ballpoint-pen spring cut to ~12 mm, Ø4) in a pocket above
  the switch pushes the lever out and gives the spring-loaded feel.
- **M2 adjuster screw** through the lever's top end clicks the switch. Its
  protrusion (`SCREW_PROUD`, 0.7 mm) sets the click at ~2.9 mm of the 3.3 mm
  stroke at the switch; at the thumb that is about 2.5 mm of travel, then the
  click.
- **Stop post** next to the spring ends the stroke at 3.3 mm, so the switch is
  never pressed past its overtravel (a test asserts this).
- **Rest stop**: the lever's tail below the pin lies on the wall; the spring
  holds it there.
- Fallbacks: a lever/endstop microswitch (3D-printer spares), or a 12 × 12 mm
  dev-kit push button in the same pocket with a spacer.

Wiring: two thin wires from the switch to the 2×9 header's through-hole tails
on the board's back (one free GPIO, GPIO3 to GPIO9, plus GND), routed behind
the light ring and above the right cell.

## The light: frosted ring + frosted cap

The board's 7 WS2812B LEDs face backward, toward the cells. A flat frosted PLA
ring (r 20.8 to 29, 2.5 mm) sits right on them behind the PCB, clamped by the
board's own three M2.5 screws, with notches for the BAT/SPK plugs and the RTC
socket. It carries the light up to a frosted cap: the top 7 mm of the case
(`CAP_Z`), printed in the same frosted PLA, so the top of the device glows.
The ring's back face clears the cells by 0.5 mm (a test asserts it).

## Cables: the channel is the duct

| Run | From | To | Path |
| --- | --- | --- | --- |
| Speaker | SPK plug (board back) | solder tabs on the speaker rim | straight down the channel; mount the speaker so its tabs point up |
| Battery + | BAT plug | top contact bridge | up the channel to the strip joining both + terminals |
| Battery − | BAT plug | bottom contact bridge | down the channel behind the speaker magnet |
| PTT (2) | mouse switch pins | header tails on the board back | above the right cell, behind the light ring |

The two cells are in **parallel (1S2P)**: same model, protected, charged to the
same voltage (within ~0.05 V) before they are joined.

## Parts list

Printed: front and back shell (grey), speaker panel (black), lever (orange),
top cap and light ring (frosted PLA), switch cradle. Have: board, speaker, two
18650s. Scavenge: a mouse microswitch, a ballpoint-pen spring, two springs and
two flat contacts from an old AA holder, an M2 × ~6 screw (adjuster), a 2 mm
pin (an M2 × 20 screw works), thin wire, the board's battery plug lead.

## Layout

```
huxcase/hardware.py      measured board / speaker / cell / switch (single source of truth)
huxcase/concept.py       Concept container + collision (fit) check + cavity volume
huxcase/concepts/hx*.py  one module per concept, Z up, front face at -Y (hx1.py = HX-1, hx1_classic.py = rev A)
huxcase/export.py        build all, refuse on collision, write out/
render/render.py         Blender Cycles renders from out/*.glb: hero, back, x-ray, plus section views (switch, open back)
tests/                   fit check per concept + board model vs the official STEP
MEASUREMENTS.md          every dimension with its source
fetch_reference.sh       downloads Waveshare's STEP / DXF / PDF / schematic (gitignored)
```

## Commands

```bash
cd clients/firmware/enclosure
./fetch_reference.sh                       # once: vendor STEP + drawings into reference/
uv run python -m huxcase.export            # out/<id>.glb, out/<id>.step, out/web/, concepts.json
uv run --group dev pytest -q               # fit checks + STEP comparison (needs reference/)
/Applications/Blender.app/Contents/MacOS/Blender --background \
  --python render/render.py -- out/ out/renders/          # add --quick for previews
```

`out/` is gitignored; everything in it regenerates from the code.

## How the fit check works

Each concept declares packaging **envelopes**: the board with 5.9 mm top
clearance, 2.5 mm under it and a 9 mm plug zone under the BAT/SPK connectors;
the speaker solid; the Ø19.4 × 74 cell bay; the MX switch, stabiliser and
key travel. It also declares its printed **shells**. `collisions()`
intersects every envelope with every other envelope and every shell, and the
export refuses to write a concept that collides. Intended contacts (standoffs
touching the PCB, the switch clipped into its plate) are listed per concept
in `allowed`.

## What these models are not (yet)

Concept models. They prove packaging and show form, not a printable part.
Fasteners, snap fits, battery contacts, board standoffs in the round
concepts, print orientation and tolerances come in the detailed design of
the chosen concept.

# Walkie enclosure (HX series)

3D-printable case concepts for the hardware client: the Waveshare
ESP32-S3-AUDIO-Board, its SPK-4020 speaker, one 18650 and an MX-style
push-to-talk switch. Hold to talk, release to hear the answer
([`../../../docs/concepts.md`](../../../docs/concepts.md)).

Status: **rev A concepts**, waiting for Mario's pick. Tracked as F-0010 in
[`../docs/triage.md`](../docs/triage.md).

| Concept      | Shape                                  | Talk button                              |
| ------------ | -------------------------------------- | ---------------------------------------- |
| HX-1 Classic | portrait brick, 66 × 35 × 125          | 2u MX bar + stabiliser, left side, thumb |
| HX-1 Duo     | HX-1 with two 18650s, 68 × 36 × 104    | 49 mm lever, right side, MX at its top   |
| HX-2 Baton   | round head on a grip, 70 × 70 × 148    | hinged trigger on the grip, index finger |
| HX-3 Radio   | landscape box + handle, 128 × 36 × 122 | squeeze bar under the handle rail        |
| HX-4 Can     | cylinder, Ø70 × 89                     | vertical 3u MX bar on the side           |

## HX-1 Duo: how two cells end up smaller than one

Mario asked for HX-1 with two 18650s and less empty space (2026-10-10).
Putting a second cell next to the first does not work as-is: the board's
underside has the BAT + SPK plugs (9 mm deep with cables) on one side and the
RTC socket (4.5 mm) on the other, so two side-by-side cells always sit on one
of them. The Duo moves four things:

1. **Board rotated 90 deg in its plane.** Both plug zones land in the channel
   between two upright cells at x = +/-20.7. The cells hug the side walls.
2. **Speaker under the board, nested.** The speaker's magnet (Ø21.8) fits the
   same channel, so the cells run past the speaker instead of stacking on top
   of it: H drops from 125 to 104. The binding fit is the speaker basket's
   back rim (r 15.8 at 12.3 mm deep) against the cells' inner flanks, which
   sets the cells' depth and D = 36.2. Body volume is 11% below HX-1.
3. **No bulkhead.** The whole sealed interior is the speaker's back box:
   130 cc versus 62 in HX-1.
4. **PTT on the right, as a lever.** The right cell sits right behind the
   side wall, so a bar there cannot travel straight in. It is pinned at its
   bottom end inside a channel in the wall; the channel floor is tilted
   (0.8 mm of wall left over the cell, deep above it) and the top end of the
   lever presses an MX switch housed above the cell. At rest the lever is flat
   and 3 mm proud. To make room, the case is 68 wide and the internal
   assembly sits 1 mm left of centre.

Side effects: the ceramic antenna is now at the top of the device (out of the
hand); USB-C exits the left side in a deep pocket; KEY1 to KEY3 sit at the top
edge (candidate volume buttons); the bottom 5 mm under the cells is free for
dock contacts. Remaining empty space is the upper-left corner above the left
cell (~8 cc), which is speaker back volume.

## Layout

```
huxcase/hardware.py      measured board / speaker / cell / switch (single source of truth)
huxcase/concept.py       Concept container + collision (fit) check + cavity volume
huxcase/concepts/hx*.py  one module per concept, Z up, front face at -Y (hx1d_duo = HX-1 Duo)
huxcase/export.py        build all, refuse on collision, write out/
render/render.py         Blender Cycles studio renders from out/*.glb
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

# Walkie enclosure (HX series)

3D-printable case for the hardware client: the Waveshare ESP32-S3-AUDIO-Board,
its SPK-4020 speaker, two 18650s and a spring-loaded push-to-talk lever. Hold
to talk, release to hear the answer
([`../../../docs/concepts.md`](../../../docs/concepts.md)).

Status: **HX-1 is the build** (rev C, 2026-10-10). Layout, mechanism, light,
Mario's two 18650 holders and the cable runs are modelled and fit-checked;
case fasteners, holder clips, print orientation and STL export are next. Tracked as F-0010 in
[`../docs/triage.md`](../docs/triage.md).

| Model        | Shape                                  | Talk button                              |
| ------------ | -------------------------------------- | ---------------------------------------- |
| **HX-1**     | two 18650s in holders, 70 × 37 × 104   | 56 mm lever, right side, 12 x 12 tact    |
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
   back rim (r 15.8 at 12.3 mm deep) against the holders' inner walls, which
   sets the cells' depth and D = 37. Body volume is still below rev A.
3. **No bulkhead.** The whole sealed interior is the speaker's back box:
   about 134 cc versus 62 in rev A.
4. **PTT on the right, as a lever.** The right cell sits right behind the
   side wall, so a bar there cannot travel straight in. It is pinned at its
   bottom end inside a channel in the wall; the channel floor is tilted
   (1 mm of wall left over the holder, deep above it). To make room, the
   case is 70 wide and the internal assembly sits 1.2 mm left of centre.

Side effects: the ceramic antenna is at the top of the device (out of the
hand); USB-C exits the left side in a deep pocket; KEY1 to KEY3 sit at the top
edge (candidate volume buttons); the bottom 5 mm under the holders is free
for dock contacts.

## The cells: Mario's holders, used whole

Mario has two single-18650 holders, each with its own pair of leads, and
wires them in parallel so the board's charger charges both (2026-10-10). The
holders go in whole, upright, open side facing the board, floor against the
back. Measured 76 x 21 x 20.5 (spring (-) at the bottom, plate (+) at the
top; `HOLDER_*`). Against bare cells they cost 2 mm of width and 0.8 mm of
depth.

The parallel joints sit off the cells, where the leads are short and nothing
presses on them: the + joint over the channel just under the rocker, the -
joint at the bottom of the channel next to the protection pocket. From each
joint one lead goes on to the board. Before joining, charge both cells to the
same voltage (within ~0.05 V); a large gap makes one cell dump current into
the other when they meet.

## Fits: everything goes together first time, no glue

Mario's rule (2026-10-10): every part must fit and stay put by design. All
clearances live in [`huxcase/fits.py`](huxcase/fits.py), per side, tuned as
starting points for his Bambu Lab P2S with PLA and a 0.4 mm nozzle:

| Interface | Clearance | Why |
| --- | --- | --- |
| Printed slides on printed, drawn in X/Y | 0.20 | `SLIDE`: free, no rattle |
| Same, bore lying horizontal on the bed | 0.30 | `SLIDE_HBORE`: horizontal holes print oval |
| Removable but stays (cap, tongue) | 0.12 | `SNUG` |
| Metal pin turning in print (lever pivot) | 0.25 | `PIVOT` |
| Bought part into a printed pocket | 0.30 + crush ribs | `PART`: always goes in; 0.8 mm ribs crush 0.15 to take up the slack |
| Heat-set insert bores | M2 3.2, M3 4.0 | `INSERT_BORE` (confirm against the kit's inserts) |

No P2S-specific tolerance data is published yet, so the first print should be a
small coupon with these fits; any correction goes into `fits.py` and every
part follows.

## The talk switch: 12 x 12 tactile button + series spring

Built only from Mario's parts drawer (2026-10-10): his 12 x 12 mm tactile
button (green cap not used) and a 0.4 x 4 x 15 mm spring from the SciWil kit.

- The orange lever is pinned at its bottom end. (How the pin is captured
  is being redesigned with the case split: the first model had no way to
  fit the screw.)
- Its top end presses a **spring in series** with the switch: lever ->
  spring -> printed plunger -> switch stem. The plunger is a 3.5 mm piston
  in a 1.2 mm-walled tube on the switch cradle; the switch sits in a pocket
  with crush ribs. Mario's switch stem is a 3.8 mm square, and its corners
  clear the tube bore (tested). The press feels progressive
  (spring-loaded) until the spring reaches the switch's actuation force,
  then the switch clicks. The click point no longer depends on print
  accuracy, so there is no adjuster screw.
- Nominal (2 N switch): click after ~3.0 mm at the switch axis, ~1.9 mm under
  the thumb; the stop post ends the stroke at 3.8 mm. A stiff switch (2.6 N)
  still clicks with one 0.5 mm washer under the plunger flange; for a
  firmer, shorter press use the 0.4 x 4 x 10 spring. Tests assert the click
  always lands before the stop and the spring never goes solid.
- The two wires go from two legs on the same end of the switch (5 mm apart:
  check with a multimeter which pair switches) to the 2x9 header's
  through-hole tails on the board's back (one free GPIO, GPIO3 to GPIO9,
  plus GND).

## The light: frosted back, frosted cap, light ring

The board's 7 WS2812B LEDs face backward, toward the cells (the mics are on
the other face and must face the user). Mario's call (2026-10-10): print the
top cap **and the upper two-thirds of the back** (`BACK_FROST_Z` = 36 mm up,
including the sides' back half) in his frosted PLA. Same size as before; the
light shows through the top and the back, strongest above the cells and in
the channel between them, with the cells visible as soft shadows.

A flat frosted ring (r 20.8 to 29, 2.5 mm) sits right on the LEDs behind the
PCB, clamped by the board's three screws, with notches for the BAT/SPK plugs
and the RTC socket. It spreads all 7 LEDs into a glow instead of 7 dots and
feeds the cap. The ring's back face clears the cells by 0.5 mm (a test).

## Power switch: KCD1-101 on the left side

Recessed 7.2 mm in the left side above the left holder, where there is
~38 mm of depth behind the board (the back had only 20 mm before the light
ring, so a back-mounted rocker would have stood 4 mm proud and added depth).
Wired in the battery + lead between the BAT plug and the + joint. The
charger sits behind it too, so **the rocker must be ON to charge**. Power on
the left, talk on the right: easy to tell apart by touch. A test asserts it
stays below the surface.

## Battery protection: pocket reserved, not fitted

Mario chose to skip it for now. The board has no cell protection (see
MEASUREMENTS), so a 26 x 5 x 3.5 mm pocket in the grey bottom third, behind
the speaker, is reserved for a 1S board (DW01 + 8205) in the - lead. Until
then the firmware should shut the device down at a safe cell voltage via
`BAT_ADC` (GPIO1).

## Cables: the channel is the duct

In the frosted zone the leads run close to the board (y ~0 to 2 mm), as far
from the frosted back as possible, so they read as one soft spine.

| Run | From | To | Path |
| --- | --- | --- | --- |
| Speaker | SPK plug (board back) | solder tabs on the speaker rim | straight down the channel; mount the speaker so its tabs point up. **Cut the 97 mm lead to 45 mm** and re-solder at the speaker: the route is 22 mm, and loose wire buzzes in the back box |
| Battery + | BAT plug | rocker, then the + joint | up the channel close to the board, to the rocker above the left holder |
| Holder + leads (2) | top of each holder | + joint over the channel | over the holder tops, under the rocker |
| Battery − | BAT plug | protection pocket, then the - joint | down the channel; behind the speaker magnet only in the grey bottom third |
| Holder - leads (2) | bottom of each holder | - joint at the channel's foot | under the speaker, in the grey bottom third |
| PTT (2) | tact switch legs | header tails on the board back | down the lever channel, in front of the right holder's top cap |

The two cells are in **parallel (1S2P)**: same model, same charge.

## Parts list

Only Mario's inventory (photos 2026-10-10): Kadrick M2-M5 button-head
screws, nuts and washers; FFVRVSS M2-M5 heat-set inserts + tool; SciWil
compression springs (0.3-0.8 wire, 2-8 mm OD); Noxtren JST-XH + Dupont kit
with 1 m ribbon cable; KCD1-101 rocker; 12 x 12 tactile button; two green
18650s in two single-cell holders with leads; the board and its speaker
(lead ~75 mm, leaves at the rim).

Printed: front and back shell (grey), speaker panel (black), lever (orange),
top cap and light ring (frosted PLA), switch cradle and plunger. Board: 3 x
M2 x 8 + washers into M2 x 2 inserts. Lever pin: M2 x 16 + nut. Switch:
12 x 12 tact + 0.4 x 4 x 15 spring. Wires: ribbon cable (double the
conductors for the battery). Not in the inventory: a 1.25 mm 2-pin plug for
the BAT socket (the JST-XH kit is 2.5 mm), unless the leads are soldered
straight to the board's battery pads. A 1S protection board is optional
(skipped for now, pocket reserved).

## Layout

```
huxcase/hardware.py      measured board / speaker / cell / holder / switches (single source of truth)
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
the speaker solid; the cells (HX-1: Mario's whole holders; the older
concepts: a Ø19.4 × 74 bay); the talk switch with its travel; the rocker; and
every cable and lead as a swept tube. It also declares its printed **shells**. `collisions()`
intersects every envelope with every other envelope and every shell, and the
export refuses to write a concept that collides. Intended contacts (standoffs
touching the PCB, the switch clipped into its plate) are listed per concept
in `allowed`.

## What these models are not (yet)

Concept models. They prove packaging and show form, not a printable part.
Fasteners, snap fits, battery contacts, board standoffs in the round
concepts, print orientation and tolerances come in the detailed design of
the chosen concept.

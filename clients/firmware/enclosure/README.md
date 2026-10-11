# Walkie enclosure (HX series)

3D-printable case for the hardware client: the Waveshare ESP32-S3-AUDIO-Board,
its SPK-4020 speaker, two 18650s and a spring-loaded push-to-talk lever. Hold
to talk, release to hear the answer
([`../../../docs/concepts.md`](../../../docs/concepts.md)).

Status: **HX-1 is the build** (rev D, 2026-10-10). Every part is located and
held by printed features, screws or crush ribs, no glue; every bought part has
a hard stop within 0.5 mm in every direction (tested); every printed piece is
one solid with a known print pose (tested). Next: Mario prints the fit coupon,
then the parts. Tracked as F-0010 in [`../docs/triage.md`](../docs/triage.md).
The interactive build page (spin, explode, x-ray, build it) comes from
`huxcase/site.py`.

| Model        | Shape                                  | Talk button                              |
| ------------ | -------------------------------------- | ---------------------------------------- |
| **HX-1**     | two 18650s in holders, 70 × 37 × 104   | 56 mm lever on a printed pin, 12 x 12 tact |
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
   sets the cells' depth and D = 37.
3. **No bulkhead.** The whole sealed interior is the speaker's back box,
   about 136 cc versus 62 in rev A.
4. **PTT on the right, as a lever** turning in a channel in the right wall.
   The case is 70 wide and the internal assembly sits 1.2 mm left of centre.

Side effects: the ceramic antenna is at the top of the device (out of the
hand); USB-C exits the left side in a deep pocket; KEY1 to KEY3 sit at the top
edge (candidate volume buttons); the bottom under the holders is free for dock
contacts.

## The case: two prints, six screws

- **Faceplate** (grey, prints face down). The face, its 2 mm lip that seats in
  the tub's rim (`SNUG`), the board's three standoffs and three centring
  posts, the two mic tubes, the charge-light tube, six screw pads and the
  tooth that holds the lever pin's front end.
- **Tub** (prints standing up: grey to 36 mm, then one filament swap to
  frosted). Everything else, roof included: so the LEDs glow through the top,
  the upper back and the upper sides.
- **Closure**: six M3 x 8 button heads through the face, flush in
  counterbores, into M3 x 5 heat-set inserts in blocks on the tub's walls.
  Visible, honest screws, TE style; nothing to snag.

The old three-piece split (rev C) could not be fastened: its seams left no
room for a screw boss and cut through the rocker, the lever channel and the
switch.

## What holds every part

| Part | Located by | Held by |
| --- | --- | --- |
| Board | three standoffs + three crush-ribbed centring posts (faceplate) | 3 x M2 x 8 + washer, self-tapping into the standoffs |
| Speaker | six crush-ribbed tabs on the grille plug | clamped between the plug's ring and a post on the tub's back wall when the case closes |
| Grille plug (black) | its window in the face | its flange in a recess behind the face, clamped by the speaker |
| Holders (x2) | lips on the outer walls, crush-ribbed pedestals and pads | the bridge: one M3 x 6 into an insert on the back wall; its ribs press both holders onto the back wall |
| Cells | the holders | the holders' springs, and the bridge's front bar in a drop |
| 12 x 12 switch | cradle in the tub, legs in two slots | crush ribs; a latch finger snaps over its front face |
| Plunger + spring | the tube | the spring's 1.5 mm preload between lever and plunger |
| Lever | its printed pin | the pin sits in two blind seats (tub behind, faceplate tooth in front), captured when the case closes; a dome takes up its axial play |
| Power switch | its panel in a recess in the left side | its own snap ears |
| Joints (+ and -) | pockets on the back wall | pressed in between two shelves (in heat-shrink) |
| Battery - lead | two hooks on the back wall | their lips |
| Battery + lead | a clip on the bridge | its lip |

Every bought part above has a hard stop within 0.5 mm in every direction
(`tests/test_retention.py`); crush ribs take up the rest so nothing rattles.

## The cells: Mario's holders, used whole

Mario has two single-18650 holders, each with its own pair of leads, and wires
them in parallel so the board's charger charges both (2026-10-10). The holders
go in whole, upright, open side facing the board, floor against the back.
Measured 76 x 21 x 20.5 (spring (-) at the bottom, plate (+) at the top;
`HOLDER_*`). Each one goes in 1 mm toward the middle, then slides out under
its two lips; the bridge goes across both and is screwed down. The lips grip
the holders' side walls, assumed 1.2 mm thick and as high as the cells'
centre: check them before printing (MEASUREMENTS).

The parallel joints sit off the cells: the + joint over the channel just under
the rocker, the - joint at the channel's foot, each in its own pocket. Before
joining, charge both cells to the same voltage (within ~0.05 V); a large gap
makes one cell dump current into the other when they meet.

## Fits: everything goes together first time, no glue

Mario's rule (2026-10-10): every part must fit and stay put by design. All
clearances live in [`huxcase/fits.py`](huxcase/fits.py), per side, tuned as
starting points for his Bambu Lab P2S with PLA and a 0.4 mm nozzle:

| Interface | Clearance | Why |
| --- | --- | --- |
| Printed slides on printed, drawn in X/Y | 0.20 | `SLIDE`: free, no rattle |
| Same, bore lying horizontal on the bed | 0.30 | `SLIDE_HBORE`: horizontal holes print oval |
| Removable but stays (faceplate lip) | 0.12 | `SNUG` |
| Lever swinging in its channel | 0.25 | `PIVOT` |
| Bought part into a printed pocket | 0.30 + crush ribs | `PART`: always goes in; 0.8 mm ribs crush 0.15 to take up the slack |
| Heat-set insert bores | M2 2.8, M3 4.0 | `INSERT_BORE`, from the FFVRVSS kit label (tip / knurl) |

No P2S-specific tolerance data is published yet, so the first print is the
**fit coupon** (`uv run python -m huxcase.coupon` writes `out/coupon_plate.stl`
and `out/coupon_peg.stl`; 25 cc, print the plate flat and the peg standing,
as exported). It has the 6 mm peg's clearance ladder (0.10 to 0.30 per side)
twice: as vertical holes and as horizontal bores into the front face. It also
has a pocket for the 12 x 12 switch with the real crush ribs, M2 and M3 insert
bores, and an M2 pivot hole. Report per row the tightest hole the peg goes
into by hand and stays (`SNUG`), and the tightest where it slides and turns
freely (`SLIDE`, `SLIDE_HBORE`). Corrections go into `fits.py` and every part
follows.

## The talk switch: 12 x 12 tactile button + series spring

Built only from Mario's parts drawer: his 12 x 12 mm tactile button (green
cap not used) and the 0.4 x 4 x 15 mm spring from the SciWil kit.

- The orange lever turns on a **printed Ø2.4 pin** (no screw, no metal pin).
  The pin goes through the lever's knuckle; its ends sit in a blind seat in
  the tub behind the channel and one in the faceplate's tooth in front of it,
  so closing the case captures it. To fit it: hold the lever 2.5 mm forward,
  press it in, slide it back into the rear seat.
- Its top end presses a **spring in series** with the switch: lever ->
  spring -> printed plunger -> switch stem. The press feels progressive
  (spring-loaded) until the spring reaches the switch's actuation force, then
  the switch clicks. The click point does not depend on print accuracy.
- 1.5 mm of spring preload (0.9 N) holds the lever firmly on its rest stop.
  Nominal (2 N switch): click after ~1.9 mm at the switch axis (~1.1 mm under
  the thumb dimple); a block on the backing stops the stroke at 3.8 mm. A
  stiff switch (2.6 N) still clicks with one 0.5 mm washer under the plunger.
  Tests assert the click always lands before the stop and the spring never
  goes solid.
- The switch slides into its cradle from the front (the stem through a slot
  in the tube) until a latch finger snaps over its front face. Solder the two
  wires to the leg pair that switches first (check with a multimeter); they
  go to the 2x9 header's through-hole tails on the board's back (one free
  GPIO, GPIO3 to GPIO9, plus GND).

## The light: the frosted tub

The board's 7 WS2812B LEDs face backward, toward the cells (the mics are on
the other face and must face the user). The tub is frosted from 36 mm up
(`BACK_FROST_Z`): the top, the upper back and the upper sides. The light shows
softest where the holders sit and brightest above them and in the channel.
Rev C had a frosted ring on the LEDs to even out the glow; Mario asked why it
was needed, and it was not: the frosted tub diffuses the light. Dropping it
also removed two overlaps.

## Power switch: KCD1-101 on the left side

Recessed 7.2 mm in the left side above the left holder. Wired in the battery +
lead between the BAT pad and the + joint. The charger sits behind it too, so
**the rocker must be ON to charge**. Power on the left, talk on the right: easy
to tell apart by touch. A test asserts it stays below the surface.

## Battery protection: recommended, not fitted

The board has no cell protection (see MEASUREMENTS). Two unprotected cells in
parallel with no fuse means a pinched wire is a short of ~20 A. Mario chose to
skip a protection board for now; its 26 x 5 x 3.5 mm pocket stays reserved
behind the speaker, the firmware should shut down at a safe voltage via
`BAT_ADC` (GPIO1), and every joint goes in heat-shrink and its pocket. A
DW01 + 8205 1S board is still the recommendation.

## Cables

| Run | From | To | Held by |
| --- | --- | --- | --- |
| Speaker | SPK plug (board back) | the speaker's solder tabs | **cut the 97 mm lead to 30 mm** and re-solder at the speaker: the route is 22 mm, and loose wire buzzes |
| Battery + | board BAT pad | rocker, then the + joint | the clip on the bridge |
| Holder + leads (2) | top of each holder | + joint | the joint's pocket |
| Battery − | board BAT pad | down the back wall to the - joint | two hooks on the back wall |
| Holder - leads (2) | bottom of each holder | - joint | the joint's pocket |
| PTT (2) | tact switch legs | header tails on the board back | short and direct, in front of the right holder |

The board's battery socket is 1.25 mm pitch (the JST-XH kit is 2.5 mm): solder
the battery leads to the socket's pads unless a 1.25 mm plug turns up.

## Print guide

| Piece | Colour | Pose on the bed | Supports |
| --- | --- | --- | --- |
| Faceplate | grey | face down | none |
| Grille plug | black | face down | none |
| Tub | grey, swap to frosted at 36 mm | standing up, open front facing you | tree supports: roof, switch cradle and tube, rocker mount; all removed through the open front |
| Lever | orange | outer face down | none |
| Lever pin | grey | lying flat | none |
| Plunger | grey | standing on its flat end | none |
| Holder bridge | grey | front face down | none |

`huxcase/printability.py` measures what needs support in each pose
(`tests/test_printability.py` holds every piece to that).

## Assembly

Tub, lying on its back:
1. Heat-set the seven M3 x 5 inserts: six in the corner blocks, one in the
   boss on the back wall.
2. Rocker: solder two leads, heat-shrink, snap it in from outside.
3. Talk switch: solder its two wires, slide it into the cradle until it
   clicks. From outside, drop the plunger into the tube, then the spring.
4. Lever: push the pin through it; set it in 2.5 mm forward, slide it back.
5. Holders: each in 1 mm toward the middle, then out under its lips. Join the
   + leads with the rocker's lead, the - leads with the battery - lead; put
   both joints in heat-shrink and press them into their pockets. Clip the -
   lead behind its two hooks.
6. Cells in (within 0.05 V of each other, + up). Bridge across, M3 x 6.

Faceplate, face down:
7. Drop the grille plug into its window, flange inside.
8. Cut the speaker lead to 30 mm, re-solder it, set the speaker on its tabs.
9. Board onto its standoffs (USB-C to the left), 3 x M2 x 8 with washers. Plug
   in the speaker. Solder the battery leads to the BAT pads and the talk
   wires to the header tails.

Close:
10. Rocker OFF. Lower the faceplate onto the tub (the lever pin's front end
    goes into the tooth) and fit the six M3 x 8, crosswise, snug.
11. Test: rocker ON, the lever clicks; on USB-C the charge light comes on.

## Parts list

Printed: faceplate, tub, grille plug, lever, lever pin, plunger, holder bridge
(the build page lists each with its volume).

Bought, all from Mario's inventory (photos 2026-10-10): the board and its
speaker; two 18650 cells in two holders; the 12 x 12 tact switch; the SciWil
0.4 x 4 x 15 spring; the KCD1-101 rocker; Kadrick button heads 6 x M3 x 8,
1 x M3 x 6, 3 x M2 x 8 with flat washers; FFVRVSS 7 x M3 x 5 heat-set inserts;
ribbon cable (double the conductors for the battery leads).

Tools and consumables assumed: soldering iron and solder, the insert tool,
hex keys 1.3 mm (M2) and 2.0 mm (M3), a multimeter (which switch legs switch;
cell voltages before paralleling), and heat-shrink for the joints.

## Layout

```
huxcase/hardware.py      measured board / speaker / cell / holder / switches (single source of truth)
huxcase/concept.py       Concept container + collision (fit) check + cavity volume
huxcase/concepts/hx*.py  one module per concept, Z up, front face at -Y (hx1.py = HX-1, hx1_classic.py = rev A)
huxcase/export.py        build all, refuse on collision, write out/
huxcase/fits.py          every clearance (P2S, PLA, 0.4 nozzle); huxcase/coupon.py prints a test of them
huxcase/printability.py  print pose per piece + what still needs support
huxcase/site.py          the HX-1 build page: per-part glTF + assembly steps (site/index.html is the page)
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
uv run python -m huxcase.site           # out/site/: the interactive HX-1 build page (spin, explode, x-ray, build)
```

`out/` is gitignored; everything in it regenerates from the code. The build page is published as a
private artifact from `out/site/` (index.html + hx1.gltf.json); `preview.html` is the same page for
a local static server. Every HX-1 part must belong to a build step (`site.STEPS`, tested).

## How the fit check works

Each concept declares packaging **envelopes**: the board with 5.9 mm top
clearance, 2.5 mm under it and a 9 mm plug zone under the BAT/SPK connectors;
the speaker solid; the cells (HX-1: Mario's whole holders); the talk switch
with its travel; the rocker; screws and inserts; and every cable and lead as a
swept tube. It also declares its printed **shells**. `collisions()` intersects
every envelope with every other envelope and every shell; for HX-1
(`disjoint_shells`) it also intersects every printed piece with every other,
since they are separate prints. The export refuses to write a concept that
collides. Intended contacts (the board on its standoffs, crush ribs into a
part, a screw in its insert) are listed per concept in `allowed`, and the
crush ribs are tested from the other side: they must really reach into what
they hold.

## What these models are not (yet)

HX-1 rev D is a printable design: fit-checked, retention-tested and
printability-checked, with clearances that the fit coupon confirms on Mario's
printer. The other HX concepts are packaging sketches (form, not printable
parts). Still open for HX-1: the measurements in MEASUREMENTS "Still to
measure", the coupon results, and the battery protection board (recommended).

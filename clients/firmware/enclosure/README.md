# Walkie enclosure (HX series)

3D-printable case for the hardware client: the Waveshare ESP32-S3-AUDIO-Board,
its SPK-4020 speaker, two 18650s and a spring-loaded push-to-talk lever. Hold
to talk, release to hear the answer
([`../../../docs/concepts.md`](../../../docs/concepts.md)).

Status: **HX-1 is the build** (rev E, 2026-10-11). Every part is located and
held by printed features, screws or crush ribs, no glue; every bought part has
a hard stop within 0.5 mm in every direction (tested); every printed piece is
one solid with a known print pose, and nothing starts in mid-air (tested); the
sliced tub has no support touching a close fit (checked in its G-code). The
fit coupon is printed and folded in; the faceplate, grille, a pin and a
plunger are printed (2026-10-11); next: the rest, on one AMS plate
(`out/print/HX1_one_plate_AMS.3mf`). Tracked as F-0010 in [`../docs/triage.md`](../docs/triage.md).
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

## The case: two prints, four screws

- **Faceplate** (grey, prints face down). The face, its 2 mm lip that seats in
  the tub's rim (`SNUG`), the board's three standoffs and three centring
  posts, the USB-C boss (it travels with the board), the two mic tubes, the
  charge-light tube, two screw pads at the top, two insert bosses at the
  bottom and the tooth that holds the lever pin's front end.
- **Tub** (prints standing up: grey to 36 mm, then one filament swap to
  frosted). Everything else, roof included: so the LEDs glow through the top,
  the upper back and the upper sides.
- **Closure**: four M3 x 8 button heads, all flush in counterbores. Two go
  through the face at the top into inserts in the tub's top corner blocks
  (above the holders). Two go up through the floor into inserts in bosses on
  the faceplate. Nothing of the tub stands in front of the holders, so they
  and the cells go in from the front (an assembly test proves every part's
  way in).

The old three-piece split (rev C) could not be fastened: its seams left no
room for a screw boss and cut through the rocker, the lever channel and the
switch.

## What holds every part

| Part | Located by | Held by |
| --- | --- | --- |
| Board | three standoffs + three crush-ribbed centring posts (faceplate) | 3 x M2 x 8 + washer, self-tapping into the standoffs |
| Speaker | six crush-ribbed tabs on the grille plug | clamped between the plug's ring and a post on the tub's back wall when the case closes |
| Grille plug (black) | its window in the face | its flange in a recess behind the face, clamped by the speaker |
| Holders (x2) | the side walls; sideways, at their full-height end caps: the bridge's tabs beside the + caps, floor lugs beside the - caps (crush-ribbed, so the holders' wall height does not matter); crush-ribbed pedestals and pads | the bridge: one M3 x 6 into an insert on the back wall; ribs on its front bar press both cells, and so the holders, onto the back wall |
| Cells | the holders | the holders' springs, and the bridge's front bar in a drop |
| 12 x 12 switch | cradle in the tub, legs in two slots | crush ribs; a latch finger snaps over its front face |
| Plunger + spring | the tube | the spring's 1.5 mm preload between lever and plunger |
| Lever | its printed pin | the pin sits in two blind seats (tub behind, faceplate tooth in front), captured when the case closes; 0.9 N of spring preload holds it against its stop |
| Power switch | its panel in a recess in the left side | its own snap ears |
| Battery - lead | two clips on the back wall, beside the speaker post | their lips |
| Joints (+ and -) | pockets on the back wall | pressed in between two shelves (in heat-shrink) |
| Wires | the - lead in its back-wall clips, the joints in their pockets | everything else loose with slack, on purpose (Mario, 2026-10-11): only the heavy parts need a place |

Every bought part above has a hard stop within 0.5 mm in every direction
(`tests/test_retention.py`); crush ribs take up the rest so nothing rattles.

## The cells: Mario's holders, used whole

Mario has two single-18650 holders, each with its own pair of leads, and wires
them in parallel so the board's charger charges both (2026-10-10). The holders
go in whole, upright, open side facing the board, floor against the back.
Measured 76 x 21 x 20.5 (spring (-) at the bottom, plate (+) at the top;
`HOLDER_*`). Each one slides straight in from the front; then the bridge is
screwed down between them. Their plastic wraps round the cell (Mario), so
nothing grips the holders' walls: crush ribs on the bridge's front bar press
each cell, and so its holder, onto the back wall. Sideways, each holder is
stopped at its two end caps, which stand full height: the bridge's tabs reach
up beside the + caps and two lugs on the floor stand beside the - caps
(`tests/test_retention.py` checks the stops on the caps alone). The fit check
reserves room for the plastic wrapping 0.4 mm past the cell's widest point,
and 0.85 of the radius for the tall stretch next to each cap.

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
| Printed slides on printed, drawn in X/Y | 0.15 | `SLIDE`: free, no rattle (coupon: the peg slides into every vertical hole, .10 to .30) |
| Same, bore lying horizontal on the bed | 0.20 | `SLIDE_HBORE`: horizontal holes print oval (lever-pin block: the 2.4 pin turns freely in the .20 hole) |
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
  so closing the case captures it. To fit it: pin through the lever, slide
  the lever in from the front along its channel until the pin drops into the
  rear seat. The spring sits in a counterbore in the lever.
- Its top end presses a **spring in series** with the switch: lever ->
  spring -> printed plunger -> switch stem. The press feels progressive
  (spring-loaded) until the spring reaches the switch's actuation force, then
  the switch clicks. The click point does not depend on print accuracy.
- 1.5 mm of spring preload (0.9 N) holds the lever firmly on its rest stop.
  Nominal (2 N switch): click after ~2.1 mm at the switch axis (~1.3 mm under
  the thumb dimple); a block on the backing stops the stroke at 3.8 mm.
  Neither the spring nor the switch is measured, so the grey plate also
  prints two longer plungers (+0.5 and +1.0 mm: 5.5 and 6.0 long against the
  normal 5.0). Each adds preload and brings the click earlier: a stiff switch
  (2.6 N) still clicks with the +0.5. Tests assert the click lands before the
  stop, the spring never goes solid, and even the +1.0 does not press the
  switch with the lever at rest.
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

Recessed 7.2 mm in the left side above the left holder. Its body is 18.9 x
11.8 under the frame (measured); the panel it clips into is 2.8 mm thick. The
clips sit on the body's long sides, with their barbs about 3 mm under the
frame (Mario, to the barb's middle), so the barb's slope starts nearer. The
hole is straight for 1.2 mm, then a 45 deg chamfer opens it to the panel's
back at 2.8 mm: wherever in that range the slope meets the edge, it pulls the
frame tight. (Rev E fixes a chamfer that was cut turned 90 deg; a test now
checks the roof and the panel round the hole.) Wired in the battery +
lead between the BAT pad and the + joint. The charger sits behind it too, so
**the rocker must be ON to charge**. Power on the left, talk on the right: easy
to tell apart by touch. A test asserts it stays below the surface.

## Battery protection: in the cells

The board has no cell protection (see MEASUREMENTS), and two unprotected cells
in parallel with no fuse turn a pinched wire into a ~20 A short. Mario's
answer (2026-10-10): use his protected 18650s, each with its own protection
circuit in the can, so no separate board. Protected cells are 2 to 4 mm
longer than bare ones: check that one sits flat in a holder. Put both cells
in at the same voltage (within 0.05 V). The
firmware still shuts down at a safe voltage via `BAT_ADC` (GPIO1).

## Wires between the halves: a ribbon

Six conductors of the rainbow ribbon from Mario's crimp kit join the faceplate
to the tub: two for +, two for -, one for talk, one for talk ground. They are
soldered at both ends, so the faceplate stays attached and opens like a book:
take out the four M3 screws, pull it straight out about 10 mm (to clear the
lip, the side fins and the lever-pin tooth), then swing it open to the left.

- **Faceplate end**: cut the board's own battery plug lead short and splice
  it to the ribbon (red to the two + conductors, black to the two - and the
  talk ground), heat-shrink each splice; the talk conductor goes to the GPIO7
  tail on the board's back (header pin 2).
- **Tub end**: a junction in the channel behind the speaker's magnet, where
  the tub's four wires (rocker +, battery -, the talk pair) splice to the
  ribbon, in heat-shrink.
- **Slack**: about 100 mm of ribbon, so the faceplate can lie open beside
  the tub. Closed, it lies loose in the channel between the holders. Mario
  (2026-10-11): the heavy parts must be held, the wires need no place.
- **Talk input**: GPIO7, header pin 2. The firmware still reads the board's
  KEY2 today; it needs one line to read GPIO7 instead (F-0011).

## Cables

Lengths with slack: lay a piece of solder wire along the route on the
printed parts, measure, and add about 20 mm. Loose is fine.

| Run | From | To | About | Held by |
| --- | --- | --- | --- | --- |
| Speaker | SPK plug (board back) | the speaker's solder tabs (upper right) | **26 mm** | short; turn the speaker so its tabs sit upper right |
| Ribbon (6 wires) | battery plug lead and GPIO7, on the board | the junction in the tub | 100 mm | loose, folded into the channel when closed |
| Rocker + | rocker tab | the junction | 80 mm | loose, across the bridge |
| Rocker in | rocker tab | + joint | 25 mm | the joint's pocket |
| Holder + leads (2) | top of each holder | + joint | trim to fit | the joint's pocket |
| Holder - leads (2) | bottom of each holder | - joint | trim to fit | the joint's pocket |
| Battery - | the - joint | the junction | 75 mm | two clips on the back wall |
| Talk pair | tact switch legs | the junction | 70 mm each | loose, across the bridge |

The board's battery socket is 1.25 mm pitch; the board's own battery plug
and lead came with it (Mario, 2026-10-11), so plug it in and splice its lead.

## Print guide

| Piece | Colour | Pose on the bed | Supports |
| --- | --- | --- | --- |
| Faceplate | grey | face down | none |
| Grille plug | black | face down | none |
| Tub | grey, swap to frosted at 36 mm | standing up, open front facing you | tree supports: under the roof, the switch cradle and its latch finger, the rocker mount and its recess, the holder pads, the joint shelves, the lever pin's notch at the front right, and thin strips under the outside bottom corners. Pull them out through the open front; the notch's and the recess's from outside (the notch has 0.4 mm of room round the tooth for what they leave) |
| Lever | orange | outer face down | none |
| Lever pin | grey | lying flat (three: two spares) | none |
| Plunger | grey | standing on its tip (three lengths) | none |
| Holder bridge | grey | front face down | none |

`huxcase/printability.py` measures what needs support in each pose and finds
any layer that starts in mid-air (`tests/test_printability.py` holds every
piece to that). The tub's close fits get no support at all: the plunger bore
and the lever pin's seat have 45 deg tops, the lever's pivot a 45 deg roof,
and the sliced project carries support blockers on the lever channel, the
rocker's panel hole and the USB pocket, whose tops then print as short
bridges. `huxcase.slice` reads the tub's G-code and fails if any support
touches those (`printability.support_keep_outs`); a tree branch may still
pass through the rocker hole or the lever slot, clear of their walls, and
comes out with the rest.

**With the AMS, one plate**: `out/print/HX1_one_plate_AMS.3mf` is a Bambu
Studio project, not sliced: open it, slice, print. It holds what is left after
the first prints (the faceplate, grille, a pin and a plunger, 2026-10-11): the
tub, the lever, and the bridge with the two longer plungers and two spare pins
as one grey object. It prints object by object, so the AMS swaps only between
objects and once inside the tub. Filaments: slot 1 grey, 2 black (not used
here), 3 orange, 4 frosted. The tub is two parts, grey below 36 mm and frosted
above, so it changes to frosted by itself, no pause. Only the tub gets
supports (tree, with its blockers). The bridge's screw hole is skinned over
on the plate's first layer (`hx1.bridge_skin`): its plate prints as a 17 mm
bridge, and with the hole open the bridge lines would end in the air (Bambu
Studio warns of a floating cantilever). About 2 h 50 min.

**Without the AMS**: `out/print/sliced/` has one sliced project per filament
colour for the P2S (0.4 nozzle, 0.20 mm Standard, Bambu PLA Basic, Textured
PEI plate; pick your plate in Bambu Studio if it differs):

| Project | Filament | About |
| --- | --- | --- |
| `1_grey_faceplate_bridge_pin_plunger` | grey | 41 min (faceplate, bridge, 3 pins, 3 plungers) |
| `2_black_grille` | black | 36 min |
| `3_orange_lever` | orange | 7 min |
| `4_tub_grey_then_frosted` | grey, then frosted | 2 h 28 min; tree supports; **pauses at 36.2 mm**: swap to frosted and resume |

`uv run python -m huxcase.slice` rebuilds all of them from out/print (after
`printability --stl`). It slices the AMS project too, to check it (one plate,
the P2S's start sequence, each object in its filament, the tub last and
frosted from 36.2 mm, support only on the tub and none touching a close
fit), that Bambu Studio raises no warnings for any project (its
`result.json`), and checks that only the per-colour tub pauses, right before its first
frosted layer. The presets are flattened first: Bambu Studio's command line
does not follow a system preset's full `inherits` chain or its `include`d
templates (the P2S keeps its start, end and filament-change G-code there),
and fills the gaps with generic defaults (until 2026-10-11 the sliced files
came out with `G28` as their start sequence and a 200 mm bed).

## Assembly

On the bench first:
1. Pull the tub's supports out (see the print guide). Cut the wires to the
   table above, with their slack.
2. Talk switch: solder its two wires to the leg pair that switches first
   (check with the meter). The rocker's leads are soldered later, in the case.

Tub, lying on its back:
3. Heat-set three M3 x 5 inserts: two in the top corner blocks, one in the
   boss on the back wall.
4. Rocker: snap it in from outside, with nothing on it. Its two tabs are
   then reached straight from the open front, about 20 mm in above the left
   holder: solder its two leads there (before the cells go in).
5. Talk switch: slide it into the cradle until it clicks. From outside, drop
   the normal plunger (5.0 mm, the shortest) into the tube, then the spring.
6. Lever: push a pin through it, slide it in from the front along its
   channel (press the spring's end down with a fingertip as it passes).
7. Holders: slide each straight in, riding over the ribs on the floor lugs.
   Join the + leads with the rocker's input lead, the - leads with the
   battery - lead; put both joints in heat-shrink and press them into their
   pockets. Snap the - lead into its two clips on the back wall.
8. Cells in (within 0.05 V of each other, + up). If one will not snap past
   its holder's wrap with the holder in its bay, slide that holder forward
   out of the bay on its leads, put the cell in, and slide it back.
9. Lay the + lead and the talk pair loose in the channel, then the bridge
   across, its tabs down beside the holders' + caps: M3 x 6 (push it through
   the thin printed skin over the bridge's screw hole first). The four tub
   wires end at the junction behind the speaker's magnet.
10. Rocker ON: meter 3.6 to 4.2 V between the + and - wire ends. Rocker OFF.

Faceplate:
11. Stand it on its top edge and heat-set two M3 x 5 inserts in the bottom
    bosses (their bores open toward the bottom edge). Then lay it face down
    and press the grille plug into its window from inside, flange inside
    (crush ribs hold it).
12. Cut the speaker lead to 26 mm, re-solder it, set the speaker on its tabs
    with the tabs upper right.
13. Board onto its standoffs (USB-C to the left), 3 x M2 x 8 with washers.
    Plug in the speaker and the battery plug.
14. With the faceplate lying open beside the tub: splice the ribbon to the
    battery plug lead and solder its talk wire to the GPIO7 tail; splice its
    other end to the tub's four wires at the junction. Heat-shrink every
    splice.

Close:
15. Rocker OFF. Fold the ribbon into the channel, swing the faceplate onto
    the tub (the lever pin's front end goes into the tooth) and fit the four
    M3 x 8: two in the face at the top, two up through the floor, snug.
16. Test: rocker ON, the lever clicks. If the click comes late or not at all
    before the lever stops, swap in the 5.5 mm plunger (then the 6.0). If
    the talk input reads pressed with the lever at rest, sand the plunger's
    spring end down 0.5 mm. On USB-C the charge light comes on.

## Parts list

Printed: faceplate, tub, grille plug, lever, lever pin (three: two spares),
plunger (three lengths), holder bridge (the build page lists each with its
volume).

Bought, all from Mario's inventory (photos 2026-10-10): the board and its
speaker; two protected 18650 cells in his two single holders; the 12 x 12
tact switch; the SciWil 0.4 x 4 x 15 spring; the KCD1-101 rocker; the board's
own battery plug and lead; Kadrick button heads 4 x M3 x 8, 1 x M3 x 6,
3 x M2 x 8 with flat washers; FFVRVSS 5 x M3 x 5 heat-set inserts; the
rainbow ribbon from the crimp kit (six conductors).

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
uv run python -m huxcase.printability --stl   # out/print/: every HX-1 piece as an STL in its print pose
uv run python -m huxcase.slice          # out/print/sliced/: ready-to-print Bambu Studio projects (P2S)
uv run python -m huxcase.coupon         # out/coupon_*.stl: fit coupon, lever-pin block
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

HX-1 rev E is a printable design: fit-checked, retention-tested and
printability-checked, with clearances the fit coupon confirmed on Mario's
printer. The other HX concepts are packaging sketches (form, not printable
parts). Nothing is left to measure for HX-1. Two things are settled on the
bench instead of by calipers: the click point (the spare plungers, step 16)
and a cell going into its holder in the bay (step 8).

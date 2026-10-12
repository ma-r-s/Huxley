# Walkie enclosure (HX series)

3D-printable case for the hardware client: the Waveshare ESP32-S3-AUDIO-Board,
its SPK-4020 speaker, two 18650s and a spring-loaded push-to-talk lever. Hold
to talk, release to hear the answer
([`../../../docs/concepts.md`](../../../docs/concepts.md)).

Status: **HX-1 is the build** (rev E, 2026-10-11). Every part is located and
held by printed features, screws or crush ribs; the one glued joint is the
tub's, between its two printed halves (Mario's call, 2026-10-11); every bought part has
a hard stop within 0.5 mm in every direction (tested); every printed piece is
one solid with a known print pose, and nothing printed without support
starts in mid-air (tested); the sliced tub has no support touching a close
fit (checked in its G-code). The
fit coupon is printed and folded in; the faceplate, grille, a pin and a
plunger are printed (2026-10-11). Two tubs printed in one piece with an AMS
change at 36 mm both parted at that layer, so the tub is now two pieces,
glued; next: the test corner, then the tub's plate
(`out/print/projects/4_tub_base_and_top.3mf`). Tracked as F-0010 in [`../docs/triage.md`](../docs/triage.md).
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

## The case: a faceplate, a glued tub, four screws

- **Faceplate** (grey, prints face down). The face, its 2 mm lip that seats in
  the tub's rim (`SNUG`), the board's three standoffs and three centring
  posts, the USB-C boss (it travels with the board), the two mic tubes, the
  charge-light tube, two screw pads at the top, two insert bosses at the
  bottom and the tooth that holds the lever pin's front end.
- **Tub**: everything else, roof included. Two pieces glued with CA: a grey
  base to 36 mm (`BACK_FROST_Z`) and a frosted top over it, so the LEDs glow
  through the top, the upper back and the upper sides. They meet in a lap
  joint like a lid's (`LAP_H`): the base keeps the inner half of its wall
  1.6 mm above the seam (then a 45 deg top, so the top's inner half prints
  over it without support), and the top's outer half comes down over it,
  0.15 mm clear on that face for the glue (`fits.GLUE`); the pocket also
  reaches 0.15 into the cavity, so the lever's backing, which the top
  carries down past the seam, clears the lip. The inside walls stay flush, so the
  holders slide past and the faceplate's lip seats across the seam. One piece
  with an AMS change at 36 mm was tried twice (2026-10-11): the change layer
  was the weak one, supports snapped there and the case parted there when
  flexed.
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

## Fits: everything goes together first time

Mario's rule (2026-10-10): every part must fit and stay put by design, no
glue. The one exception is his (2026-10-11): the tub's two halves, glued
with CA in their lap joint (above). All
clearances live in [`huxcase/fits.py`](huxcase/fits.py), per side, tuned as
starting points for his Bambu Lab P2S with PLA and a 0.4 mm nozzle:

| Interface | Clearance | Why |
| --- | --- | --- |
| Printed slides on printed, drawn in X/Y | 0.15 | `SLIDE`: free, no rattle (coupon: the peg slides into every vertical hole, .10 to .30) |
| Same, bore lying horizontal on the bed | 0.20 | `SLIDE_HBORE`: horizontal holes print oval (lever-pin block: the 2.4 pin turns freely in the .20 hole) |
| Removable but stays (faceplate lip) | 0.12 | `SNUG` |
| Glued lap, tub base to top | 0.15 | `GLUE`: slides together dry over the 70 x 35 ring, leaves CA a film |
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

The top prints in Mario's transparent PETG (2026-10-11; PETG is amorphous,
so it gets clearer than PLA), for clarity: he asked for it "as even and as
transparent as possible", not glass. From Bambu Lab's translucent PLA/PETG
guide on its wiki (its PETG values read off its screenshots), on Bambu's
own "PETG Translucent" preset for the P2S, and what carries over to a
2.4 mm wall on a 0.4 nozzle (`huxcase/slice.py`, `CLARITY_*`):

- Dry the filament first: translucent PETG is very hygroscopic, and damp
  filament leaves bubbles that scatter light.
- Solid: one wall, then 100 % infill with every layer's lines the same way
  (Bambu's "Aligned Rectilinear", 0 deg; the guide's "Rectilinear" turns
  90 deg a layer, and the stack showed as a crosshatch through the clear
  PETG of Mario's test corner), no top or bottom shells, so there is no sparse grid
  to show in the glow and fewer boundaries between lines. Infill lines 0.5 wide; the walls keep 0.42 so
  the fine features still print.
- Hot and slow so the lines fuse: 240 C, the top of the 230 to 240 C that
  Mario's spool (ElitePETG Transparent) asks for at 50 to 100 mm/s; the
  guide's 270 strung badly on the first clear top (2026-10-11, with the AMS
  at 48 % humidity: dry it first). Flow 1.01
  (preset 0.95), about 40 mm/s (a cap on the frosted filament's flow,
  4 mm3/s). The guide says 20 mm/s; that roughly doubles the top's time.
- No cooling: part fan and side fan off, as the guide says; overhangs and
  bridges still get the preset's 90 %.
- Even layers, 0.2 as the fits are tuned. The guide's 0.1 would double the
  time again.
- After printing, sanding the outside evens the frost further; a clear coat
  would go glassier, which is not the aim.

Base and top share a plate, so these settings sit where only the top
feels them: the heat, flow, fan and speed on the frosted filament, which
only the top uses. The solid walls are the plate's, so the base prints
solid too (stronger, a little slower). Not as the objects' own settings:
Bambu Studio did not apply those for Mario.

Print the test corner first (`5_test_corner`, about 20 minutes): both
halves' back right corner round the seam, the same way and with the same
settings. Push them together dry (they should slide on and sit flush), and
hold the frosted one to a light.

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
- **Talk input**: GPIO7, header pin 2. The firmware reads it (or the
  board's KEY2) since F-0014 on the `firmware-talk-gpio7` branch: built and
  tested off the board, ready to flash (`clients/firmware/build/flash-talk-gpio7/FLASH.txt`
  in the main checkout).

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
| Tub base | grey | standing on its floor, open front facing you | tree supports: thin strips under the outside bottom corners (their lowest millimetre leaves the bed almost flat; sand what they leave) and the - joint's shelf |
| Tub top | frosted PETG (clarity settings) or any PLA | on its back, the open front up | tree support from the plate outside the case: under the wall in front of the lever's slot and in the rocker's recess, and the first millimetre of the back's rounded edges; none at the lever's pivot, the slot's back face or stop, in the plunger's tube, the pin seat, at the rocker's clips, in the switch's cradle, the glue pocket or on the USB plug's path (checked in the G-code). Pull it out sideways from outside. No brim |
| Lever | orange | outer face down | none |
| Lever pin | grey | lying flat (three: two spares) | none |
| Plunger | grey | standing on its tip (three lengths) | none |
| Holder bridge | grey | front face down | none |

`huxcase/printability.py` measures what needs support in each pose and finds
any layer that starts in mid-air (`tests/test_printability.py` holds every
piece to that).

**Why the top prints on its back** (Mario, 2026-10-11). Standing on its seam
it needed support under the whole roof, its foot was the lap's 1.1 mm edge,
and the trees grew from outside in through the lever's slot and on through
the plunger's tube to reach the switch cradle: clear of their walls, but in
PETG they fused to them. On its back (`hx1.TOP_PRINT_UP`, the front up) it
stands on its whole back wall, the inserts' holes and the lever pin's seat
print upright and round, the roof's corners come out smooth, and it prints
in about two thirds of the time.

What that took. The plunger's bore points to the front (`_bore_x_front`).
Ledges that would hang over the back wall reach down to it (the lever's
backing, the stop block, the cradle and its web, at 45 deg over the + lead,
the tube's underside) or slope at 45 deg from what holds them (the insert
blocks: the right one from the side wall, the left one along the rocker
hole's own taper, out of the rocker's mount). The 1 mm strip between the USB-C opening and the seam is gone, so
the opening reaches the seam; the faceplate's face closes it in front. The
backing's end at the seam rises 45 deg off the lap's pocket instead of
roofing it. One roof over a close fit has nothing under it, inside or out
(a 45 deg slope there would start in mid-air too), so it prints on support
that grows from the plate outside the case: the wall in front of the
lever's slot, with 0.4 mm more room there where the slot is
(`SLOT_RELIEF`) so what it leaves stays off the lever. The rocker hole's
front edge prints as a bare 12 mm bridge across its panel, with 0.3 mm
round the body for its sag; the clips catch on the long sides, which stay
clean. That, the rocker's recess and the first millimetre of the back's
rounded edge are all the support it gets. What was there only for standing is gone: the
45 deg roofs over the lever's pivot, the 0.4 mm over its top end, the pin
seat's and the tube's 45 deg tops, the roof's 0.3 mm over the lip, and the
one-piece standing tub.

One part bends across its layers on its back: the latch finger that clicks
over the talk switch runs front to back, so it stands up in the print, and
there is no room to turn it (a flat finger would hang in mid-air). It is
0.8 mm thick (the cradle's wall is cut down over it) with a 0.35 mm catch,
0.34 % strain at full catch measured from its root to the lip's catching
face (11.15 mm), about a third of what PLA's layer bond takes (tested,
thickness probed on the printed shape).

`huxcase.slice` reads the top's G-code and fails on any support at all,
branches passing through included, at the lever's pivot, in the tooth's
notch, on the slot's back face or the lever's stop, in the plunger's tube
to its mouth, the pin seat, where the rocker's clips catch, in the switch's
cradle, the glue pocket or on the USB plug's path
(`printability.support_keep_outs`, `support_keep_out_rounds`).

Support blockers are gone (2026-10-11): with them on the lever slot, the
rocker hole and the USB pocket, those overhangs printed bare and failed on
Mario's first tub.

**The projects**, in `out/print/projects/`: Bambu Studio projects for the
P2S (0.4 nozzle, 0.20 mm Standard, Bambu PLA Basic, and Bambu PETG
Translucent for the frosted slot; Textured PEI plate, pick yours if it
differs), not sliced: File > Open Project, slice,
print. The same sliced are in `out/print/sliced/`.

| Project | Filament | About |
| --- | --- | --- |
| `1_grey_faceplate_bridge_pin_plunger` | grey | 38 min (faceplate, bridge, 3 pins, 3 plungers) |
| `2_black_grille` | black | 30 min |
| `3_orange_lever` | orange | 6 min |
| `4_tub_base_and_top` | AMS: slot 1 white PLA, slot 4 frosted PETG | 3 h 16 min; by object: the frosted top, then the base |
| `5_test_corner` | AMS: slot 1 white PLA, slot 4 frosted PETG | 29 min; both halves' corner at the seam: print it first |
| `7_tub_top_pla` | one PLA (red set) | 1 h (on its back); the top alone, standard settings, for a base already printed |

The tub's plate prints object by object, so the filament changes once,
between the pieces, never inside one. The frosted top goes first, so its
first layers come out clean (no grey left in the nozzle). The pieces sit
94 mm apart: the toolhead (72 mm round) and the gantry (32.5 mm over the
nozzle, lower than either piece) never pass over a finished one, which
Bambu Studio checks. Don't press Arrange, it may undo that. Organic tree
supports are the plate's (Bambu Studio did not apply an object's own
settings for Mario). No prime tower: by object, the AMS purges into the
chute. No brim: the top lies on its whole back wall. Clean the plate first (dish soap, or isopropyl alcohol). When
sending, check the AMS mapping (1 white PLA, which Mario uses for the base instead of grey; 4 frosted PETG) and that slot 4 is set
to PETG on the printer, its spool dried. Supports sit 0.25 off the part
(0.2 is standard), as PETG welds to them closer. The bed runs 70 C for the
PETG top, then 55 C for the PLA base (Bambu sets it at the change).

On plate 1 the bridge's screw hole is skinned over on its first 0.5 mm
(`hx1.bridge_skin`), or its bridge lines would end in the air (Bambu Studio
warns of a floating cantilever).

`uv run python -m huxcase.slice` rebuilds every project from out/print
(after `printability --stl`) and checks each sliced one: one plate, the
P2S's start sequence, no pause, no Bambu Studio warning (its
`result.json`), on the tub's plate the top then the base, each in its
filament, and no support touching the top's close fits. The presets are
flattened first: Bambu Studio's command line does not follow a system
preset's full `inherits` chain or its `include`d templates (the P2S keeps
its start, end and filament-change G-code there), and fills the gaps with
generic defaults (until 2026-10-11 the sliced files came out with `G28` as
their start sequence and a 200 mm bed). And each project lists what it changes
(`different_settings_to_system`): Bambu Studio's window loads the named
system presets and applies only the settings listed there. Until
2026-10-11 the list was empty, so Mario's Bambu Studio showed and printed
the defaults (no supports, a prime tower, 15 % grid infill in the clear
test corner) while every check of the command line's slice passed;
`slice.gui_settings` now checks the list against what the slice used.

**Variable layer height** (Mario asked, 2026-10-11): not in the projects.
Bambu Studio's layer height file, tried, landed on the wrong object of the
two-object plate. The base's bottom corners are where it would help: their
lowest ~1 mm needs support whatever the layers, and 0.08 mm layers over the
bottom 3 mm only make that strip smaller and finer. In Bambu Studio: select
the base, Variable layer height, raise the bottom 3 mm's resolution. Keep
the rest at 0.2: the fits are tuned there.

## Assembly

On the bench first:
1. Pull both tub pieces' supports out (see the print guide). Push the top
   onto the base dry first: it should slide down and sit flush. Then glue: a thin line of CA round the base's
   lip, very little (its fumes leave a white haze on the frosted part;
   low-bloom CA if you have it, and air moving across; scuff the lip and
   the inside of the top's skirt with fine sandpaper first, CA grips PETG
   better on a rough face), then the top
   straight down over it, open fronts flush, held 30 s. Cut the wires to
   the table above, with their slack.
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
9. The bridge across, its tabs down beside the holders' + caps, with the
   rocker's free + lead and the talk pair held up out of its way: M3 x 6
   (it pokes through the thin printed skin over the bridge's screw hole).
   Then feed the + lead and the talk pair down through the bridge's U, between
   the holders, to the junction. Laid first, they get pinched by its plate. The four tub
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

Printed: faceplate, tub (base and top, glued), grille plug, lever, lever pin (three: two spares),
plunger (three lengths), holder bridge (the build page lists each with its
volume).

Bought, all from Mario's inventory (photos 2026-10-10): the board and its
speaker; two protected 18650 cells in his two single holders; the 12 x 12
tact switch; the SciWil 0.4 x 4 x 15 spring; the KCD1-101 rocker; the board's
own battery plug and lead; Kadrick button heads 4 x M3 x 8, 1 x M3 x 6,
3 x M2 x 8 with flat washers; FFVRVSS 5 x M3 x 5 heat-set inserts; the
rainbow ribbon from the crimp kit (six conductors).

Tools and consumables assumed: CA glue (low-bloom is best), soldering iron and solder, the insert tool,
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
uv run python -m huxcase.slice          # out/print/projects/: Bambu Studio projects (P2S); sliced/: the same, checked
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

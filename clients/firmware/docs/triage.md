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

**Open, for Mario (2026-10-10)**: the review's last plug problems (the
shuttle can leave its cage when the case opens; the 0.8 mm-short depth
sits at the housing's lock hook) have one robust fix from a design agent:
seat the plug fully against two small springs behind the housing, on a
follower plate, with a frame screwed over the shuttle's ears (M2 x 6 into
M2 inserts), and the header's tails bent under its plate; the springs are
chosen after weighing the plug's mating force on the coupon. Alternative:
no plug, a ribbon folded into a pocket at the seam (fewer parts, nothing
to align, but the faceplate stays tethered and opens like a book).
Recommended: the ribbon. **Mario chose the ribbon (2026-10-11)**: the faceplate
opens like a book about its left front edge; a ribbon service loop lies in a
slot along the left wall. Then simpler still (Mario, 2026-10-11): the
heavy parts must be held, the wires need no place. So the plug, yoke, shuttle
and cage are gone; six ribbon conductors are spliced from the board's battery
plug lead and GPIO7 to the tub's wires at a junction behind the speaker's
magnet, with about 100 mm of slack lying loose. Done meanwhile: the joint pockets are 14 and 11 mm
long (a lead a few mm off moves its joint along), the rocker goes in empty
and its tabs are soldered in place, and wires are measured on the print.

**Round 5, final review before the tub (2026-10-11)**: a fresh critic found
the rev E tub not ready. Fixed:

- Blocker: the rocker panel's chamfer was cut turned 90 deg (a slit through
  the roof, the switch loose). Swapped; a test probes the roof and the panel.
- The sliced tub had support inside close fits (plunger bore, lever pivot and
  channel, rocker hole, USB pocket). Now: 45 deg tops on the plunger bore and
  the lever pin's seat, a 45 deg roof over the knuckle, support blockers in
  the tub's project for the channel, the rocker hole and the USB pocket, and
  `huxcase.slice` fails if any support touches those
  (`printability.support_keep_outs`; tree branches may pass through clear of
  the walls). The tooth notch keeps its support (open to the front and side):
  0.4 room round the tooth for what it leaves. 0.3 roof relief over the lip.
- The bridge's wire clips printed in mid-air: deleted (wires loose, Mario's
  rule). `printability.islands()` now finds any layer that starts on nothing;
  every unsupported piece is tested free of them.
- Holders stopped sideways at their full-height end caps (bridge tabs up to
  the + caps, ribbed floor lugs at the - caps), so their unmeasured wall
  height no longer matters; tested on the caps alone.
- Click margin: spare plungers 0.5 and 1.0 mm longer instead of a washer that
  is not in the kit; `click_travel` now counts the stem's 0.25; plunger tip
  chamfered; three lever pins. Docs: README, MEASUREMENTS, build page.
  Not changed, on purpose: the faceplate (Mario may have printed it; its thin
  engraved labels may fill in, cosmetic only).

**Printing (2026-10-11)**: Mario printed the faceplate, grille, a pin and a
plunger from the STLs (all still current; the plunger has since gained a
0.4 tip chamfer, not needed on his). Found: every sliced project until then
was built on generic defaults (start sequence `G28`, a 200 mm bed, default
infill and fans): the Bambu CLI does not resolve a system preset's whole
`inherits` chain or its `include`d G-code templates. Fixed by flattening
the presets in `huxcase/slice.py`. The rest now goes on one AMS plate,
`HX1_one_plate_AMS.3mf` (unsliced, print by object, grey / black / orange /
frosted in slots 1 to 4, the tub as a grey and a frosted part), checked by
slicing it here.

**First tub print (2026-10-11)**: failed where the support blockers were
(the lever slot, the rocker hole, the USB pocket): those overhangs printed
in mid-air (read back from the G-code: overhang walls and bridges at z 54,
87 to 100, 99 to 101 and 79 inside the blocker volumes). The checks only
proved no support touched the close fits, never that the bare overhangs
print. Now: no blockers, every bridge supported (the preset default); a
second 45 deg roof over the lever's web (its flat strip drew support where
the lever swings); the channel top raised 0.4 over the lever's end; the
rocker hole 0.3 above and below the body (its top edge still prints as a
bare bridge: Bambu sees nothing to support). Checked: support touches
none of the bore, pivot, pin seat, rocker hole or USB plug path; no
warnings. New tub-only AMS project `HX1_tub_AMS.3mf`.
Lesson: a slicer check that support stays away from X must come with a
check that what is left bare is printable; prefer geometry (45 deg roofs,
relief) over blockers.

**Tub in two pieces, glued (2026-10-11)**: two more one-piece tubs (AMS
change at 36 mm) failed at the change layer: the supports snapped there, and
what printed parted there when flexed, though it felt solid. Mario proposed
CA glue (relaxing "no glue" for this joint only) and asked for the frosted
part as clear and even as possible, not glass; then for both pieces on one
plate, printed by object, and whether to use adaptive layer height.
Built: a 45 deg lap joint at 36 mm (`hx1.LAP_H`, 0.05 glue gap a side, the
inside walls flush); the top stands on its seam face with an outside brim
(68 mm tall on a 1.15 mm strip, auto brim gave none); a clarity profile from
Bambu's translucent filament guide (read on its wiki): one wall, 100 %
rectilinear, no shells, 230 C, flow 1.01, low fan, ~40 mm/s, carried by the
frosted filament so the base on the same plate prints at full speed; a
15 min swatch cut from the top's corner. One plate `4_tub_base_and_top`,
top first (clean frosted start), 95 mm apart so the toolhead and the
gantry (32.5 mm) never pass over a finished piece (Bambu Studio validates
it). Checked: object order, each in its filament, the clarity settings on
the frosted filament only (they first landed on black: per-variant lists,
`filament_self_index`), real feedrates (top 44 to 53 mm/s, base full), no
support on the top's close fits, no warnings; all STLs watertight (the
lap's tapered corners collapsed to a point: an open mesh). Not done:
variable layer heights. The rough lower edges are the 8 mm bottom
corners, whose lowest ~1 mm needs support whatever the layers; Bambu
Studio's layer height file landed on the wrong object of the two-object
plate in testing, so it is left to the GUI (README).
Lessons: a filament change mid-object is a weak layer, so split the part
instead; check where per-filament settings land, not just that they are
set; a profile file the slicer accepts silently is not proof it applied:
read the layers back.

**Split tub, review fixes (2026-10-11)**: a cold reviewer found (1) the
pocket's inner edge was the inside surface exactly, so the lever's backing,
which the top carries down past the seam, sat flush on the lip's inner face
along 24 mm: zero clearance, it would bind or stop the top seating before
the CA grabbed; (2) "outer only" brim still ran inside the open U, along the
skirt's edge that slides over the lip; (3) the glue gap was 0.1 on its one
mating face (docs said 0.05 a side), under SNUG. Fixed: the pocket reaches
LAP_GAP into the cavity; LAP_GAP = `fits.GLUE` 0.15; no brim; a test that
the top keeps LAP_GAP off the lip (`test_tub_top_clears_the_lip`, 0.15 now,
0 before); a 19 min test corner of both halves at the seam (backing
included) replaces the roof swatch, to push together dry before the long
print. Also: zig-zag infill alternates 0/90 per layer (docs said one
direction), and the README's per-piece support lists were wrong. Mario
asked for the top on its back (less support): checked, it would turn the
lever channel's front wall into a 45 mm ceiling over the lever's slot;
kept standing. Prime tower off (it showed in his GUI), organic supports.
Lesson: a "no overlap" test passes faces that touch; a mating joint needs a
minimum-distance test.

**Frosted top in PETG (2026-10-11)**: Mario's frosted spool is transparent
PETG, not PLA. Slot 4 now loads Bambu's "PETG Translucent @BBL P2S" preset,
with the guide's PETG values (read off its screenshots): 270 C, flow 1.01,
part and side fans off (overhangs and bridges keep 90 %); supports 0.25 off
the part (PETG welds at 0.2); bed 70 C for the top, 55 C once the base
starts. check() asserts PETG on the frosted slot. The fits are PLA-tuned:
the test corner (lap, backing, the rear pin seat's start) is the check.

**Bambu Studio ignored the projects' settings (2026-10-11)**: opening
the tub project in Mario's Bambu Studio (driven from here) showed supports
off and the prime tower on; his saved copy held only the two settings he
had changed himself, all else the presets' defaults (2 walls, 15 % grid
infill, auto brim, Generic PETG 255 C). Cause: the GUI applies only the
keys a project lists in `different_settings_to_system`, and ours listed
none; the command line uses the whole config, so every check passed. It
explains the per-object supports that "did not apply" (F-0010 earlier) and
the grid Mario saw inside the clear test corner. Fixed: `_changed` writes
the list, `gui_settings` checks it; verified in his GUI. Lesson: check a
deliverable in the program the user opens it with, not only in its CLI.

**Clear top strung; tub in one PLA piece (2026-10-11)**: the first clear
top (sent from his Bambu Studio, settings verified there) strung badly.
His spool's tag (ElitePETG Transparent): 230-240 C at 50-100 mm/s, bed
60-70; we ran the guide's 270 at ~40 mm/s, with the AMS at 48 % humidity.
PETG now 240; dry the spool before the next clear try. To have one soon he
asked for PLA: `6_tub_one_piece_pla`, the whole tub as one print (no
filament change, so no seam, no glue), standard settings, 2 h 28 min, any
PLA tray (he picked orange, then another colour he is loading). Lesson:
read the spool's own range before taking a guide's temperature.

**Top prints on its back (2026-10-11)**: Mario, removing PETG supports from
"inside every hole", did not believe standing was best. Measured: standing,
the trees grew from outside through the lever's slot and the plunger's tube
to the switch cradle (my check only counted contact layers, so it passed);
on its back, support lands in the slot and the tube unless the top changes.
He asked for it done on its back. Done: standing-only features out (knuckle
roofs, channel and roof relief, pin seat's and tube's +z points), the bore's
point toward the front, ledges filled to the back wall or sloped from what
holds them, the USB-C opening's 1 mm strip to the seam removed, the latch
finger thinner (it now bends across its layers, 0.37 % strain, tested). Two
roofs have nothing under them in or out and print on support from outside:
the wall in front of the lever's slot (0.4 relief) and the rocker hole's
front edge (0.3). The slice check now counts any support, not just contact,
inside zones drawn round working surfaces (pivot as a round, tooth notch,
slot back face, stop face, bore to its mouth, pin seat, rocker clips, USB
plug). All plates pass; red top 1 h 1 min (1 h 45 standing), tub plate
3 h 17 (4 h 10). The base is unchanged (checked against Mario's print).
Review fixes (cold reviewer, same day): the left insert's wall had been
cut to 0.6 mm by a sign error (now cut along the rocker hole's taper,
2 mm wall); the slot relief cut a slit through solid wall (now only where
the slot is); the latch was really 1.2 mm thick and its strain 0.58 % (the
cradle wall is now cut to 0.8 over it, catch 0.35, 0.34 % measured to the
catching face, thickness probed in a test); the backing roofed the glue
pocket (now rises 45 deg off it); keep-outs extended (bore to its mouth,
rocker clip chamfers, slot back face down to the pivot, switch cradle,
glue pocket); the rocker hole's front edge prints as a bare 12 mm bridge,
not on support. A seam cut twice reached the base's lip: the base's exact
volume is now pinned in a test.
Lessons: (1) a 45 deg slope only prints if it grows out of supported
material: my first slot chamfer and bevels started as knife edges in mid-air
and drew trees; (2) "no support touching" is not "no support there": count
the bodies; (3) measure a claim the user doubts before defending it.

**Clear top, second try (2026-10-12)**: Mario has the red PLA top on; he
wants the clear one eventually and asked for it printed overnight.
`8_tub_top_clear`: the top alone on its back, the clarity settings, PETG
only. Reddit (his pick for research) is blocked to both the browser and
web search, so the settings come from forum and makers' guides for clear
PETG: 20 to 30 mm/s, 240 to 270 C, fans off, and above all a dry spool.
Now 25 mm/s (2.5 mm3/s cap) at 245 C (the preset; a touch over the tag's
240 for the slower speed); 3 h 52 min, 41 g, zones clean, checked in his
Bambu Studio too. The spool dries in the AMS first: 45 C for 12 h (Bambu
caps PETG at 45 with PLA spools inside; Mario kept them in), humidity 53 %
at the start, 38 % after 2 h, 37 % at 06:42; its countdown lost an hour
overnight (ends about 13:41, not 12:40). Printing when the drying ends.
The slower clear speed also moves the tub plate to 4 h 35 min (the test
corner to 31 min). A re-slice ran over four hours: `in_keep_out` rebuilt the
keep-out boxes, one a boolean (the bore's mouth), for every support sample;
cached, the whole slice takes 19 s. Lesson: a check that runs per sample
must not build geometry.

**TPU bumper (2026-10-12)**: Mario's mother's idea, a TPU case against falls
with a loop for the neck or the wrist (`huxcase/bumper.py`). A cold critic
reviewed v0 (a 3 mm band, a rim round the lever, printed on its back)
before it was built. Its findings:
- the rim and a flush band would sink the lever below its surroundings, so
  presses miss the 2.1 mm click and TPU can drag on a lever that springs
  back on 0.9 N;
- on its back the lever's window was a 58 mm bridge;
- the lips rolled off the case's round edge under a jerk;
- the loop at x -10 hung 8 deg tilted.
v1 took its fixes: it prints on its front, the lever sits in a U open to
the back, the front lip is the seat and the back lip the way in, and the
loop sits near the centre of mass. Then Mario asked for corner protection
rather than width: v2 has 1.5 mm sides and 4.2 mm corners (outer R3 over
the case's R8), 73 x 107, about 30 g. No supports, 2 h 14 min on Bambu's
TPU 95A preset (Mario's spool is Overture TPU 95A, standard flow); a 37 min
front ring (8 mm) tests the fit. Mario asked about forces and infill: at
277 g a 1.2 m drop is about 3.3 J; bare PLA stops in about 0.25 mm (13 kN),
a 4.2 mm TPU corner squashes about 2 mm (about 4 kN). Measured how far the
TPU stands past the case per drop direction: 5 mm at the vertices (I first
guessed they were the weak spot; measuring showed the case's doubly rounded
corners leave the most TPU there), 4.2 at the corners, 2.8 along the face
edges, 1.5 on the flats. Solid, no infill: the sections are too thin for
it, and a softer corner bottoms out sooner. 2.5 mm lips gained under 1 mm
at the edges for 2 mm more depth: kept 1.5. A risk it can't fix: worn on the chest, an arm or a hug can
press the lever; the server's silent-press gate (T1.35) drops presses with
no speech.

**Decisions waiting on Mario**: (resolved: HX-1 is the build, speaker at the
bottom is fine, holders whole, no glue but the tub's halves, plastic pivot, protected cells
instead of a protection board, the ribbon and loose wires, the board's
battery plug is there); dock yes/no; volume buttons yes/no. Nothing left to
measure: the click point and a cell going into its holder in the bay are
bench checks in the README's assembly (steps 8 and 16).

**Firmware impact**: PTT moves from the on-board `K2` (via TCA9555) to an
external switch on GPIO7 (header pin 2; input, pull-up, low = talk; the LCD
is unused).
`hux_button` in F-0003 should read a direct GPIO; keep `K2` as the bench
fallback.

**Docs touched (Gate 4)**: `enclosure/README.md`, `enclosure/MEASUREMENTS.md`,
`firmware/README.md` (enclosure pointer), root `CLAUDE.md` (repo layout).

---

## Queued (next up, not started)

### F-0011 — Talk button on GPIO7 · built 2026-10-11, as F-0014 on branch `firmware-talk-gpio7`

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
**Done (2026-10-11)** on branch `firmware-talk-gpio7` as F-0014 (the firmware
tracker had already used F-0011): GPIO7 or K2, plus `set_volume`,
`input_mode` and `audio_clear`, which the firmware had been ignoring. See
the firmware tracker for the evidence and the bench test left.

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

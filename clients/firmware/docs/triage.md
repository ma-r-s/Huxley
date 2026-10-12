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

**TPU bumper (2026-10-12)**: a TPU case against falls
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
  at the edges for 2 mm more depth: kept 1.5. A risk it can't fix: worn on
  the chest, an arm or a hug can press the lever; the server's silent-press
  gate (T1.35) drops presses with no speech.

**Button clearance (2026-10-12)**: the firmware session's W7
(docs/remote-operation.md) asked for a paperclip pinhole to RESET. It was
designed (the edge buttons are side switches, so the bore ran in the
board's plane through the left wall) and committed (23c41f5f), then
dropped the same day: Mario wants no holes in the case and nothing else to
print. Kept from it: the tub top's corner block touched BOOT's plunger
(0.00 mm; cut round the board's outline, buttons included), and BOOT held
at a reset boots download mode (F-0020). Every edge button now has 0.6 mm
(`hx1.KEY_CLEAR`), inside the clear top he prints anyway. Lesson:
clearance to a part that changes how the device boots is a tested number,
not whatever the envelope left.

**Final colours, merged, on the website (2026-10-12)**: Mario's final
colours are a white faceplate, a red base, the clear PETG top, the black
grille and the orange lever. The black TPU case and tactile front dots are
designed elsewhere. The build page shows them (site.SITE_KIND). The print
set follows: plate 1 white, the tub plate expects A1 clear PETG and A4 red,
and a new `11_tub_base_red` (34 min) covers a base alone. The enclosure
branch is merged into main and pushed (a791d4f0) together with the
website's /hx-1 page, which shows HX-1 in 3D (`huxcase.site --web`). The
TPU front test ring fit "really, really good"; the full bumper is next.

**Face art (2026-10-12)**: Mario wanted something meaningful on the face,
first as braille or morse dots glued on through a printed stencil. Four
rounds of 2D mockups were rejected before one stuck:
- small items beside the mic rings: crowded, badly placed;
- dot fields filling the face: pretty, but meaningless;
- "hola" sideways down the centre line: something off.

What worked: "hola" in braille at about 2x, so from afar it reads as
scattered dots. The layout is laid on lines the face already has:
- the grille's left edge starts the word and a small black HX-1 badge;
- the mic rings' axis ends it, the "a" a single dot between the rings;
- the word sits midway between the rings, and the badge on the lower ring's row.

A cold critic ranked six layouts and set the last 0.3 mm (round shapes past
a straight edge, the "a" on the axis). Dots alternate two colours by letter;
Mario asked for "more colourful, less Halloween", and orange + sky blue is my
pick of six pairs. Built in `huxcase/face_art.py`, all glued with CA:
- ten domed dots, 3.2 x 1.0 mm;
- the badge, 17 x 6.8 mm, a 1 mm black pill with its letters raised 0.6
  in orange, so the filament changes once at one height (Mario: no mixing
  within a layer); one object of two parts (`slice.Plate.parts`);
- a stencil, a 1 mm plate on the face with a lip over the top and sides.

All of it is on one plate printed by object (`12_face_art`, Mario's ask):
the badge, the orange dots, the blue dots, then the stencil in blue. That is
two filament changes, purged into the chute, since by object has no prime
tower. `tests/test_face_art.py` covers the parts. A cold review before
printing found:
- the stencil's 0.4 mm relief was deeper than the dots' foot, so the holes
  held a dot only by its dome (about 0.65 mm of play): the relief is now 0.2
  and the foot 0.36, and a test pushes each dot past its clearance;
- Menlo letters at 4.2 mm would merge on the classic wall generator: they
  are now 5.0 mm, spaced 0.4, on Arachne.

Bambu Studio orders a by-object plate by its build list, and keeps every
object 72 mm from the others even when low: `slice.layout` sets both.
The faceplate loses its engraved name, which printed face down on the
textured plate had come out invisible. Lessons:
- when Mario rejects placement, keep the meaning he asked for and fix only
  the placement;
- lay decoration on the device's own lines, not the face's centre, when
  the fixed features are off-centre;
- a 2-filament plate needs its flush matrix sized 2 x 2 (the presets carry
  4 x 4 and the CLI refuses).

It is printed and glued (2026-10-12), and the build page and the website's
/hx-1 model show it (`site.face_art_parts`).

**Clear top retuned against strings (2026-10-12)**: before printing it,
Mario pasted six Reddit threads on clear PETG and asked for the settings
with the best chance of succeeding, no strings. Read against the slice:
the threads' "Aligned" infill holds at 100 % here (every layer 0 deg in the
G-code; one commenter says Bambu alternates it), but three settings were
riskier than Bambu's own preset, and one came from it: flow 1.01 against
its calibrated 0.95 (6 % over, solid in every layer), part fan off (its
10/30), and retraction 0.3 (the preset's, against the P2S's 0.8; about
3000 travels). Now 240 C (the spool's top; 245 first layer), flow 0.98,
the preset's fan (10 % in this part, off for three layers, 90 % on
overhangs), retraction 0.8, and "Avoid crossing walls" on. 3 h 55 min,
zones clean. Mario's aim is an even glow, not glass, so a little clarity
went for success. Lesson: read the base preset before overriding it; the
override can be the risky value, and the preset can carry one too.

**The /hx-1 scroll story (2026-10-12)**: Mario pointed at play.date and asked
for something as good. The top of /hx-1 is now one pinned 3D HX-1 that the
page scrolls past in four beats: the hero turning slowly (a drag turns it)
with a "hola." bubble on the dots; the face close up, the braille letters
lighting in reading order; the right side, the lever pressing in and the
exchange it makes appearing beside it; the device apart, its main parts
named (`web/landing/hx1/stage.ts`, `HX1Story.tsx`; the old viewer is gone).
`site.py --web` adds what that needs: each dot's letter, the lever's pivot
and travel, the spring. Phones and reduced motion cut between still poses.
Mario saw the spring run through the orange lever when it pressed: the
spring now shortens with the lever. Lessons:
- words that sit beside a moving model must stay put and only fade, in
  windows computed from where the colour edges sweep, or they cross the
  model or an edge at some window size;
- anything named on the model (part labels) waits for the model's own eased
  state, not the scroll position, or a jump names a closed case.

**The build guide on the website (2026-10-12)**: Mario asked whether the
build instructions were on the site ("if someone wants to make it, I don't
want to lose it"). They were not: the build page lived in `out/site`
(gitignored) and a private artifact. It is now at /hx-1/build, written by
`site.py --web` from the same template (loading the website's GLB, its
mark linking back), linked from the /hx-1 hero ("Build one") and its "Made
to be made" section. Its copy no longer speaks to one maker's printer
(tested). It is English only; the ES and FR links say so. Mario then
asked for the print files on it: a "Print it" section offers the Bambu
Studio projects (11 plates, 2.2 MB) and every STL with the fit coupon
(4.1 MB), each zip with PRINT.txt; both were scanned for personal
metadata before publishing (none: Bambu leaves Designer empty).

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

### F-0014: Talk on GPIO7; act on `set_volume`, `input_mode`, `audio_clear` · bench-tested 2026-10-11 (K2 and a call left for a hand)

**Problem**: the HX-1 case's talk lever closes a switch on header pin 2
(GPIO7) to GND, but the firmware only read the board's K2. And against
today's server three messages were logged and ignored: `set_volume`
("sube el volumen" did nothing), `input_mode` (a Telegram call heard
silence from the device: the mic only ran while talk was held) and
`audio_clear` (an audiobook seek or an interrupt played the old tail
first). Also: a failed I2C read of K2 counted as a press (the expander
read returned "low" on error, and the button is active-low).
**Solution** (branch `firmware-talk-gpio7`): talk = GPIO7 (pull-up) OR
K2, through a host-tested debounce (`hux_debounce`); `hux_board_tca_read_level`
reports errors, an unreadable K2 is not pressed. `hux_proto` parses
`set_volume.level`, `input_mode.mode`, `state.value`,
`hello.session_ready`, and `audio_clear`. `audio_clear` is handled on
the data plane in order with `audio`; the speaker ring is reset by its
reader (`spk_task`) on request. The mic runs while talk is held or
while `input_mode` is `skill_continuous`. A release whose press was
ignored no longer sends a stray `ptt_stop`; a WS drop stops the mic.
**Evidence so far**: `idf.py build` clean (no warnings, 1.0 MB, 52 % of
the app partition free); host tests green (hux_proto 92 assertions incl.
the new payloads, hux_ws_frag, hux_debounce); server contract tests
green both ways (new: server frames vs what the firmware parses,
`"type"` within the probe's 100 bytes). Not yet run on the board.
Review round (fresh critic, same night): no blockers ("would flash as
is"). Fixed: the volume scale (esp_codec_dev's default curve put 50 at
-25 dB while the server's system skill assumes 50 to start; now 50 =
-15 dB, the old boot loudness, boot at 50, floor 10); clears read the
ring out instead of resetting it (a reset can race the writer's
lock-free send and fill the ring with 0x55, a loud hum); the type probe
decides on the first "type" key only (a nested one in a dev_event could
clear the speaker), now in `hux_type_probe` with host tests; the
contract test's state check; docs and comments.
**Open product question for Mario**: during a skill's claim (a Telegram
call) the device's mic now streams continuously, as the PWA does and as
the protocol's mic mode says. It is the first time the device has an
open mic without the lever held. Calls need it to be two-way; if the
walkie-talkie rule should hold even in calls, it is one line in
`hux_app.c` (`mic_update`).
**Bench test, 2026-10-11** (the original Huxley board, speaker amp
off, no sound in the room; see F-0015 to F-0019 for what it found):
GPIO7 pressed electrically through JTAG (pull-up flipped to pull-down in
IO_MUX: `button.talk press src=lever`, Mic1 streamed 22 frames, release
ended the turn); answers play (reply audio reaches the codec, 0 write
errors, verified electrically by the loopback self-test); "pon el volumen
al 80" by injected speech -> `set_volume 80` -> ES8311 register 0xB5;
`audio_clear` by barge-in; "pon la radio" streamed 27 s at real time
with no underrun, "para la radio" cleared it 81 ms after the press. **Left for a hand**: K2 (behind the I2C
expander, JTAG can't press it; its reads never failed in hours of
polling) and a real Telegram call (`input_mode`).
**Known gaps** (not in scope): no echo cancellation during calls; the
speaker leaks into the mic.

### F-0015: Long replies stalled, dropped and played choppy · fixed 2026-10-11, measured on the board

**Problem**: a long answer ("cuéntame un cuento largo") died 15 s in:
the socket heard nothing until TCP keepalive killed it, and the rest of
the reply was lost. With that fixed, the reply arrived at 0.65x real time
and the speaker ran dry hundreds of times (choppy); with that fixed, it
arrived 5-6x faster than real time and the 512 KB ring dropped 60 s of
an 80 s story.
**Evidence** (all on the board, `say 3`, amp off):
1. JTAG snapshot mid-stall: the WebSocket's TCP control block had
   `ooseq` = 32 segments (46 KB), the in-order queue empty (`rcv_wnd`
   65535). lwIP kept out-of-order segments without a cap
   (`TCP_OOSEQ_MAX_PBUFS=0`, IDF's default with PSRAM) against a 64 KB
   window and 32 Wi-Fi RX buffers: after one lost frame the out-of-order
   queue held every RX buffer and the retransmission had nowhere to land.
   IDF's Kconfig describes exactly this.
2. `ws.rx.stats` (new): 0.65x real time, gaps of ~250 ms (lwIP's TCP
   timer). Wi-Fi driver counters clean (`wifistat`: no RX buffer
   failures), ping to the board 0 % loss at 140 KB/s, the server's socket
   pinned at 128 KB queued. Cause: `LWIP_TCP_RECVMBOX_SIZE=6`; lwIP's rule
   is window / MSS + 2 (25). The mailbox filled while the WS task handled
   a chunk, lwIP refused the next segments and retried on its 250 ms timer.
3. With 1 and 2 fixed: 363 KB/s, an 80 s story in 14 s, and 364
   `spk.ring.overflow` (59.5 s dropped): the ring dropped when full.
4. Backpressure alone (wait for room) kept the audio but let macOS grow
   the server's socket buffer (`net.inet.tcp.autosndbufmax` 4 MB): the
   server's keepalive ping queued behind 30 s of audio, the server failed
   the connection after 20 s, and F-0016 left the device dead.
**Fix**: `TCP_WND` 32 KB, `TCP_OOSEQ_MAX_PBUFS` 8, Wi-Fi dynamic RX
buffers 40, `TCP_RECVMBOX_SIZE` 32 (sdkconfig.defaults, with the story);
the speaker ring is 4 MB in PSRAM (87 s, a whole reply; 3.4 MB PSRAM
stays free) and `hux_audio_spk_push` waits for room instead of dropping
(re-checking the barge-in hold every 20 ms; gives up after 2 s if
playback is stuck). A clear marks the ring stale and reads it out over the
next passes (a full 4 MB ring empties in ~0.6 s; the caller isn't held).
**Verified**: 46 s and ~150 s stories: 0 underruns, 0 drops, received =
played to 0.4 %, server send queue <= 132 KB, server -> board lag of the
last message 2.8 s (was 29.8 s), no disconnect. `spk.underrun` (new) and
`ws.rx.stats` (new) watch it from now on. **Residual**: a reply longer
than 87 s falls back to backpressure and the server-side buffer growth
(main `docs/triage.md` T1.32).

### F-0016: Any close from the server left the device dead until power-cycled · fixed 2026-10-11

**Problem**: esp_websocket_client's task exits on a CLOSE frame unless
`enable_close_reconnect` is set (it defaults off) and emits `CLOSED` /
`FINISH`, which `hux_net` didn't handle. The app stayed READY on a dead
client: no reconnect, presses sent nothing, no offline cue. Every server
close does this: an eviction by another client, a server shutdown, the
keepalive timeout of F-0015.
**Evidence**: after the story, JTAG showed no `websocket_task` and no
active TCP control block while the app logged READY; the server had
logged `client_disconnected close_code=1006` at the same second.
**Fix**: `enable_close_reconnect`; `CLOSED` posts a disconnect to the app;
`FINISH` (the task ended) makes the app restart the client. An eviction
(close 1001 "Replaced by new client", parsed from the CLOSE frame) pauses
the client until the next talk press, as an evicted PWA tab waits until
it is shown: reconnecting at once would evict that client, and the two
would trade places forever.
**Verified**: a test client took the server: `ws.rx.close code=1001
eviction=1`, paused; a talk press played the offline cue and the board
was READY 62 ms later.
**Review round** (fresh critic, same night; all fixed, bench 9/9 after):
a FINISH reaching the app after a press had restarted the client could
loop stop/start forever (the client's liveness is now one flag, set by a
start and cleared by the task's own FINISH; `resume` never stops a live
task); a Wi-Fi reconnect restarted an evicted (paused) client (only the
first IP starts it now; the app owns every later start/stop); a redial
queued before the eviction could leave the app READY on the stopped
client (the pause posts a disconnect after it); a client that couldn't
restart is restarted by the next press.

### F-0017: Barge-in played the old reply over the user for 1.5-2.5 s · fixed 2026-10-11

**Problem**: a press clears the ring, but the reply audio the server had
already sent kept arriving and played until the server's `audio_clear`
(it follows that audio in the stream): measured 1.55 s, then 2.5 s.
**Fix**: on a press while the server says it is speaking
(`model_speaking`, now parsed), the speaker holds: inbound audio is
dropped until `audio_clear` (or `model_speaking: false`, or 3 s). The hold
goes on before the clear so a write waiting for room can't land in the
emptied ring. Host test for the `model_speaking` parse; contract test
for its shape.
**Verified**: press 25 s into a story with the 4 MB ring full: 4.2 MB
marked stale, 167 KB in flight dropped, `audio_clear` 612 ms after the
press, the new answer played.
**Review round**: a chunk the WS task was still writing into a full ring
when the press cleared it landed after the clear and played; spk_task now
plays nothing while the hold is on, and `audio_clear` clears before it
ends the hold. The hold's expiry could wipe a newer press's hold: one
atomic word (press time | 1), expired with compare-and-swap.

### F-0018: Smaller defects found on the bench · fixed 2026-10-11

- **Volume clipped at the top**: esp_codec_dev's default "PA headroom"
  (a 5 V PA, a 3.3 V DAC) added 3.6 dB to every level: 100 was +3.5 dB
  of digital gain, so a full-scale reply clipped in the DAC. Same rail =
  0 dB; the curve's middle moved to -11.5 dB so 50 keeps the loudness of
  every test so far. Read back from the ES8311 register (and by loopback):
  10 / 50 / 80 / 100 = -42.5 / -11.5 / -5.0 / 0 dB.
- **Wi-Fi gave up after 5 retries** (`wifi.retries_exhausted`, then never
  again): a router reboot left the walkie offline until power-cycled. Now
  5 quick tries, then 1, 2, 4 ... 30 s, forever (`hux_wifi_retry`, host
  test); the disconnect reason is logged. On the board with an SSID that
  doesn't exist: reason 201, retries at 0 x5, 1, 2, 4, 8, 16, 30, 30 s.
- **Joined the first AP heard, not the strongest**: on the mesh the board
  sat at -26, -60 and -87 dBm on three boots a metre from a node. Full
  scan, strongest first (~3 s more at boot); `wifi.associated` logs the
  BSSID, channel and RSSI.
- **The WebSocket started before Wi-Fi had an IP**: a false "Host is
  unreachable" error every boot and 2 s lost. It starts on the first IP
  (READY at 2.2 s, was 4.1 s, before the full scan).
- **I2S underflow replayed the last buffer** (a stutter or buzz if
  spk_task is ever late): `auto_clear` sends zeros instead.
- **A press while offline couldn't stop playback** (the ring can hold a
  whole reply from before the drop), and its cue waited for the ring to
  drain: it clears the ring, then plays the cue (critic). Known edge: if
  the server was still sending the reply, it sends the rest after the
  reconnect (it never heard that press).
- **Stack headroom**: `hux_button` had 532 B left of 3 KB (now 4 KB),
  the WS task 1.2 KB of 4 KB before its first reply (now 6 KB), the log
  drain 688 B of 4 KB after a 12-turn soak (now 6 KB). `info` reports
  every task's minimum; `bench.py` fails below 512 B.
- `reset=UNKNOWN` for USB/JTAG resets; `ring_used` read 1 when empty.
- The original board's coredump partition held stale data (a false
  "Incorrect size of core dump image" error every boot): erased.

### F-0019: Bench tools: test the board with no hand and no sound · done 2026-10-11

The serial console (`hux_console`): `talk` / `release` (a third talk
input, debounced with the lever and K2), `say <n>` (a whole turn: a clip
from flash replaces Mic1 while talk is held; `tools/clips.py` renders
Spanish questions with macOS `say -o`, nothing is played), `selftest`
(electrical loopback: a tone on the DAC read back on the ES7210's echo
reference channel; pass = 80+ dB rise, mics alive), `tone`, `vol`,
`pa off|on` (silent bench), `wifi off|on`, `wifistat`, `info` (heap,
PSRAM, every task's stack minimum, ring, volume and the ES8311 register,
RSSI, time since the last inbound data). Also new on the wire: `client_event
device` on every connect (firmware version, reset reason, boot counter,
RSSI, volume: the server log now says which firmware is talking) and
`mic peak_dbfs` in each press's stats. `tools/bench.py` runs the whole
bench check list and prints PASS/FAIL. Review: a `say` whose press was
ignored left its clip armed for the next real press; the console disarms
it when the say ends.

### F-0020: The walkie got stuck in download mode inside its closed case · fixed 2026-10-12

**Problem**: plugged into the Mac after running on battery, the walkie
went silent: `rst:0x15 (USB_UART_CHIP_RESET),boot:0x0
(DOWNLOAD(USB/UART0))`, `waiting for download`. Every reset after that
(esptool's RTS reset, my logger's, a JTAG reset) came back the same;
only a power cut (rocker off, USB out) brought it back, and Mario had
to open the case.
**Cause** (esptool docs, "Advanced Options"; Espressif staff on
esp32.com): on the S3's USB-Serial/JTAG the host's RTS line is a core
reset that doesn't re-read the boot pins, so a chip a host once reset
with the boot pin overridden low stays in download mode. Opening the
port with DTR/RTS toggling (my logger, `idf.py monitor`, terminals) can
do that, and `idf.py flash` ends with an RTS reset. GPIO0 read high
throughout (esptool `read_mem` of GPIO_IN): the case doesn't press BOOT.
The S3 can't refuse a host's reset in software (C6 and later can;
`RTC_CNTL_USB_RESET_DISABLE` doesn't, per a forum report).
**Root cause** (found after the first fix, from the schematic): the
TCA9555 expander's INT# output is wired to GPIO0 through R15 (0 ohm).
INT# goes low on any input change and stays low until the changed port
is read; the firmware only ever read port 1 (K2), so after any change on
port 0 (EX1-EX3 on the header, RTC and card-detect lines) GPIO0 stayed
low and every reset short of a power cut booted into download mode. A
power cut resets the expander too, which is why only that helped; on
the bench nothing on port 0 moved. Fix (88baa3aa): both ports are read
every 10 ms and at boot; port-0 changes are logged (`tca.port0 change`).
Verified: GPIO0 sampled high over JTAG before the flash; `flash.sh`'s
watchdog reset booted the app by itself, case closed. Not all of it:
the next flash (1cd6e68c) landed in download mode again: GPIO0 read high
in the ROM but had been latched low at the reset, so something still
pulls it low at the instant of a reset right after flashing (unknown;
the same reset a minute later booted). `flash.sh` now checks whether
the ROM still answers and resets again, up to 8 times; the flash after
that came back by itself in 13 s.
**Also**: `tools/flash.sh` flashes and ends with esptool's watchdog reset
(a full reset that re-reads the pins) and `--unstick` boots a stuck
board from the Mac; `tools/serial_log.py` (and `serial-capture.sh`,
`bench.py`, `smoke.sh` on top of it) hold DTR and RTS still; the console
has `reboot` (esp_restart) so tools never reset the board over USB. The
README, debugging guide and CLAUDE.md say not to use `idf.py flash` /
`monitor` on this board. On battery or a plain charger nothing drives
those lines: a walkie in use can't get there.

### F-0021: Updates and debugging over Wi-Fi · done 2026-10-12

**Problem**: every firmware change meant the USB cable, and the walkie
lives in a closed case (4 screws, ~20 cables). Mario: "so I don't ever
have to connect this device again to this computer unless absolutely
necessary", and "look what people have built ... and implement that".
**What others do** (looked up, not invented): ESP-IDF's own
`esp_https_ota` with app rollback (the `advanced_https_ota` example,
the base of ESPHome's and Tasmota's updaters); Tasmota/ESPHome's
pattern of a small authenticated HTTP endpoint on the device; ESP-IDF's
core dump to flash, fetched later. The Fleet project's push-style OTA
marked images valid only after a network check, which rolls back a
good image when the server is down; this one doesn't.
**Built**: `hux_ota` (on-demand `esp_https_ota` into the other slot;
refuses another project's image and the running version unless forced;
a new image confirms itself after 30 s up, else the bootloader boots
the old one; revised after review, see below), `hux_dev` (port 80, token in `X-Huxley-Token`: `GET
/info`, `POST /cmd` = any console command, `GET /coredump`), console
`reboot` and `ota <url> [force]`, `tools/dev.py` (finds the board in the
server log, serves `build/`, follows the board's lines, keeps each
flashed ELF as `build/elf/<version>-<sha9>.elf` and decodes a dump with
the one whose hash it carries).
**Evidence** (on the board in its closed case, no cable used for any of
it): `dev.py flash` 02cc5438 in 11 s, confirmed 30 s later in ota_1; a
test image that aborts in `app_main` was installed, crashed, and the
bootloader went back to 02cc5438 by itself; `dev.py coredump` fetched
the 64 KB dump over Wi-Fi and decoded `abort()` at `app_main
main.c:118` with the test image's ELF (picked by hash, not the running
one); flashing the running version prints `not updated: ... ota.refused`
and exits 1.
**Gaps**: after that rollback the old image reported `reset=SW_RESET`,
not `PANIC`, so the reset reason can't tell a rollback apart; `/info`
now has `last_invalid` (the slot and version the bootloader rejected)
and `dev.py flash` prints it when an update doesn't stick.
HTTP, not HTTPS: fine on the home LAN, the token is the lock. The
bootloader and partition table still need USB.
**Review** (fresh critic, 2026-10-12), acted on: the 30 s timer kept
images that couldn't take the next update (dead door, bad Wi-Fi
settings, a hang): now `dev.py flash` confirms over the door after 30 s
(`ota confirm`), so a kept image is one that was reached and updated
over Wi-Fi; `dev.py flash` printed "done" while still pending and
matched versions by string (an uncommitted `X-dirty` build matched the
old image reconnecting): it now compares the ELF hash in `/info`
(`elf_sha`), checks the slot changed and waits for `valid`; a remote
`wifi off` locked everyone out (now `wifi off [s]` comes back); `ota`
rebooted the walkie mid-reply (refused while playing or listening unless
`force`); the image server shared all of `build/` (the ELF holds the
Wi-Fi password and the token): only the image now; the door accepted
`change-me` and could take every socket: weak tokens refused, a build
without one is an error, 3 sockets; `ota rollback` added. Not acted on
yet: an automatic go-back after N crashes in a row past the confirm
(medium; filed as F-0023), core dumps never erased after reading
(low).

### F-0022: Measure the link instead of guessing; stalls drop the connection · in_progress 2026-10-12

**Problem**: Mario, playing music on the walkie (case closed, on
battery): "It's being choppy all of a sudden." I answered from clues
(RSSI went from -27 to -48), and Mario: "I don't want you to guess
anything. We have complete control of the firmware and the software on
the servers. Why don't you create a reliable way to test stuff".
**Evidence** (server log, 2026-10-12): at 08:03:26, 08:03:35 and
08:08:48 the board opened a new WebSocket in the middle of a talk press
(the server evicted the old one, close 1006); the press at 08:08:46
reached the server 2.3 s late first. From 08:04 the music arrived slower
than real time (`ws.rx.stats` 300 ms of audio in a 1529 ms window,
`events` well above `msgs`) and the speaker played 57 gaps of 20-940 ms.
The board's own side of those drops never reached the server: lines
logged while the WS is down were dropped.
**Cause of the drops** (read, not guessed): esp_websocket_client tears
the connection down when a send can't finish in its timeout
(`esp_websocket_client.c:705`, `wlen == 0` -> `abort_connection`), and
every send uses `WS_SEND_TIMEOUT_MS` = 200 ms. The mic sends ~66 KB/s; a
link that slows below that fills the TCP buffers in a few seconds, and
the next 200 ms wait kills the connection and the question with it.
**Built (the harness)**:
- Server `server.tx.audio_stats` once a second while audio flows: audio
  handed over, time spent waiting in `ws.send`, the asyncio write
  buffer, and the kernel's unsent bytes (`SO_NWRITE` on macOS,
  `TIOCOUTQ` on Linux). Next to the board's `ws.rx.stats` (now with
  `rssi`) it says whether the network or the source was slow.
- Board log lines survive a disconnect: the sink says whether a line
  went out, the drain task holds it and retries, 256-line queue in
  PSRAM; boot lines (update check, Wi-Fi AP, previous crash) are queued
  too and arrive on the first connect.
- Console `server <uri>` (until reboot) and `wifi off [s]`, which comes
  back by itself (from the review: over Wi-Fi a plain `wifi off` locked
  everyone out until a power cut).
- `tools/bench.py --wifi`: the check list over Wi-Fi (developer door +
  server log), and two fault steps through Toxiproxy (Shopify's tool
  for this): `stall` (uplink at 1 KB/s for 5 s mid-question; pass = no
  reconnect, the question is heard) and `slowdown` (downlink capped
  mid-stream; prints server-sent next to board-received per second).
**Fix** (built; verified below once the board is free): sends wait up
to 5 s for room in the socket (`WS_SEND_TIMEOUT_MS`, was 200 ms): the
library only aborts a link with no progress for 5 s, a slow one keeps
going. The mic queue is 10 s in PSRAM (was 1 s). `ptt_start`, the frames
and `ptt_stop` go through that one queue in order: `ptt_stop` used to
leave from the app task and overtake frames still queued, so a backlog
cut the end off the question. `mic.tx.done` logs when `ptt_stop` really
goes out, with the press's totals.
**Results** (2026-10-12 night, case closed, amp off, over Wi-Fi):
- `stall` (uplink stopped 3 s, 1 s into a 5 s question). Before the fix
  (856c5385): FAIL, `ws.send.incomplete requested=1306 sent=0`, a
  reconnect, the question never heard (the field failure, reproduced).
  After (83e58a54): PASS, 0 reconnects, backlog peaked at 86 frames and
  drained, 287/287 frames sent, `ptt_stop` after the last, the question
  heard word for word.
- First try of `stall` through Toxiproxy passed on the broken build: on
  macOS (no root) its sockets autotune to megabytes, the Mac swallowed
  ~250 KB and the board never felt the stall. `bench.py` now has its own
  proxy (LinkProxy) with fixed 8 KB socket buffers.
- `slowdown` (downlink at 32 KB/s, ~40% of real time, for 8 s, radio):
  the server sent on time (1100 ms per ~1050 ms window), the board got
  400-500 ms per second: the network, measured. 25 dropouts (3.4 s of
  silence). With rebuffering (after a dropout mid-reply, wait for 1 s of
  audio before playing on): 2 pauses (2.2 s, 1.5 s; 3.7 s total).
- Full `bench.py --wifi --reset`: boot, selftest, turn, volume, bargein,
  health pass; offline and evict failed on the bench's side (a `wifi
  off` cut its own HTTP reply; eviction lines arrive only after the
  reconnect): both fixed in the bench/console.
- Also found: a silent press transcribes as "Subtitulos realizados por la
  comunidad de Amara.org" (Whisper on silence) and gets answered (server,
  fixed separately); board log levels arrive as `info` (the server's
  structlog overwrites the client's `level`; server fix); ~30 empty
  `wifi:` lines per boot (the Wi-Fi blob logs header and text apart):
  no longer streamed.
- Re-run of the full list on 5c88849c: everything passed except
  `offline`, a real one: Wi-Fi back at board time 45.3 s, the server
  again only at 63.2 s. The drop was at 33.17 s and the new connection
  came 30.05 s later: the WebSocket's reconnect attempt started just
  before the interface went down, and its TCP connect waited out the
  library's `network_timeout_ms` (30 s) on a SYN into a dead link. In
  the runs that recovered in under 1 s the attempts started after the
  interface was down and failed at once (every 3 s). The race can't be
  forced from outside; `network_timeout_ms` is now 5 s (the connect,
  ping/pong writes and the rest of a half-read frame), bounding it. The
  offline step now measures IP-to-READY (pass under 10 s), idle and in
  the middle of a slowed reply.
- Review (fresh critic, 2026-10-12 night), acted on, 979fbc7b: a
  stalled mic send holds the library's tx lock up to 5 s and the
  WebSocket task's PONG waited only 2 s for it, then aborted: a server
  ping during a stall still dropped the question (the first `stall` PASS
  was luck against a 20 s ping cycle). bench.py's proxy now injects a
  ping at a frame boundary mid-stall: d5b0bc8c FAIL, 4 reconnects;
  979fbc7b (tx lock timeout 6 s, build-checked > send timeout) PASS, 0
  reconnects, 984/984 frames. Also: the last 20 ms of every question
  was lost (mic stop didn't wait for the frame in flight; now 288/288
  frames where it was 287), `server <uri>` reverts by itself after 15
  min and can't race the app's resume, `quiet` capped at 12 h,
  rebuffering waits for 60 ms dry, bench restores in a finally and
  leaves a quiet period alone, `stall` checks the whole question went
  out, dev.py confirms an image left pending by an interrupted flash.
- New bench tools tonight: `silent` (an empty press must not be
  committed; fails on the server until its gate lands), `dev.py clips`
  (bench clips over Wi-Fi, POST /clips) with the requests that failed in
  real use as clips 7-15.

### F-0023: Go back by itself after repeated crashes past the confirm · queued (required by F-0028, ignoring BROWNOUT)

**Problem** (from the F-0021 review): an image that passes its 30 s and
is confirmed, then crashes on every talk press, crash-loops for good;
`ota rollback` needs someone to reach the door between crashes.
**Solution**: count panics with short uptime in RTC memory (the boot
counter is already there); after 3 in a row, `esp_ota_set_boot_partition`
to the other slot if it's valid, and say so (`ota.auto_rollback`).
**Effort**: half a day with a crash-on-press test image.

### F-0024: Opus audio over the WebSocket (the walkie's link drops packets) · in_progress 2026-10-12

**Problem**: playback cut for seconds ("plays half a second, cuts for 2
or more"; 34 % of playing time was gaps at 08:00-08:30). Measured on both
ends: the server sent on time; the walkie's 2.4 GHz link lost packets in
episodes (ping loss up to 8.3 %, p90 up to 665 ms, against 0 % to the
router at the same time); uncompressed PCM as base64 JSON is ~64 KB/s,
and TCP over that loss caps far below it. Not the amp (on/off A/B: gaps
0/57/49 s with it on, 15/0/24 s off), not the channel width (20 vs 40
MHz A/B, no difference).
**What others do** (Mario: "don't reinvent the wheel"): xiaozhi-esp32,
the most used open-source ESP32 voice assistant (Espressif ships it as a
component), sends Opus, 60 ms frames, one raw packet per binary
WebSocket message, negotiated by `audio_params` in the hello, JSON for
everything else on the same socket; Espressif's esp_audio_codec on the
device, `opuslib_next` on its Python server.
**Built (firmware)**: `?audio=opus` on the WebSocket URL (the opt-in); the
server's hello `audio_params` {opus, 24000, 1, 60} turns it on, anything
else stays base64 PCM; `hux_opus` (esp_audio_codec ~2.5.0): mic encoder
VOIP 24 kbit/s complexity 3 (packets <= 280 B), 20 ms mic frames gathered
into 60 ms packets, a short last one padded before `ptt_stop`; received
binary packets queued (PSRAM, 2.9 s) to a decoder task that feeds the
same speaker ring, with `audio_clear` through the same queue so it lands
after exactly the packets before it; the queue is dropped on a
disconnect. Stats: `mic.tx.stats opus_bytes enc_us`, `ws.rx.stats opus
dec_us`. Verified so far: init ok, internal heap 137-146 KB free, PCM
fallback with today's server (turn, silent, health PASS).
**Next**: the server side (in progress), then the bench on Opus (turn,
stall, slowdown, resume, hearing) and `dev.py link` over real use.

### F-0025: Say how much battery is left · scheduled 2026-10-12 (the wiring in F-0026's open-case session; Mario approved the case opening once)

**Ask** (Mario): "a way so the device can tell me how much battery is
left." Nothing reads the battery today.
**Validate**: the board's schematic (ESP32-S3-AUDIO-Board v1.1) divides
the cell by 3 (R2 200K over R11 100K, 200 nF) onto BAT_ADC, but its note
says battery measurement "is disabled by default": BAT_ADC reaches GPIO1
only through jumpers R53 + R78, or GPIO6 through R78, and stock boards
ship them open (the camera loses CAM_HREF on GPIO1; we have no camera).
**Probe** (`hux_battery`, console `battery`): reads GPIO1 and GPIO6
floating, then pulled down. A wired divider reads cell/3 (~1.3 V) and
holds ~0.5 V against the pull-down; an open pad reads noise, then ~0.
One `battery.probe gpio=N ... verdict=wired|not_wired` line per pin.
**Design** (if wired): the board pushes `client_event system.battery`
{mv, pct} on connect and every few minutes; the system skill subscribes
(`ctx.subscribe_client_event`), keeps the last reading, answers a
`get_battery` tool from it (saying when it is old or missing), and
speaks a warning when a reading crosses 20 % and 10 %. No new framework
concept: client_event already carries device telemetry to skills.
**Probe result** (c9a022d2 on the walkie, 2026-10-12): GPIO1 floats at
2632 mV (spread 725) and drops to 0 pulled down; GPIO6 2594 mV (spread
351), pulled 3 mV. Both `not_wired`: a wired divider would read ~1.3 V
and hold ~0.5 V. Mario: the original firmware showed a level with
nothing soldered. Waveshare's Arduino demo for this board reads GPIO8
(x3); its factory image and xiaozhi's board code read nothing. Probe
widened to every free ADC1 pin (1f1d57c3): GPIO 1-6, 8, 9 all float
(2373-3030 mV, spread 192-748) and fall to 0-38 mV pulled down. No pin
carries the cell on this board. The level Mario saw two days ago came from
this board's previous firmware (Fleet's statue): it read GPIO8 with a
x1.5 "divider" fitted so the reading looked like a full cell (x3 gave
8.3 V), and a guard that hid its "intermittent garbage". That is a
floating pin's 2.6-3.0 V, which our pull-down test drives to 10 mV. No software path gives a level (the charger's STAT only
drives the LED; the 3.3 V rail is regulated until the cell is empty).
**Blocked on Mario**: open the case and either bridge R78's pads 1 and 3
(BAT_ADC to GPIO6, the board's own 1 % divider), or wire 200K from the
cell's + to header IO6 and 100K from IO6 to GND. Either lands on GPIO6;
`battery` then says `gpio=6 ... verdict=wired`, and the design above
gets built.
**Where it stopped**: R78 and R53 are 0603 pads on the board's bottom
(the BAT / SPK side), ~2 mm apart (Waveshare's STEP: R78 x 19.4-21.0,
y 31.7-32.5; R53 y 33.6-34.4, board coordinates). Next: map them onto
Waveshare's bottom photo for Mario, or take option B (no map needed).

### F-0026: The open-case session before handover (remote-operation W7) · queued 2026-10-12

**Why**: the walkie goes to grandpa's retirement home and the case opens once more, never again (Mario, 2026-10-12; plan `docs/remote-operation.md` W7, server side T1.43 owned by the "Huxley improvement plan" session). Everything that needs the open case or the USB cable happens in that one session.
**Hardware** (a guide for Mario, with photos of where to solder, before the day): battery to IO6 (bridge R78 pads 1-3, or a 200K/100K divider; F-0025's probe confirms `gpio=6 ... wired`); VBUS through a divider to a free ADC1 pin (charging detection: the charger's STAT only drives the LED); remove R15 (the 0 ohm link from the expander's INT# to GPIO0: no reset can ever boot download mode again, F-0020); a pinhole to RST in the case (coordinate with the case session); check the cell has a protection board; a charge-only magnetic USB-C adapter afterwards.
**Flash** (the last USB flash): partition table with OTA slots 2 to 4 MB each (image 1.3 MB today), NVS 24 to 64 KB, coredump 64 to 256 KB (staged as `partitions-w7.csv` until the day: the device's table only changes over USB); final bootloader; full flash dump and `espefuse summary` saved.
**Battery firmware** (F-0025 reporting side): two-point calibration against a multimeter, median of idle samples, Li-ion table with hysteresis, 6 dB attenuation; `client_event system.battery` and the skill tool; low battery: a clip once, then deep sleep with a timer wake.
**Tests**: drain to cutoff with the rocker on, plug in, boots and reconnects alone; a realistic day on battery; current with Wi-Fi power save off vs on.

### F-0027: A walkie that survives alone (remote-operation W5) · queued 2026-10-12

Spec: `docs/remote-operation.md` W5. One escalation ladder (restart the WebSocket at 2 min without the server, Wi-Fi at 10, next known network, reboot at 60, after 3 reboots the other slot if valid; a network counts as working when it reached the server); reconnect backoff 2 to 60 s with jitter; a supervisor that feeds the task watchdog only while each part's liveness advances and logs which stalled; Wi-Fi list (compiled baseline: the home's network, a family phone's hotspot, Mario's home; plus NVS entries, `wifi.add` tries and reverts); server URI in NVS, kept once a hello succeeds; black box in RTC memory (last ~4 KB of log lines, reset cause, rollback reason) sent with the next hello; heartbeat `client_event device.health` every 60 s (fields agreed with the server session, including underrun ms and discarded ms per `audio_clear`); volume in NVS; spoken clips for no connection, low battery, OpenAI refusing.

### F-0028: Updates and the remote console through the server (remote-operation W4) · queued 2026-10-12

Spec: `docs/remote-operation.md` W4. The hello identity (fields agreed with the server session, one `docs/protocol.md` change); pull OTA on `server_event firmware.update {version, url}`, only when idle; an image keeps itself only after a local self-test, the hello, an HTTPS GET of the manifest with the OTA client's settings, a server-initiated close and reconnect, and `ota.accept`; the update channel frozen and handled in every state (a protocol mismatch must not park it); rollback bookkeeping in NVS (no_server, crash, selftest, manual); F-0023 required, ignoring BROWNOUT; remote console `server_event dev.cmd` / `client_event dev.cmd_result`; core dump summary in the hello, full dump on request; the port-80 door compiled out of grandpa's build. Built on the twin board (a0:f2:62:e2:fb:60) first.

Note on IDs: F-0011 to F-0013 were used in code comments and commit
messages (boot buzz, decode scratch, Wi-Fi power save) but their
entries never landed here; F-0003 (the speaker path) shipped as v0.3.x
and is moved to Done. The enclosure tracker (`docs/triage.md` on the
enclosure branch) calls the talk-button work F-0011; this is it.

---

## Queued (next up, not started)

### F-0003 — v0.3 speaker path · done (v0.3.x; see the code: hux_audio spk_task, hux_net reassembly)

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

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

**Talk lever: stays on, will not click, wobbles (2026-10-13)**: Mario,
after assembling rev E: pressed, the switch clicked but often stayed on
after release; cutting the spring shorter made it never click; he spent an
hour and found no length that did both. And the lever slid along its pin and
its blade caught on the channel going in, until he put a ring cut off a
spring on the pin as a washer. Validated: a tact dome lets go well below its
click (taken as 40 %), so the spring must push under the let-go at rest and
over the click at the stop, inside the lever's 3.8 mm stroke (3.55 of
spring). The 0.4 x 4 x 15 (0.59 N/mm) gains 2.1 N over that, and rev E rested
it at 0.9 N, about where a dome lets go; each cut coil jumps the force ~0.8 N.
Along the pin the lever had the channel's 0.25 mm each side over its whole
18 mm width. Fix, the lever and plungers only (the printed clear top, base,
faceplate and pin are unchanged, checked by their volume and bounds):
- spring 0.5 x 5 x 15 from his SciWil kit (label photo IMG_2464), whole:
  0.99 N/mm (6 active; 0.74 if 8), 0.5 N at rest, 4 N at the stop; 6 mm
  springs do not fit the tub's 6.4 bore; a 2 mm seat in the lever, its rim
  chamfered (a ramp from the front edge, first drawn, was on the edge that
  goes in last, and from the back edge it would cut the stop's face);
- plungers 4.5 / 4.75 / 5.0 / 5.25 (0.25 N steps) for a switch whose force
  is not known (1.6 to 2.6 N) and a spring that may come long, on the
  orange plate with the lever (`3_orange_lever_plungers`; plate 1 is now
  `1_white_faceplate_bridge_pin`);
- washers on the hub, 0.1 mm off the tooth and the seat wall, the blade
  narrower each side above it, 0.15 growing to 0.6 at the top (the pin's
  play lets it lean under a sideways push).
Cold review: the ramp (fixed as above), the lean (taper), plunger steps
smaller than the label's free-length tolerance (the 4.5 added), the washer
test (now on the hub's own ring), stale text; tub, faceplate and pin
verified unchanged to 0.000 mm3. Held at the stop the press is about 7 N
at the thumb against rev E's 5.
Tests: `test_hx1_tact_switch_clicks_and_lets_go` (for every switch force a
plunger clicks with margin and lets go; a stiff switch still clicks with
the spring a quarter softer) and `test_hx1_lever_is_centred_on_its_pin_by_its_washers`
(fails on rev E's lever). The website's model and print files are
regenerated. Lessons: a switch in series with a spring needs the let-go
force in the budget, not only the click; and a part that slides on a pin
wants its bearing faces near the pin, not across its whole width.

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

**Found 2026-10-12 by the case session**: the HX-1 tub top's corner block touched the BOOT button's plunger with 0.00 mm clearance (the CAD followed the board's outline, buttons included); a top printed a hair proud could have held BOOT in, which at any reset boots download mode. Fixed in the enclosure (a 0.6 mm pocket for every edge button). The hardware fix for the INT# path is R15's removal (F-0026).

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

### F-0025: Say how much battery is left · done 2026-10-12 (1b0fe784, probe fix below) (the wiring in F-0026's open-case session; Mario approved the case opening once)

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
**What the board has** (Mario looked, 2026-10-12): both are 3-pad groups with a 0R fitted. Upper group: 0R across middle and right; lower: 0R across middle and left (board held BAT/SPK plugs on the right). The schematic's "0R NC" puts each factory 0R across pins 2-1 (R53: CAM_HREF-GPIO1; R78: BAT_ADC to the link toward R53's pin 3, a dead end); that is the only fitting consistent with the probe (GPIO1 and GPIO6 both unconnected), so on R78 pin 2 is the left pad and the empty right pad is pin 3 (GPIO6). **The fix**: keep both 0R parts, add one solder blob on the lower group joining the middle pad to the empty right pad (BAT_ADC to GPIO6). Held the other way round, the same instruction lands on R53 instead and puts BAT_ADC on GPIO1 (through R78's 0R and the link): also readable, the probe covers both pins. Picture sent to Mario (`battery_bridge_exact.png`).
**Built ahead of the blob** (2026-10-12): firmware `hux_battery_start` (finds GPIO6 or GPIO1, median of 32 at 6 dB once a minute while quiet, `hux_battery_level.c` host-tested: a Li-ion curve, a fall followed at once, a rise only past 5 points), `system.battery {mv, pct}` after each hello, on a change and every 5 min, `battery cal <mv>`; the system skill keeps the last reading for `get_battery` (says how old past 15 min, "not reported yet" before any) and speaks once at 20 % and once at 10 % (re-armed above 30 %), the warning as its own task (the handler runs in the receive loop). Waiting on Mario's blob to see `battery.wired gpio=6`, then a multimeter reading for `battery cal`.
**Done on the walkie** (2026-10-12 20:20Z): Mario's blob landed on the upper group (R53: GPIO1 to the link, through R78's 0R to BAT_ADC), so the cell reads on GPIO1, as predicted for that case; GPIO6 and every other pin still read empty (no stray bridge). First boot: `battery.wired gpio=1`, 3.66 V uncalibrated = 18 %, and the system skill spoke the 20 % warning. A multimeter read the cell at 3.79 V: `battery cal 3790` set `cal_ppm=1026265` (the ADC read 2.6 % low), and the level became 3.81 V = 53 %. **Lesson**: around 3.6-3.8 V the curve is steep (130 mV is 30 points), so an uncalibrated board can warn falsely; every board gets one multimeter reading. Found doing it: the console probe read the level pin with the 12 dB scale after the level task had set it to 6 dB (1.9x too high, `verdict=not_wired`); fixed, the probe uses that pin's own scale.
**Where it stopped (before)**: R78 and R53 are 0603 pads on the board's bottom
(the BAT / SPK side), ~2 mm apart (Waveshare's STEP: R78 x 19.4-21.0,
y 31.7-32.5; R53 y 33.6-34.4, board coordinates). Next: map them onto
Waveshare's bottom photo for Mario, or take option B (no map needed).

### F-0026: The open-case session before handover (remote-operation W7) · queued 2026-10-12

**Why**: the walkie goes to grandpa's retirement home and the case opens once more, never again (Mario, 2026-10-12; plan `docs/remote-operation.md` W7, server side T1.43 owned by the "Huxley improvement plan" session). Everything that needs the open case or the USB cable happens in that one session.
**Hardware** (a guide for Mario, with photos of where to solder, before the day): battery to IO6 (bridge R78 pads 1-3, or a 200K/100K divider; F-0025's probe confirms `gpio=6 ... wired`); VBUS through a divider to a free ADC1 pin (charging detection: the charger's STAT only drives the LED); remove R15 (the 0 ohm link from the expander's INT# to GPIO0: no reset can ever boot download mode again, F-0020); check the cell has a protection board. (A pinhole to RST was planned; Mario dropped it, 2026-10-12: no holes, no extra prints. Recovery is the firmware's job, F-0027.)
**Flash** (the last USB flash): partition table with OTA slots 2 to 4 MB each (image 1.3 MB today), NVS 24 to 64 KB, coredump 64 to 256 KB (staged as `partitions-w7.csv` until the day: the device's table only changes over USB); final bootloader; full flash dump and `espefuse summary` saved.
**Battery firmware** (F-0025 reporting side): two-point calibration against a multimeter, median of idle samples, Li-ion table with hysteresis, 6 dB attenuation; `client_event system.battery` and the skill tool; low battery: a clip once, then deep sleep with a timer wake.
**Tests**: drain to cutoff with the rocker on, plug in, boots and reconnects alone; a realistic day on battery; current with Wi-Fi power save off vs on.
**Prep done 2026-10-12**: the handover table is staged (`partitions-w7.csv`, 29704bfc, validated with gen_esp32part.py; on the day also move clips.py / flash.sh to storage 0x820000). Parts located from Waveshare's STEP (assembly placements composed onto local leaf boxes), registered onto their underside product photo (fitted on the BAT and SPK plugs, checked on three chips within 2 px): **R78 and R53 are the two stacked 3-pad boxes near the top centre of the underside** (the BAT/SPK plugs on the right); **R15** (3 pads: pad 1 INT#, pad 2 3V3, pad 3 GPIO0; the fitted 0R joins 1-3, which is F-0020's download-mode trap) is on the underside about 12 mm left of centre, within about 1 mm: confirm on the day with a continuity beep from the BOOT button's GPIO0 side. **Charging detection needs no board soldering**: header P1 pin 1 is USB_5V (silkscreen "5V"), so 100K from 5V to a free ADC1 pin (IO8) and 100K from that pin to GND reads about 2.5 V while charging and 0 V on battery. The cells are protected 18650s (the enclosure decision): check each has its protection board when the case is open. **No RST pinhole** (Mario, 2026-10-12: "I don't want to make any holes or print anything else"; the case session had designed one and took it back out; the 0.6 mm clearance round the edge buttons stays, which fixes BOOT's 0.00 mm contact). Staff can't reach the board: a hung walkie recovers only through the firmware (F-0027's watchdog supervisor, escalation ladder and remote reboot; the black box for why). Whether staff can power-cycle it depends on the rocker (the case CAD says fingertip-reachable in the left wall, an older note says not; Mario is checking, 2026-10-12); either way the watchdog has to cover every hang. Idea from the case session for the battery firmware: alert when 5V is present (IO8) but the cell isn't rising, i.e. the rocker left OFF while plugged in (the charger is on the board's side of the rocker). **Parts decided**: battery level by a solder blob on R78's middle and empty pad, keeping the factory 0R (no parts; see F-0025 for what the board has); charging detection by two 100K through-hole resistors built inline in the harness (header 5V -> 100K -> junction -> 100K -> GND, junction wire to IO8), heat-shrunk in the cable channel like the lever's wires; battery fallback (only if the bridge fails) 200K (2 x 100K) from the cell's + to IO3 and 100K IO3 to GND, so one value (100K) covers everything. **Pins moved off IO5/IO6** (case session, 2026-10-12): the header tails of IO4/5/6/9 sit 0.7 mm from the holder bridge's front bar, so a wire soldered there is crushed when the case closes; IO8 (ADC1_CH7, the unused LCD's MISO) and IO3 (ADC1_CH2, the unused LCD's DC; a strap only if the JTAG-select eFuse is burned, check with `espefuse.py summary` on the day) clear it with the wire dressed straight up along the board. Build each divider compact: the resistors side by side, heat-shrunk to about 13 x 6 mm (a straight 25 mm bundle has one slot, two don't fit). The firmware reads IO8 for charging and IO6 (bridge) or IO3 (fallback) for the cell. The fallback divider's + comes from the ribbon's + splice at the faceplate end (the board's battery lead, cell + after the rocker), so no seventh conductor crosses the hinge; the bundles and routes are in the enclosure README's cable table. **Charging**: no magnetic adapter (Mario, 2026-10-12): staff use a plain USB-C cable into a wall charger (no data lines, so no host can pull the boot lines; never charge it from a computer).

### F-0027: A walkie that survives alone (remote-operation W5) · in_progress 2026-10-12

Spec: `docs/remote-operation.md` W5. One escalation ladder (restart the WebSocket at 2 min without the server, Wi-Fi at 10, next known network, reboot at 60, after 3 reboots the other slot if valid; a network counts as working when it reached the server); reconnect backoff 2 to 60 s with jitter; a supervisor that feeds the task watchdog only while each part's liveness advances and logs which stalled; Wi-Fi list (compiled baseline: the home's network, a family phone's hotspot, Mario's home; plus NVS entries, `wifi.add` tries and reverts); server URI in NVS, kept once a hello succeeds; black box in RTC memory (last ~4 KB of log lines, reset cause, rollback reason) sent with the next hello; heartbeat `client_event device.health` every 60 s (fields agreed with the server session, including underrun ms and discarded ms per `audio_clear`); volume in NVS; spoken clips for no connection, low battery, OpenAI refusing.

**Progress** (more urgent since Mario dropped the reset pinhole: every hang must recover in firmware):
- Server URI in NVS: done, 13394437 + 1642f2f6. Board test: `server keep` to a second address (the bench proxy on 8775) saved at its hello; a reboot came back on it (`source=nvs`); with that address dead after a reboot, the fallback to the compiled one is under test.
- Board test of the saved address (2026-10-12): `server keep` to the bench proxy (8775) saved at its hello; a reboot came back on it (`source=nvs`); with the proxy dead after a reboot the walkie handed over to the compiled address on its own 10 min later and connected (18:27:41Z). That outage also measured the log flood: 786 lines dropped (3 per 2 s retry), the fallback's own line among them.
- Backoff and ladder (WS 2 min, Wi-Fi 10, reboot 60; `hux_link.c`, host-tested). Per-attempt lines quieted (first three, then every tenth; the app's `net.ws.disconnected` once per outage).
- Code review (fresh critic, 2026-10-12), all taken: **blocker**, `server keep` with no `ws://` failed `set_uri` silently, stayed on the old server, its hello saved the typo, and every boot then asserted in WS init (USB only): addresses are now validated (scheme, host, explicit port, path, length; `hux_link_uri_ok`, host-tested), a refused `set_uri` undoes the switch, and a saved address that is invalid or fails at boot is erased (never assert on NVS data). Also: a hello queued from a closed connection could prove the new address (connection generation now); the revert decided outside the lock (re-checked under it with a generation); pause/resume and hello took no lock, so `s_paused` could stick and disable the ladder (every start/stop under the link lock, a start clears it); a client started before the first IP broke the running flag (only the first IP starts it, through a pending-start handoff; the event task never waits on the lock); the reboot step waited on the lock (now none); the Wi-Fi step raced the retry timer; the watch handover moved to 5 min (off the Wi-Fi step at 10); the jitter at the cap now spreads 45-60 s. Nits taken: logs for every early return, `xTaskCreate` checked, key=value lines, the ladder task in `info`.
- Ladder board test (0ae881b2, 2026-10-12 18:42-18:55Z, `server ws://...:9999/ 13`, nothing listening): backoff 1.8, 4.3, 7.2 s ... 60 s by attempt 10; `ladder.step step=ws lost_s=122`; `step=wifi lost_s=602` (re-associated, new IP, `ws.retry_fast`, backoff back to 1.5 s); revert at 780 s, hello, `ladder.found lost_s=780 steps=wifi`. The app's resume after each deliberate stop waited on the link lock and found the client running (`ws.resume skipped why=running`). Validation refused `192.168.68.71:8775/` and `ws://192.168.68.71/`; the black box carried the previous boot's 16 W/E lines across a reboot (`app.blackbox bytes=1032 sent=1`).
- An old dump found on the walkie (build 7b01b89c-dirty, this morning, never read): `websocket_task` LoadProhibited in `strcasecmp` from `esp_transport_list_get_transport` at the client task's start (`esp_websocket_client.c:1129`, `config->scheme` freed). That is the critic's finding #2: `set_uri` (a `server` switch, the bench proxy) freeing the config while the client task was starting. Fixed by 0ae881b2's link lock (every start, stop and `set_uri` serialized, `set_uri` only on a stopped client). It also showed why dumps must be read and erased: with no-overwrite on, the next crash would have left none.
- Volume in NVS: done, de860186.
- Black box: WARN/ERROR lines in a 4 KB RTC ring (`hux_blackbox.c`, host-tested), sent as `device.blackbox` after the next hello.
- Supervisor: built, 6f2f9cd8 + 7756f75f, to the critic's notes below. Board tests (2026-10-12 19:13-19:20Z): `hang spk` stalled at 10 s, the watchdog fired, restart with `super.boot reset=6 last_stall=speaker at_s=55 early_in_a_row=1`, the black box carried `super.stalled` and `twdt.fired tasks= - hux_super`; `hang app` stalled at 30 s, and its core dump shows `hux_app` stopped at `hux_app.c:660` (the test hang); two more early stalls made `early_in_a_row=3 degraded=1`, where a hung speaker logged `enforced=0` and did not restart; a plain restart cleared it (`degraded=0`). An update under the armed watchdog (7756f75f) went through with no false stall.
- Supervisor design critic (2026-10-12), folded in: **blockers**: count work done, not loop turns (a wedged Opus decoder leaves the speaker writing silence and every counter advancing: watch "work waiting, none finished" for the decoder queue, the uplink queue, successful speaker writes and mic reads); a stall after every boot is a reset loop no OTA can fix and wears the core-dump sectors (count early supervisor resets in RTC memory, at 3 make the audio parts log-only and report it in the hello; `CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE`). Also: the panic restart is a CPU reset (no strap re-latch) but its RTC-watchdog fallbacks (panic's 10 s during the dump, `esp_restart_noos`, the bootloader's 9 s) do re-latch, so R15's removal stays a hard gate; the app's liveness from a 1 s queue timeout, not a ping; watch the esp_timer task (1 s periodic counter); a standing-down state for deliberate exits (deep sleep, OTA reboot, the ladder's restart); `esp_task_wdt_isr_user_handler` writes the triggered tasks to the black box; `hux_super` above the audio tasks; button liveness = successful TCA reads; `CONFIG_ESP_TASK_WDT_TIMEOUT_S=10`; bench each heavy path (OTA, a ring fill, a dump erase, the ladder's Wi-Fi restart) with panic on.
- Supervisor (design, gate 2, 2026-10-12). Problem: the task watchdog only logs (`CONFIG_ESP_TASK_WDT_PANIC` off), so a hung task leaves the walkie dead until a power cycle. Sketch: a `hux_super` task samples liveness every 1 s and feeds the task watchdog only while every part advances: the app (the supervisor posts a ping event every 5 s; stalled after 30 s with none handled), the speaker loop and the mic loop (every 20 ms; stalled after 10 s), the button poll (10 ms; 10 s), the ladder task's tick (60 s: it blocks in `esp_websocket_client_stop` if the WS task is stuck). The WebSocket while the server is up: no event for 90 s (the server pings every 20 s, and pings arrive as DATA events) counts as lost for the ladder, not a reboot. On a stall: `super.stalled part= age_ms=`, the part saved in RTC memory for the next hello, then no more feeding; with `CONFIG_ESP_TASK_WDT_PANIC=y` (10 s) the panic writes a core dump (every task's backtrace) and restarts. A panic restart is a software reset, which doesn't re-latch GPIO0 (F-0020's trap needs a chip reset; R15 comes off on W7 anyway). No task-watchdog warning in a day of logs (2026-10-12), so the idle-task checks stay on with panic. Repeated stalls right after boot belong to F-0028's rollback. Tests: the stall decision as a pure host-tested function; on the board a dev command that hangs one part, to prove the reset and the dump.
- Heartbeat `device.health`: built, 3de141d8 (`hux_health`, every 60 s, counts since the last line that reached the server, unsent lines fold into the next). On the walkie 19:30:43Z: `uptime_s=60 heap_internal_min=125459 heap_internal_largest=53248 psram_min=2605780 rssi=-42 channel=9 reconnects=1 underruns=0 ota_state=valid`. The per-event `audio.discarded` is not built: `discarded_ms` in the minute line covers the trend; the event needs the clear's cause carried to a sender that isn't the speaker task.
- Spoken clips (2026-10-12): `hux_prompts`, sentences stored in the firmware and said through the speaker decoder, so no server is needed and an update carries them. `tools/render_prompts.py` renders each with the persona's voice (OpenAI TTS `gpt-4o-mini-tts`, coral, "despacio, claro y con calidez"), trims it, encodes it with the server's own Opus encoder (24 kHz, 60 ms: what the walkie already decodes) and checks it by decoding and transcribing it back (both came back word for word). `offline` "No tengo conexión. Lo sigo intentando." (3.3 s, 11.4 KB) replaces the falling tone pair on a press while offline (the tones stay as the fallback); `battery_empty` "Batería agotada. Ponme a cargar." (2.8 s, 9.8 KB) is for F-0030. Level matched the model's own speech through the walkie then (speech RMS -25.8 and -24.5 dBFS against -22.7 to -26.1 for six recorded replies). Re-levelled 2026-10-13 for T1.47 (one loudness for everything the walkie plays: speech at -20 LUFS, true peak at most -1.5 dBFS; the server raises the model's voice about +7 dB): same takes, two-pass loudnorm, `render_prompts.py --level-only`; after the Opus round trip offline -19.9 LUFS / -5.4 dBTP, battery_empty -20.1 / -2.5 (were -26.6 and -26.1). Packets enter the decoder's queue staged under its control lock (never the WebSocket task's staging buffer), between two in-order decoder resets. A press during an outage now fixes the hello's `played_after_drop_ms` / `ring_left_ms` at that press, so the spoken cue isn't reported as server audio heard (T1.44). Host test: the parser and both shipped files. Console `prompt <name>`; the bench's offline step expects `prompt.play name=offline ... queued=N of N`. Board test: pending (the walkie was off).
- **Wi-Fi list (design v2, after its critic, 2026-10-12).** Why now: Mario sets up the home's network in person with this laptop (he learns the name and password only there; nobody else touches the walkie), and today the firmware knows one compiled network (his).
  - v1 (NVS list, scan-and-rank, `wifi addhex` kept only after a hello within 90 s, redacted logs) went to a fresh critic. Blockers: the console's 64-byte line silently cut long passwords (and dev.cmd ran a line cut at 128); keeping a network only if the server answered within 90 s drops the home's network whenever the Pi, the tunnel or Mario's internet blips, or the WebSocket points at the LAN address; NVS is erased on NO_FREE_PAGES / NEW_VERSION, losing the only copy. Should-fix: the ladder's Wi-Fi step fires once per outage and the 60 min reboot restarts any rotation; six places call `esp_wifi_connect` and a scan fails during a connect; the Wi-Fi driver writes the password in plain text to `nvs.net80211`; an add must reset the ladder and the server watch; `serial_log.py` echoes FIFO lines to /tmp logs, a dev-cmd GET puts it in shell history, `getpass` needs Mario's own terminal, core dumps hold stacks; reasons 15/204 also mean weak signal; WPA3 needs `sae_pwe_h2e`. Cut: remote add, the "active" rule. Add: Mario's phone hotspot compiled in, a scan from the walkie's own spot, its MAC, a step-by-step verdict, a reboot at the end.
  - **v2.** Networks: up to 3 compiled (`HUX_WIFI_SSID` plus optional `HUX_WIFI2_*`, `HUX_WIFI3_*` in secrets.h: Mario's home, his phone hotspot, and after the visit the home's network: the first update after the visit compiles it in, so NVS is only a bridge) plus up to 4 in NVS (one blob each). `good` = the SSID that last reached the server (written only when it changes). Compiled server URI `wss://walkie.ma-r-s.com/` in grandpa's build.
  - Choosing (pure, host-tested `hux_wifi_rank`): known networks heard, `good` first, then by signal; hidden networks (empty name in a scan) count as maybe-heard last; none heard: `good`, then the first compiled. At boot with `good` set: join it directly (its own all-channel scan already picks the strongest AP of a mesh), no extra scan.
  - One join path under the link lock, run by the ladder task, never the event task: it pauses the retry timer, disconnects, scans if asked, sets the config, connects. `esp_wifi_set_storage(WIFI_STORAGE_RAM)`: the driver keeps no copy of any password. WPA2 minimum, `sae_pwe_h2e` both.
  - No IP for 2 min: scan, and move to the best known network heard if it isn't the current one; never to one not heard; none heard, stay. IP but no server for 10 min: the ladder's Wi-Fi step moves to the next heard candidate (round robin, index in RTC memory, repeated every 10 min) or restarts Wi-Fi when there is only one. Reboot at 60 as today.
  - Adding (USB only): `tools/wifi.py` opens the serial port itself (no reset, no FIFO, nothing written to /tmp), shows `wifi scan` from the walkie's spot (name, signal, security, band), Mario picks or types the name, types the password with no echo (his own terminal, never through Claude). The walkie gets `wifi addhex <ssid hex> <password hex>`, checks the lengths (`wifi.add ssid_len= pass_len=`), joins it, and keeps it once it has an IP (`wifi.add saved`), then reports each step: heard, associated, IP, server reached (or why not: TLS/HTTP/DNS from a richer `ws.error`); failure says why (not heard; wrong password only after 3 auth failures in a row; security too weak; enterprise) and goes back to the previous network. An add resets the ladder and the server watch. The tool also checks `https://walkie.ma-r-s.com` from the laptop on the same network, then reboots the walkie and waits for its hello (the boot path is not the add path).
  - `wifi list` (names, compiled or saved, `good`; never passwords), `wifi forget <ssid hex>` (refuses a compiled one), `info` gains the MAC.
  - Secrets: lines up to 256 bytes; an overlong line is refused whole at every entry (console, developer door, dev.cmd), never cut; the console echo and the developer door log redact everything after `wifi addhex`; the console's buffer is zeroed after it. No remote add.
  - **Built** (2026-10-12): `hux_wifi_list.c` (ranking + hex, host-tested), the list, join, scan, add/forget/list in `hux_net.c`, `tools/wifi.py`, compiled extras `HUX_WIFI2_*`/`HUX_WIFI3_*`, the compiled URI the tunnel (secrets.h), a `ws.error` with its cause. Code critic (fresh): nothing strands the walkie (the hourly reboot, `good` at boot, the 2 min rescan and the ladder always bring it back); one blocker and nine should-fixes, all taken: the tool's final "Done" proved nothing (now it drains old lines, survives the USB port dropping at the restart, waits up to 4 min and says which network the walkie came back through); a refused config still moved `s_cur` (now kept, the add fails `why=config`; a 64-char password must be hex); the no-IP clock counted from the last join, not the IP loss; "never a network not heard" was broken by the ranking's fallbacks (now only networks heard by name rank; none heard, stay; hidden ones only as `good` at boot); a broken `good` still heard was never left (no IP now takes the next one heard); forgetting the active network left `s_cur` on the wrong entry; "server reached" could be the previous network's hello (now after the IP); a failed NVS write still said saved (now `why=nvs`); the console's scan raced the retries (now under the link lock, retried by the tool); the add now restarts the server watch. Nits taken: the redaction decided by token, the console's read buffer zeroed, saved entries an update compiled in stay in NVS (a rollback still has them), the add's lock timed, auth failures counted in a row, the driver's old flash copy of a password erased once, the port opened exclusive, the real SSID out of the test. Left: the rotation index in RTC memory (a rotation finds a working network within 10 min, before the 60 min reboot).
  - **Board checks** (0d5c581e, 2026-10-13 01:09Z, remote): boot `wifi.list compiled=1 saved=0`, `wifi.flash_copy_erased err=ESP_OK` (the driver's old flash copy of the password, once), `wifi.init source=compiled`, hello 7 s after boot through the tunnel, `wifi.good saved=ESP_OK`; `wifi list` (MAC, good, active); `wifiscan` with `auth=` and `known=`; `wifi addhex` through the developer door refused (`USB console only`) and redacted (`dev.cmd <redacted>`, also with odd spacing); a 220-byte line refused whole (HTTP 400). Not yet: the add itself over USB (Mario, with his phone's hotspot, before the visit). Known oddity: the driver logs `E wifi: sta is connecting, return error` once per boot since this image (a second connect while the first is under way; the join works every time); trace later.
  - **First field test failed** (2026-10-14 00:28-02:04Z): Mario took the walkie outside with his iPhone's hotspot on (Maximize Compatibility, Allow Others to Join, the hotspot screen open; compiled in as `HUX_WIFI2_*`, 312e4182). It left home Wi-Fi at 00:28:49 (RSSI -75, close 1006), said "No tengo conexión" at every press, and never joined the hotspot; nothing showed connected on the phone, even with the phone renamed "Familia Ruiz" and the home password. Its outage log was lost: Mario switched it off and on with the rocker, and the black box (RTC memory) dies with power. Back home it rejoined at once (02:04Z). Suspected: the no-IP rescan scans while a reconnect is still winding down (`esp_wifi_scan_start` refuses, ESP_ERR_WIFI_STATE) and the walkie stays put; that path never ran on hardware. Next: a bench `wifi join <ssid>` to put the walkie on a network it can't hear (the field case in reverse) and watch it find home, with the scan retried while the radio is busy and each retry logged. Lesson: in a field test, don't use the rocker before the walkie has reconnected somewhere: the black box is RAM.
  - **Before the handover** (2026-10-14 ~05:50Z, Mario asleep, nothing audible allowed): the field test's lines are gone (the rocker), so no reason codes exist. The join path read again: the station config pins nothing (all-channel scan, strongest AP, WPA2 or better, PMF capable, SAE H2E), and every scan and join clears its retry hold. The suspected cause (a rescan refused while a connect winds down, leaving the walkie on a network it can't hear) was fixed in ebb0afab and passed the bench repro (`wifi join` onto an absent network, found home again in 2 min 10 s). Still owed: a real hotspot join with Mario's phone. For the retirement home: its Wi-Fi goes in on site over USB (`tools/wifi.py add`); the hotspot stays the compiled rescue.
  - **On site** (2026-10-14 14:29Z, Mario with the walkie on the laptop's USB, `wifi.py add`, 02975ebc): the home's "Rafael" (2.4 GHz, WPA2-PSK, channel 12) joined at once (`connected with Rafael ... channel 12`, the driver's default country policy adapts to the AP), `wifi.add saved`, `server_reached`, `wifi.good ssid="Rafael"`, and back by itself after the reboot. Before it, `wifiscan` showed `found=0` twice while the ladder's scan heard 12: without an IP the console scan restarted the connect before reading its results, and the connect's own scan emptied the list. Fixed (the results read first, then the reconnect); rides with the next update.
  - Later, not now: the "other slot after 3 reboots" must count only reboots where a known network gave an IP (a 3 h outage at the home must not roll the firmware back).
- Still to do: next known network (needs the Wi-Fi list), the other slot after 3 reboots (with F-0028's rollback bookkeeping), `audio.discarded`, a clip for "the server is up but can't answer" (needs the server session: it knows the cause).

### F-0028: Updates and the remote console through the server (remote-operation W4) · in_progress 2026-10-12

Spec: `docs/remote-operation.md` W4. The hello identity (fields agreed with the server session, one `docs/protocol.md` change); pull OTA on `server_event firmware.update {version, url}`, only when idle; an image keeps itself only after a local self-test, the hello, an HTTPS GET of the manifest with the OTA client's settings, a server-initiated close and reconnect, and `ota.accept`; the update channel frozen and handled in every state (a protocol mismatch must not park it); rollback bookkeeping in NVS (no_server, crash, selftest, manual); F-0023 required, ignoring BROWNOUT; remote console `server_event dev.cmd` / `client_event dev.cmd_result`; core dump summary in the hello, full dump on request; the port-80 door compiled out of grandpa's build. Built on the twin board (a0:f2:62:e2:fb:60) first.

**Split** (agreed with the server session, 2026-10-12): firmware here; the server serves `/firmware/manifest.json` and `/firmware/<version>.bin` behind the Bearer token (url relative to the server URI in use, last two versions kept), `POST /admin/dev-cmd` and `POST /admin/firmware-offer` (loopback and Tailscale), and the accept flow: a pending image sends `device.hello slot_state=pending_verify` then `firmware.status {phase: "selftest", error}`; GETs the manifest with the OTA client's settings; the server closes with 1012 to force one reconnect; on the next hello it sends `ota.accept`, which the walkie honours only if its version matches, its silent self-test passed (supervisor parts progressing 20 s, expander and both codecs on I2C, heap floors), the manifest GET returned this version and elf_sha, and a reconnect followed the close; then `firmware.status {phase: "accepted"}`, else `ota.accept_refused reason=` and `failed`. The device token minted (secrets.h `HUX_DEVICE_TOKEN`, `server/runtime/.env` `HUXLEY_DEVICE_TOKEN`, mode 600).
**Progress**: `device.hello` (schema 1, replaces `device`; the server already treats it as the firmware; dev.py reads both), the token on every upgrade, `server_event` parsing (`hux_proto`, host-tested: dev.cmd, firmware.update, ota.accept, odd payloads, a long line cut and flagged), `dev.cmd` answered with what the command logged (`hux_log_capture_*`). Board test (6caa2636, 2026-10-12 ~20:50Z, a stand-in server on :8776 for one connection): the token arrived and matched; `device.hello` complete (schema 1, elf_sha, slot ota_1 valid, caps health/dev_cmd/battery, coredump null, net source compiled, time_to_hello_ms 4066); `dev.cmd` `info` and `battery` answered with their output; back on the real server, `client.device.hello` logged. The pull update and the accept flow: built, d8084a7a (wss and the CA bundle 35e99eb6; a public wss echo answered the walkie's frames, internal heap min 125 KB -> 84.9 KB with one TLS session, largest block 31.7 KB). **End to end against the real server through the tunnel** (2026-10-12 22:13-22:23Z, the Pi on 80c409f, the walkie moved with `server keep wss://walkie.ma-r-s.com/`: certificate validated 1.16 s after the start, connected at 1.85 s, internal heap min 81 KB with one TLS session, the Pi logged `server.auth.accepted role=device source=external token=valid`):
  - Pull 1 (80c409fe-otatest): offer to download 0.2 s, 1.4 MB in 12.6 s over a second TLS session while the WebSocket's was open (no allocation failure: mbedTLS stays in internal RAM for now), reboot, back on wss 7 s later as `pending_verify`, self-test passed (`dac_id=0x83 adc_id=0xff expander=1 heap_min=86655`). Then **bug**: the manifest GET went out with no token (401 `auth_refused reason=missing`): the bearer was set only by a pull, and a pending image fresh from a reboot hasn't pulled. Fixed in 4dddec8c (`hux_ota_after_hello(token)`). The stand-in never checked the manifest's token.
  - Rollback: a `reboot` of the stuck pending image booted the previous one (`ota.last_invalid ota_0 80c409fe-otatest (rolled back from it)`); its hello got the next offer at once.
  - Pull 2 (4dddec8c-otatest, the fix): download 12.7 s, pending, self-test, manifest 200 match=1, the server's 1012 two seconds later, reconnect, `ota.accept`, `accepted` at 22:19:18: 43 s from the hello that carried the offer.
  - Pull 3 (4827d527, the release build under its commit name): the same way, accepted 22:22:23 (self-test heap_min 84963; the reconnect hello logged `app.after_drop ... queue_ms=0 received_ms=0 frozen=0`). The walkie runs what is offered.
  - Also found: after a `server keep` switch the hello said `played_after_drop_ms=6120` (the two prompts since boot): no drop recorded for a stopped client (fixed, 4dddec8c). The server keeps a device `pending_verify` until its next hello, so an offer right after an accept waits for a reconnect (told the server session). A console line that reconnects the walkie (`server ...`) can't answer the admin route (its connection is gone): expected.
Next: rollback bookkeeping (the hello's `rollback` field), F-0023, the heap during a download (measure before moving mbedTLS to PSRAM; F-0032's alert measured two sessions at `heap_internal_min=25615`, 2026-10-13: an update pulled while an alert or a manifest check is in flight would be a third).

### F-0029: Battery life: how long a charge lasts, power save, and a low-power mode · queued 2026-10-12

**Why** (Mario, 2026-10-12): grandpa may forget to switch it off, and charging daily would be a burden. Nothing has measured how long a charge lasts.
**What we know**: the steady draw is the Wi-Fi radio, kept awake (`WIFI_PS_NONE`) since F-0013 for fast first packets, when the walkie was assumed mains-powered. Messages are noise next to it: a battery reading is 60 bytes, well under a millisecond on air. The rocker cuts the cells completely (the measuring divider sits after it), so off is off. WS pings went from 10 s to 20 s (Mario). TCP keep-alive probes after 3 s of idle (`keep_alive_idle 3 / interval 2 / count 3`) would wake a sleeping radio every few seconds: revisit with power save.
**First figures** (2026-10-12, preliminary): unplugged about 21:25Z; the first hour off the charger reads high (surface charge: 4.36 V to 3.85 V at once), so it doesn't count. From 22:03 to 23:10Z, streaming music the whole time (volume 100, then 20 from 22:41), 48 % to 44 % (3802 to 3780 mV): about 6 % an hour, roughly 16 h a charge in heavy use, Wi-Fi awake throughout. Needs a quiet stretch (idle, radio on) and a full day before deciding on power save.
**Plan**: (1) measure: one day unplugged in normal use, the drain read from `device.health`'s `battery.mv` once a minute (no meter needed), giving hours per charge; (2) if under a day or two, test `WIFI_PS_MIN_MODEM` (F-0013's failure was a 200 ms send timeout; it's 5 s now) and measure both the drain and the press-to-answer latency; (3) a low-power mode for when he forgets it on: after hours with no press and nothing playing, a deeper sleep that still wakes on the lever, on incoming reminders and calls (it can't fully sleep: proactive turns reach it), plus the low-battery deep sleep with a spoken clip (F-0027); (4) say it plainly when the cell is low (done: 20 % and 10 %, F-0025).

### F-0030: A low-battery cutoff above the cells' own protection · built 2026-10-13 (a8e8ef5b; the lever paths left for a visit)

**Ask** (Mario, 2026-10-12): a cutoff voltage as an extra layer, not relying on the cells' protection boards (about 2.5-2.75 V, deep enough to age them).
**Design v1 rejected by its critic** (2026-10-12; light sleep inside the running firmware at 3.40 V). Blockers: (1) the running firmware undoes a park: IDF moves esp_timer forward across light sleep, so the ladder restarts the WebSocket, Wi-Fi (`esp_wifi_start`) and the board on schedule, the Wi-Fi retry reconnects, a lever press resumes the client, a cue freezes mid-sound; (2) an audiobook never gives a quiet reading (loaded readings are ignored), so hours of book run the cell to the protection board anyway; (3) a broken reading (a cracked blob reads about 0 V) would park it forever; (4) the low two-tone already means "no connection": the cutoff needs its own spoken clip; (5) a level-triggered lever wake loops on a held lever or a shorted wire.
**Design v2** (the critic's): **park at boot**. On cutoff the running firmware plays the cutoff clip, sends `system.battery {cutoff: true}` (no waiting), sets an RTC flag, reads the expander inputs (F-0020) and restarts (a CPU reset: RTC survives, GPIO0 isn't re-read). `app_main`, before Wi-Fi, audio, the supervisor and the ladder: if the flag is set or a pre-Wi-Fi reading is under 3.45 V, a park loop (expander set, PA off, codecs closed, ADC, the lever as a wake source) light-sleeps 60 s at a time, reading the inputs before each sleep; at 3.60 V or more (only a charger lifts it there) it continues into the normal boot. Triggers: a quiet reading (ring empty 30 s) under 3.40 V, or three loaded readings under 3.25 V a minute apart (a long book); only readings between 2.9 and 3.4 V count, only after a reading in the 10 % zone at least 10 min earlier, only with the calibration within 5 %; below 10 %, readings every 2 min. A press during the park says the clip once, then waits for release (held over 30 s: timer-only wakes). Holding the lever 10 s boots anyway. Logs at warning level with RTC counters (`battery.cutoff`, `battery.resume parked_s= wakes= presses= min_mv=`). Light sleep with codecs closed is estimated at 1.5-3 mA against 1-2 mA for deep sleep (measure in series once); deep sleep later if the twin board proves a deep-sleep wake keeps the boot mode (`rst:0x5`).
**Depends on**: the spoken clips (F-0027): a voice prompt stored on the walkie, played without the server. Done, bba8319c.
**Built** (2026-10-12): the decision, pure and host-tested (`hux_battery_cutoff.c`, 29 checks: dwell, glitch, broken readings, loaded counting and its resets, charging, boot and resume); the level task feeds it every reading (2 min in the zone; resting = amp quiet 30 s and no press under way); `hux_park` says it, arms, restarts, and parks at boot right after the expander and the ADC (`main.c` order: board, battery, park, then the app). Code critic (fresh, 2026-10-12) found no path that strands the walkie, and seven should-fixes, all taken: the sentence interleaved with a streaming book in the decoder queue (now the link pauses and the queue is flushed first); the codecs stayed powered through the park (a CPU reset doesn't reset them: their own power-down sequences are written over I2C, and the amp goes off before every restart); a stuck or flapping lever defeated the park or looped say-restart (now only a press that began after a release seen in this park counts, a held lever gets timer-only wakes, the lever is debounced, one sentence per 30 s); a reading at a press counted as resting (now never); a park would roll back an update not yet kept (never parked while pending; the update itself is refused under 40 %); no remote way out of a wrong-but-believable reading (`battery cutoff off|on`, NVS); the flag was set after saying it (now first; the battery task 6 KB). Nits taken: the sleep's result checked, a layout-bound RTC magic, `why=calibration` on resume, bench notes.
**Board test** (0d5c581e on the walkie, 2026-10-13 01:09-01:13Z, cell 3.71 V): a normal boot didn't park; `battery park 3300 2` restarted into the park (`codecs.power_down dac_fails=0 adc_fails=0`, `battery.parked mv=3300 why=test`), off the network (no ping), two 60 s light sleeps, `battery.resume why=test_over parked_s=121 wakes=2 lever_wakes=0`, back on the server through the tunnel at 01:12:44 (hello uptime 126 s: esp_timer ran across the sleeps; log timestamps, tick-based, did not). Left for a visit with the lever: a press while parked (the sentence), the 10 s escape, the light-sleep current in series.

### F-0031: Calls: the far end hears itself, 1.5 s late · in_progress 2026-10-13 (gate on at 50 dB since 2026-10-14; the next real call confirms)

**Problem** (Mario, relayed by the server session, 2026-10-13): during a walkie call he hears himself on his phone. **Evidence**: the recorder's two legs of the 01:53:26Z call (his voice to the walkie; the walkie's mic back), 20 ms loudness envelopes correlated: a peak at +1250 ms (corr 0.58, as strong as the direct pickup in the room), nothing between 400 and 830 ms. The walkie plays the call's downlink about 1.25 s after the server sends it, and its mic picks the speaker up.
**Why**: the mic streams continuously during a call (`input_mode skill_continuous`, decisions 2026-10-11, which listed echo cancellation as not done). The 1.25 s is the speaker's rebuffer target (F-0022: 1 s of audio before playing on after a dropout, right for streams), which in a call adds that much to every exchange and to the echo.
**Design** (gate 2):
- Call jitter buffer: while the mic is continuous, the rebuffer target is 240 ms instead of 1 s (VoIP's usual 200-300 ms; a bad link stutters more, a call answers 1 s sooner). Back to 1 s when the call ends.
- Echo suppressor, calls only: the ES7210's channel 0 is the speaker's own output looped back, sampled in the same TDM frame as the mic (no delay to estimate). Per 20 ms frame: the reference power with a decaying hold (the room's tail, about 60 dB in 300 ms); the echo coupling learned as a slowly rising minimum of mic/reference power while the reference is active (a near-end talker only raises the ratio); predicted echo = coupling x held reference. Mic power over 4x the prediction (+6 dB) is the near end talking: pass. Otherwise, while the reference is active: -30 dB. The gain closes at once and opens over about 40 ms, ramped across each frame (no clicks). A pure, host-tested function (`hux_echo.c`); the mic task applies it; `audio.echo` stats once a second while active (reference level, coupling, suppressed share), so the next call's log shows it working.
- Not now: full echo cancellation (esp-sr's AFE: 16 kHz only, our path is 24 kHz Opus, a new dependency). Half duplex is what speakerphones without AEC do; double talk passes the echo along with grandpa's voice. Revisit if a quiet grandpa gets cut off.
**Design critic** (fresh, 2026-10-13): build it differently. Blockers: (1) the 240 ms target only sets the refill after an underrun, and nothing ever takes audio out, so a stall's backlog (TCP turns a lost Wi-Fi frame into a 250-400 ms stall; the server forwards what piles up) stays for the rest of the call: a cap that trims back is needed; (2) a minimum-tracked broadband coupling is biased low (bass and plosives in the reference that the small speaker can't play, clipping, a quiet mode ending mid-call), so the echo would pass as the near end; (3) the planned bench check was a tautology (with the amp off the reference is active and the mic hears nothing, so "suppressed" passes with a broken suppressor). Should-fix: a kill switch and a measure-only mode, shipped first; at high volume the echo is likely 20-30 dB over a quiet elderly voice, so double talk would mute grandpa rather than leak echo; "speaker active" relative to the line's own floor; -30 dB may not be enough at volume 100 (comfort noise); the reference may clip at high volume; richer stats. Alternatives: esp-sr AFE is viable (Opus can encode a call at 16 kHz), SpeexDSP's canceller is BSD and runs at 24 kHz; server-side rejected (variable delay). **Taken**: the call jitter buffer with a cap now; the suppressor ships in shadow; one real call's numbers choose between a gate (with comfort noise) and real cancellation.
**Built** (v2): call jitter buffer `calljb` (240 ms, NVS), trimmed back to it above +160 ms in a quiet chunk within 2 s (`spk.call_trim dropped_ms= ring_ms= quiet=`); `hux_echo` measures on 300 Hz high-passed signals, counts the speaker active 10 dB over its own channel's floor (held 300 ms), keeps a histogram of mic/speaker power over active frames, peaks and clips; modes `echo off|shadow|on` (NVS, shadow by default; on = gate the mic -30 dB while the speaker is active); `audio.call on|off rebuffer_ms= echo=` and every 5 s `audio.echo mode= frames= active= ratio_db_p10= p50= p90= ref_floor_db= mic_peak= ref_peak= mic_clips= ref_clips= vol= pa= ring_ms_min= max= trims= jb_ms=`. Host tests: far end gated 25 dB+, shadow untouched and measuring the room's coupling, near end untouched, a hissing line doesn't gate, clips counted, no click.
**DoD**: host tests (far end alone suppressed; near end alone passes; near end over the far end passes; learning from a cold start; no click at a gain change); on the board, a silent bench check: a tone through the speaker with the mic continuous, suppressed share near 100 %, and the call buffer at 240 ms (`spk.underrun ... rebuffered ring_ms=`); a real call with Mario after, checked with `scripts/call_echo.py <date/HHMMSS-conn>` (the server session's tool: both legs from the recorder, correlation from -0.5 to +3 s; the 01:53Z call gives 0.58 at 1247 ms; done = no peak after 0 ms, or under 0.3).
**First real call measured** (2026-10-14 05:44:43Z, Mario: "I talk and then I hear myself back like a second later"; the server session, silently from the recorder's two legs, `call_echo.py 2026/10/14/054443-4bcce2c6`): the echo peak 0.79 at 531 ms (the 240 ms call buffer worked: was 1250 ms), the suppressor in shadow changing nothing. Replaying the `on` rule offline on those legs: while the speaker plays the mic sits at -16.2 dBFS, 50.7 dB over its -66.9 floor, against -39.4 with only the room talking: the echo is about 23 dB over a near-end voice (the critic's 20-30 dB). A -30 dB gate leaves it at -46 dBFS, 20 dB over the floor, audible on his phone; -50 dB puts it at the floor. The speaker was active 66 % of that call.
**Shipped as default** (2026-10-14, the server session's recommendation, standard speakerphone practice; reversible with `echo shadow` / `echo depth`): `echo on` by default, depth 50 dB (`echo depth <dB>`, NVS, 20-70, in `audio.call` and `audio.echo`), the 300 ms hold kept, no double-talk pass (half duplex: one at a time, like the lever; a quiet grandpa talking over the caller is muted rather than the caller hearing themselves). Host test: a -16 dBFS echo in speech-like bursts comes out near the room's floor. Done when the next real call's legs show no peak over 0.3 after 0 ms (`call_echo.py`).
**Measured on the speaker** (2026-10-14 ~12:25-12:31Z, Mario allowed noise; bench `call on`: call mode with the mic read for the gate only, no uplink; volume 50): the mic hears the speaker 13-20 dB above the reference channel (it clips on speech), the reference's floor -85 dBFS. With the speaker counted active from -70 dBFS (02975ebc; was -50: a chime's quiet tail passed, 66 % gated): tone 100 % gated (161 frames for 150), speech 100 % (63 / 63 played), chime 86 % (48 / 56: its bell tail under -70); release (speaker stops -> mic open) 80-280 ms. (A queued sentence plays only ~1.3 s in call mode: the call buffer trims a backlog over 240 ms + 160; `spk_ms=` in audio.echo now shows what played.) Real calls still to confirm with `call_echo.py`.

### F-0032: The walkie tells Mario's phone itself when it can't reach the server · in_progress 2026-10-13

**Ask** (Mario, relayed by the server session, 2026-10-13): an alert when the walkie can't reach the server, free. The server session's Cloudflare Worker can't deliver: ntfy.sh answers Workers `429 42908 daily message quota reached` (anonymous limits are per IP; Workers share Cloudflare's). The walkie has its own IP (the home's). The page already alerts when the walkie is offline and the Pi is alive; this covers the Pi, its power, the house internet or the tunnel being down while the walkie's own internet works.
**Design**: `hux_alert_logic.c` (pure, host-tested): "needs you" when the server has been unreachable 10 min (11 after review) with an IP (the server's restarts and deploys take under 1 min); "fixed" once it's back, only after a down went out; never repeated while the state is the same (state in RTC memory: the ladder's hourly reboot doesn't repeat it; a power cut forgets it); quiet hours 22:00-07:00 Bogota (UTC-5), held, sent at 07:00; a failed POST retried every 2 min; no wall clock (SNTP not synced): no quiet hours, "since ?". `hux_alert.c`: SNTP (pool.ntp.org), a task ticking every 30 s, an HTTPS POST to ntfy.sh with Title / Priority / Tags / Click (the watch page), `alert.sent kind=down|fixed|test status=` as a warning (the black box carries it). The topic (the same as huxley-watch's `ntfy_topic`) is a secret: `HUX_NTFY_TOPIC` in the gitignored secrets.h, never logged; none set, alerts are off (`alert.disabled why=no_topic`). Console `alert test`: one low-priority push, end to end, run on the alert task's stack.
**Code critic** (fresh, 2026-10-13): no blockers; seven should-fixes, all taken: an uptime wrap after 25 days stalled the down until the hourly reboot (now a retry flag and unsigned differences; a wrap test); a server that connects and crashes over and over never counted as lost (now up only after 2 min on one connection, `hux_net_server_lost_ms` from the last steady up); "fixed" held overnight left a stale "needs you" (now sent at once, a silent low-priority push at night); a `server <dead uri>` bench switch would page the phone (a test switch, `SRV_TEMP`, never counts); the internal heap with a second TLS session unmeasured (`alert.sent` now carries `heap_internal_min= largest= stack_free=`); held alerts and the outage's start unlogged (`alert.held why=no_ip|quiet_hours|retry_wait`, `alert.server_lost since=`, `err=` on a failure); a power cut after a down lost its "fixed" (the state also in NVS, written on transitions). Nits taken: the topic's characters checked (a URL that fails to parse is logged whole by the HTTP client), 11 min (the ladder's Wi-Fi step is at 10), retries 2 min doubling to 30, a fallback for the click URL. Left: the HTTP client's event-post timeout (sdkconfig regeneration, not worth it now); the task stays resident (8 KB internal).
**On the walkie** (304c5d5f, 2026-10-13 22:47Z): `alert.started sntp=ESP_OK down_sent=0`; `alert test` reached Mario's phone (`alert.sent kind=test status=200`). **Heap**: with the alert's TLS session open beside the WebSocket's, `heap_internal_min=25615 largest=31744`: tight. An OTA pull at that moment would be a third session; follow-up in F-0028 (mbedTLS buffers to PSRAM or dynamic buffers). The 14 min dead-server test is the server session's to run.
**DoD**: host tests (11 min with an IP, never without one, once then fixed once, a short outage says nothing, quiet hours hold a down until 07:00, a fixed at night goes at once, retries back off to 30 min, the uptime wrap, no clock no quiet hours); on the walkie `alert test` arrives on Mario's phone; the server stopped inside a bench window for 14 min: one "needs you", one "fixed", `alert.sent` in the black box.

### F-0033: The walkie says its connection state with sounds · built 2026-10-14 (3c3ba563; board run passed 05:47Z; Mario's ear pending)

**Ask** (Mario, through the server session, sounds approved 2026-10-14): startup, searching, connected, lost, as sounds the walkie plays on its own. Files final on main 11d9ad4a: `server/personas/_shared/sounds/device/{startup,searching,connected,lost}.wav` (PCM16 24 kHz mono; cues at -19 LUFS momentary max; the startup's sparkle reaches 5.3 kHz: check it survives the Opus round trip).
**Behaviour** (the server session's, from standard practice; Mario saw the tick schedule and kept it): power on: startup, then searching. Searching (no server): a tick every ~3 s for 2 min, then every 30 s, stop at ~17 min. Lost: only after 20 s unreachable (deploys and restarts take 3-20 s), once, then the searching schedule from its start. Connected: on the first connection after power on and after a lost, never for a silent blip. Night 22:00-07:00 Bogota (when the clock is known): lost, ticks and connected silent; a press and power on still sound. Never over playback or during a press. A press while searching plays the offline prompt, as now. Logs `sound.status kind=startup|searching|connected|lost|held_night` and the state machine's transitions.
**Design note**: one "server unreachable" state machine for this and F-0032's alert (thresholds differ: 20 s here; 11 min and 2 min steady there).
**DoD** (Mario: "make sure they work well"): unit tests for the state machine (blips under 20 s silent, the schedule, night hold, press while searching gives the offline prompt); a silent bench run whose log shows each `sound.status` at the right moment (power on, Wi-Fi off and on, a server restart); offered to the walkie in a bench window after the idle wait; then Mario hears them.
**Built** (2026-10-14):
- Sounds: `render_prompts.py --sounds` encodes the four WAVs with the server's Opus encoder (24 kHz, 60 ms) into `components/hux_prompts/prompts/{startup,searching,connected,lost}.hxp`, embedded like the sentences (0.9, 0.36, 1.62, 1.32 s; 16 KB together). After the round trip the startup's 4.5-6.5 kHz sparkle band is -35.6 dB against -35.7 before: it survives.
- Rules: `hux_status` (pure, in hux_app), stepped by the app loop (at least once a second). Beyond the spec: any boot but a real power on or a charge after the battery park (an update, a crash, the ladder's hourly restart in a long outage) comes back silent until a new drop, so a restart at 3 am never starts ticking, but a `connected` owed from before it is kept in RTC memory and still plays; an outage ends only with a connection that lasts 2 min (a crash-looping server gives one `lost`, one `connected`, one schedule); a `connected` still blocked 3 s after the connection is let go (a resumed book or a press already says it); evicted (another client took the server on purpose) is not an outage. Busy: the decoder not up yet, the lever held (offline too), a press, a call, the model speaking, audio in the ring or the decoder queue, or the speaker's last sound under 800 ms ago. A clip goes into the decoder whole (`hux_opus_clip_begin/end`; the WebSocket task's packets and clears wait on the same lock), so `connected` can't interleave with a book resuming. A sound played while the link is down fixes the next hello's after-drop figures first, as a press's offline cue does (protocol.md updated). SNTP now starts at boot even without an alert topic (the night needs the clock).
- Design note answered: the sounds key on the app's READY (the server's hello) and F-0032's alert on `hux_net_server_lost_ms`; both read the same link, with their own thresholds and the same 2 min "stable" idea. One shared machine would carry both sets of timers for no gain.
- Console `status poweron` starts the sounds over as after a power on (the bench can't cut the power). Logs `sound.status.init`, `sound.status.phase ... outage=`, `sound.status kind=... played= n=`, `sound.status kind=held_night|let_go_busy sound=`, `sound.status.ticks_done n=`; ticks thinned to the first and every tenth so an outage's 256-line log queue keeps room (debugging.md).
- Tests: `test_hux_status` (16: power on, blip, back-to-back blips, lost then ticks then connected, the 3 s/30 s/stop schedule and its end flag, night, busy, busy at night, the let-go, a short sound, a drop while `connected` waits, a crash loop for 2 h, the uint32 wrap, a software restart with and without an owed `connected`); `test_hux_prompt_parse` checks the four shipped files.
- Critic (fresh agent, 2026-10-14), all taken except two nits: BLOCKER crash loop beeping forever (205 lost / 206 connected / 615 ticks in 2 h on its probe) → the 2 min stable rule. SHOULD: lever held while offline not busy → tracked; owed `connected` lost on restart and park resume treated as a software reset → RTC flag, `hux_park_at_boot` returns whether it parked; `connected` interleaving with server packets → the clip lock; night silence depended on the alert topic (SNTP) → SNTP always; tick logs crowding the outage queue → thinned. Nits taken: the held reason (`held_busy`), freeze keyed on the link (an evicted walkie's `connected`), a comment on `rx_reset` counting queued sound packets as discarded (over, the safe side). Nits left: a `lost` held at night isn't replayed if ticks resume at 07:00 within the 17 min (the ticks themselves say it); the drop-sound-reconnect-drop re-baseline (pre-existing, errs safe).
- Press while searching gives the offline prompt: unchanged code path (`enter_ptt`), checked on the board by the bench (`talk` during the server drop).
- **Board run** (2026-10-14 05:47-05:56Z, b24eb0e9, amp off, night: the held sounds log `held_night` at their moments): `status poweron` -> startup played (silent), Rainbow `heard=1`, `connected` held 2 s later; a 6 s Wi-Fi blip -> connected->dropped->connected in 12 s, no sound; a 1 min dead server -> `lost` at 20.9 s, a tick 3 s later, a press in the outage gave the offline sentence and Glow with no tick during it (busy deferred them: 9 ticks in 41 s), `connected` on return; a Wi-Fi drop 2.7 min later (a stable connection, so a new outage) -> `lost` at 20.9 s, ticks every 3 s, `connected`; `battery park 3500 1` -> parked ~1 min, booted as a power on (`power_on=1`): startup, Rainbow, a tick, `connected`; the LEDs cleared before the park (`led.started` ahead of `battery.parked`).
- Pending: Mario hears them in daytime.

### F-0039: An offline press says what to check · built 2026-10-14

**Ask** (Mario, through the server session): "we should have more precise messages for my grandpa. He really can't fix stuff by himself... when it gets no internet access, it says so, and tells him something like make sure that your phone is charged and that the hotspot's on." The walkie runs on his phone's hotspot ("Rafael").
**Built**: two stored prompts in the persona's voice (`render_prompts.py no_wifi no_internet`, both transcribed back word for word): `no_wifi` "No encuentro el internet de tu teléfono. Revisa que el teléfono tenga batería y que la zona wifi esté prendida, o pídele ayuda a alguien." (10.5 s, -20.6 LUFS) when there's no IP; `no_internet` "Tu teléfono no me está dando internet. Revisa que tenga datos, o pídele ayuda a alguien." (6.9 s, -20.0 LUFS) when there's an IP but the last WebSocket attempt broke below HTTP (`hux_link_fail_kind`: a transport error with no HTTP answer, DNS, TCP or TLS to Cloudflare, which always answers when there's internet; no public-host probe needed). "No tengo conexión" stays for an HTTP answer (the tunnel's 530) or a server close (Mario's alert covers that). "Me estoy actualizando" keeps priority. Pure and host-tested (`hux_link_fail_kind`, `hux_status_offline_kind`); logs `press.offline_prompt which= why= have_ip= link_fail=`, `ws.fail_kind`. Docs: debugging.md.

### F-0038: The walkie retries a declined update by itself once idle · queued 2026-10-14

**Problem** (986a52d9, 2026-10-14 18:05Z): an offer that finds the walkie in use is refused (`ota.status ... phase=failed error=busy`) and the server re-sends offers only at the next hello, so an update waits for a reconnect: hours if he listens to a book or the radio. Today a Mac-side loop re-offers at each idle moment (no press for 2 min, nothing playing).
**Design (draft)**: on `error=busy` the walkie remembers the offered version, url and force flag (RAM: a restart drops it, the next hello re-offers) and pulls it once it has been idle N minutes (no press, nothing in the ring, no call), never 22:00-07:00 Bogota when the clock is known, at most a few tries; a withdrawal (or a new offer) replaces or drops it. Logged `ota.deferred version= why=busy` and `ota.retry version= idle_ms=`. No server change (the server session agrees).

### F-0037: A press during an update says it's updating · done 2026-10-14 (986a52d9, on the walkie)

**Ask** (Mario, through the server session): "Can we add a message when the device is updating like 'Mario está actualizando el dispositivo, intenta luego' or something better". A press during an update played "No tengo conexión", which sounds like something broke.
**Built**: a stored prompt, "Me estoy actualizando. Intenta otra vez en un minuto." (the server session's wording; `render_prompts.py updating`: 5.0 s, -20.3 LUFS, true peak -5.3 dBFS after Opus, transcribed back word for word). `hux_status_updating` (pure, host-tested) picks it over "No tengo conexión" while the walkie's own update downloads or verifies (`hux_ota_busy`; the press doesn't reach the server: the restart would cut the answer), while a new image waits for acceptance (`pending`, for 3 min after boot at most), and for 90 s after the server closes for a restart (`1012`, or `1001` with no reason, websockets' shutdown close; an eviction carries a reason, a crash sends no close) until the next hello. Night rules unchanged (a press is always answered). Logs `press.offline_prompt which= why=`, `ws.rx.close ... restart=`. Docs: debugging.md, protocol.md.
**Shipped**: offered over the air at Mario's "Install ASAP"; the first offer (18:05Z) and a retry (18:09Z) were declined `error=busy` (in use), the next at an idle moment accepted at 18:17:59Z (that gap is F-0038). Lesson: a watcher must treat `error=busy` as "later", not a failure.

### F-0036: Add a Wi-Fi network from afar (T1.52's firmware half) · design v2 2026-10-14 (critic taken; the rescue network is Mario's call)

**Problem** (T1.52, the server session's audit at the handover): everything about the walkie updates from afar except its Wi-Fi list: `wifi addhex` and `forget` are refused off USB (`hux_console.c` run_line), and the walkie now lives at the retirement home where nobody can plug it in. A new router or password there strands it.
**The catch**: the only remote channel is the walkie's own WebSocket, so a remote add needs the walkie online. It covers (a) pre-staging: the home announces a new router or password, we add it while the old one still works; (b) the walkie on the rescue hotspot, adding the home's network; (c) a second known network. If the old network dies first with nothing staged, only the hotspot (or a visit) helps.
**Design (draft)**:
1. Where a line comes from: USB, the LAN developer door (`hux_dev`, plain HTTP + token, unreachable from afar), the server's `dev.cmd` (wss with the device token; the Pi's admin token in front). `wifi addhex` accepted from USB and the server, still refused from the LAN door. `wifi forget` stays USB only (a wrong forget strands it).
2. Two kinds of add: `wifi addhex <ssid> <pass>` joins now for a trial, as today (no IP in 30 s: back to the previous network); from the server it is kept only if the server answers within 90 s (a remote operator loses the walkie on a network with an IP and no route), on USB it stays "IP is enough" (the operator is there). New `wifi addhex <ssid> <pass> later`: saved without joining (a staged entry), tried by the ranking only when heard, never `good` until it reaches the server.
3. Secrecy, unchanged rules extended: the line is never logged (console.cmd `<redacted>`, the dev.cmd receipt logs only its id, the result echo is the captured, redacted log), never in the black box; the line buffers wiped after the run (the parsed event, the JSON message) where we own them.
4. A USB add that gives an IP and no route stays saved; the no-server ladder (the Wi-Fi step at 10 min, then the next heard every 10 min) moves back to a network that works (the ranking prefers `good`).
5. T1.52 item 2 already holds: any software restart (an update's included) plays no startup and no `connected` (F-0033: only a power on or a charge after the park).
**Open questions for the critic**: a password change on the same SSID (staged while the old password still works); removing a wrong staged entry from afar (forget is USB only); testing without a spare board (the only board is grandpa's walkie, reachable only through the Pi).
**Critic** (fresh agent, 2026-10-14): the draft closes only an announced change. BLOCKERS: (1) an unannounced change (the ISP swaps the router, staff rotate a password) has no fix from afar: the only compiled rescue is Mario's own phone hotspot, not at the home, and never proven in the field; fix: a compiled rescue network whose name and password we choose, so any phone at the home can make it, on a card for staff or family (who holds it is Mario's call), plus one real hotspot join; (2) a password change on the same name is impossible (`why=known`, forget is USB-only), and a compiled entry shadows a saved one for good; fix: up to two passwords per name, the other tried after 3 auth failures in a row (rate-limited: managed APs may block a device), `good` remembers which password reached the server, saved before compiled; (3) a failed remote trial deletes the entry (`wifi_add_tick`), so a tunnel blip during the trial throws away a network that works; fix: revert the connection, keep the entry staged, never delete automatically. SHOULD: `run_line` keeps 4 tokens (a 5th `later` would vanish); the dev.cmd line is capped at 128 (`hux_proto.h`), a long name plus password is refused, raise to 256; the add runs on the supervised app task with an untimed `link_lock()` and its reply goes out after `esp_wifi_stop` (lost), so the join belongs to the ladder task with an immediate `queued` reply and the outcome after the next hello; remote forget for any entry but the active one (else staged mistakes fill the 4 slots); the operator side must never put the password in a command line (the server session moved it to stdin, e6d7cbb6); grandpa hears a daytime trial (`lost` at 20 s, ticks): refuse while busy, during an update or `pending_verify`, add at night; wipe the dev.cmd copies we own (the parsed line, the message buffer). NITs: requiring the server for remote adds doesn't conflict with F-0027's objection; a Pi outage's rotation may hop onto staged entries (bounded).
**Design v2** (the critic's minimal design): from the server, `wifi addhex <ssidhex> <passhex>` always saves the network as staged (no `later` flag needed), answers `queued` at once, and the ladder task tries it now only if a scan hears it and the name is new to the list; a failed try reverts the connection and keeps the entry; a name already in the list gets the password as its second one (never overwriting one that reached the server). `wifi forget <ssidhex>` from the server for any entry but the active one. dev.cmd lines up to 256. Refused while busy, while an update downloads or waits for acceptance. USB keeps today's behaviour plus the second password. Tests: host tests for the ranking with two passwords, the flip after 3 failures, staged never `good` until the server, the source policy, every secret command redacted, the NVS layout read by the previous image; then on grandpa's walkie at night, only zero-risk steps (stage an unheard fake name with a marker password, `wifi list`, search the Pi's logs and black box for the marker, forget it from afar).
**Decided** (Mario, 2026-10-14): no rescue network. A router or password change at the home is covered by a warning ahead (a staged add from afar) or a visit with the laptop.
**Progress**: the dev.cmd line now holds 255 characters like a USB line (protocol.md's admin route already allowed 256; test_hux_proto). Nothing is enabled from afar until the rest of v2 lands: today's add path from the server would hit blockers 3 and 6.

### F-0035: The walkie restarted itself during the hotspot field test (task watchdog) · in_progress 2026-10-14 (guard shipped; the cause not reproduced)

**Evidence** (found in the handover checks, 2026-10-14 ~06:00Z): device.hello carried a core dump from 312e4182, first reported at the 02:04:54Z power-on right after the field test (F-0027), so the walkie panicked once out there, offline at the edge of Wi-Fi. Decoded with the archived ELF (`build/elf/312e4182-ae84979dc.elf`): "Task watchdog got triggered: IDLE1 (CPU 1)" (10 s), the task running on the panicking CPU `hux_opus_dec` in `denormalise_bands` (CELT decode), IDLE1 itself in `esp_cpu_wait_for_intr` at the dump. The restart's own log was lost with the black box when Mario used the rocker. Raw dump kept in the firmware session's scratchpad.
**Why it matters**: grandpa's walkie will sit at the edge of a retirement home's Wi-Fi; a starved core restarts it (the supervisor and the watchdog recover it in ~7 s, silently, no startup sound: not a power on), but the cause is unknown.
**Guard** (7b260927): the decoder (priority 7, above the app, the WebSocket and idle) yields a tick after 100 ms of work without waiting (`opus.yield run_ms= waiting= count=`, thinned); playback needs ~20 % of a core, so normal streams never hit it. Bench: `opus yield on|off`, `opus flood <s>` (speaker held, a stored sentence queued again and again: the decoder busy all along).
**Repro attempt** (06:07Z, amp off): guard off, `opus flood 15`: 20 sentences, ~1100 packets decoded in 15 s (the decoder at 100 % of a core), no restart: a busy decoder alone doesn't starve an idle task (the scheduler moves it between cores). So the field panic needs something else on CPU 1 at the same time (Wi-Fi scans and reconnects at the edge of range are the suspects); the guard stays as insurance, the cause stays open.
**Next**: catch it with the black box intact: on the next field test, no rocker until the walkie is back on a network; read `super.boot reset=` and the black box. A tight Wi-Fi edge on the bench (the router attenuated, or `wifi join` to a far AP) while a stream plays and the lever is pressed.

### F-0034: LED animations through the clear back, at boot and while the lever is held · in_progress 2026-10-14 (on the walkie, dda50f32; critic fixes built)

**Ask** (Mario, through the server session, 2026-10-14): light visible through the HX-1's clear back, for onlookers (grandpa can't see it), at boot and while the lever is held. Mario is choosing between mockups; the server session sends the pick. Nothing to build until then.
**Hardware** (the server session's notes, pin checked here): the board's 7 WS2812B on GPIO38 (`bsp_board.h` `LED_STRIP_GPIO_PIN 38`, `LED_STRIP_LED_COUNT 7`), underside, ring r = 22 mm, LED i at board angle i x 360/7 from board +x. In HX-1 board +x is device down (`hx1.py` BOARD_LOC): LED 0 points straight down; mirror pairs (1,6), (2,5), (3,4); height steps bottom {0}, low {1,6}, mid {2,5}, top {3,4}. Through the back only LED 0 (the channel between the holders) and 3,4 (above the holders) show directly; 1,2,5,6 sit behind the battery holders and show as spill. All seven are above the grey/clear seam (36 mm).
**Candidates**: boot "Rise", one step per startup chime note (LED 0 at 0 ms, 1+6 at 60, 2+5 at 120, 3+4 at 135, then a soft whole-ring glow fading by ~1.2 s), started with the startup sound so the light climbs with the pitch. Lever "Glow" (soft steady glow in ~110 ms, out ~240 ms after release) or "Voice" (while held, how high the light climbs follows the mic level, ~70 ms smoothing: bottom first, top only when loud).
**Constraints**: low brightness; never delays audio or the press path; at night (22:00-07:00 Bogota, when the time is known: `hux_alert_quiet`) a third of the brightness; off whenever idle. Log `led.anim kind=boot|press mode=`.
**Scoping notes** (firmware, 2026-10-14):
- GPIO38 is unused by our firmware and nothing drives the strip today; it needs `espressif/led_strip` (RMT backend; the S3's RMT is otherwise unused). A low-priority LED task fed by atomic flags keeps it off the app and audio paths; 7 LEDs refresh in well under 1 ms.
- "Rise" timed to the sound must start when the startup sound reaches the speaker, not when it is queued: the decoder and I2S add a fixed delay (measure it with the loopback, `selftest`). Decided 2026-10-14 (with the server session, standard practice): the boot light follows the startup sound's rule exactly (F-0033: a real power on or after the battery park); updates, crashes and the ladder's hourly restart stay dark as well as silent.
- "Voice" can read a smoothed level the mic sink already computes per frame (`mic_sink` peak/RMS in hux_app); the mic only runs while the press is acted on, so a press the walkie can't act on (offline) falls back to "Glow" (decided 2026-10-14).
- Battery: WS2812Bs draw about 0.5-1 mA each when dark if their rail is always on. That's already the case today whatever we animate, but worth a reading with F-0029's idle drain work; low brightness for ~1 s at boot and during presses adds little.
**DoD** (draft): the pick implemented; host test for the animation timing (pure frame function: time -> 7 RGB values, the night scale); a silent bench run whose log shows `led.anim` at boot and press with no change in press-to-`ptt_start` or audio timing; then Mario sees it.
**Mario's pick** (2026-10-14, his mockup artifact "Walkie lights"): startup "Orbit" in warm white (255, 206, 158); lever "Voice" in white (236, 240, 255); offline presses "Glow", white. b = 1 is 25 % of full to start, tuned by eye; night a third.
**Built** (2026-10-14): `hux_led` component. `hux_led_anim` (pure) is the mockup's JS ported formula for formula; `test_hux_led_anim` checks it against reference values computed from the mockup's own JS in double precision (14 Orbit instants, Voice held and released, Glow), plus mirror symmetry, the end at 1.4 s, the mic mapping and the pixel scale. `hux_led.c`: led_strip 3.0 on the RMT with DMA (no interrupt timing under Wi-Fi), a priority-2 task that renders 16 ms frames only while a light shows and sleeps on a notification otherwise; every setter is an atomic and a wake. Orbit starts when `hux_audio_played_ms()` passes its value from before the startup sound was queued, plus 60 ms (the I2S DMA's 6 x 10 ms ahead of the chunk), 1 s timeout. Voice reads each mic frame's mean square from `mic_sink` (no extra work per sample), stale after 100 ms. Boot and press together: the brighter per channel, as the mockup. Console `led bright <pct>` (NVS), `led demo orbit|glow|voice`. Logs in debugging.md.
**On the walkie** (2026-10-14 ~04:27Z, at Mario's ask: "flash it with the new sounds and with the lights"): ea106a69, then dda50f32 minutes later. The first boot log showed `led.anim.end kind=press ms=0`: a press from dark woke the task with no time gone, the level stayed 0 and the light ended before its first frame; fixed (out only after release, the first frame after a wake moves 16 ms) with a regression test. `status poweron` then gave `led.anim kind=boot mode=orbit wait_ms=48 heard=1 night=1`.
**Critic** (fresh agent, 2026-10-14): the blocker was the same press-from-dark bug (already fixed). SHOULD, all taken: DMA with the default 48-symbol block still refilled from an interrupt mid-frame (Wi-Fi interrupts could garble a frame) → a 256-symbol DMA buffer holds a whole frame; a failed refresh was recorded as shown (the last frame could stay lit while the task slept) → retried ~0.5 s, logged; a restart mid-light followed by the battery park would leave the LEDs lit for hours → `hux_led_start` (which clears) runs before the park; 3 KB stack tight (~2.6 KB measured with logging) → 4 KB in PSRAM; the task logic untested → moved into a pure `hux_led_engine_step`, tested (boot wait heard/lag/timeout, demo, first frame from dark, mic staleness with the mic a tick ahead, demo expiry, sleep). NIT taken: the mic-staleness race (signed age). NITs left: Orbit ends at 1.4 s with the top pair still at 0.12 (the mockup does the same: an exact port); the power-on Orbit at night is full brightness until the clock syncs (no clock at power on; documented); `status poweron` during a reply could start Orbit early (bench only).
**Chain order** (2026-10-14, Mario: Orbit "looks kind of sideways"): the hardware notes assumed LED index i sits at i x 51.43 deg; the schematic chains GPIO38 -> U9 -> U19 -> U14 -> U13 -> U12 -> U11 -> U10, and Waveshare's STEP puts them at 102.8, 51.5, 0.3, 308.4, 249.5, 206.6, 164.2 deg (the server session's finding, frame checked against MEASUREMENTS.md). So chain index c sits at position (2 - c) mod 7: the bottom was chain 0 lit at 102.9 deg, Orbit rotated two steps. Fix: the animations stay in positions, `hux_led_chain` writes position p to chain index {2, 1, 0, 6, 5, 4, 3}[p]; test_chain_order pins it (the bottom drives index 2, pairs (1,3), (0,4), (6,5)). Console `led walk`: each position alone 0.6 s, bottom first, for Mario to confirm by eye. Lesson: a mapping from hardware is a fact to check (the walk), not a test to derive from the same assumption.
**Confirmed by eye** (2026-10-14, 8d3b30bf on the walkie): `led walk` at full peak goes once round the ring from the bottom (to the left, seen from the back). At night's third of 25 % the four LEDs behind the battery holders barely show, so the first look read as "down, up, then center": the order was right, the side LEDs too dim to see. Brightness back to 25 %.
**Mario's second look** (2026-10-14): Orbit read "up, then down" (the bottom blip under 0.1 s, the climb hidden behind the holders, the end glow relighting the bottom), so it was held 150 ms at the bottom with the end on the upper half; then Mario changed direction: "an effect using all the LEDs at the same time, maybe different colors", and for talking "just vary the intensity of the LEDs, all at the same time". Voice is now the whole ring, 15 % silent to full at -15 dBFS (built, tested). Startup: options mocked up for his pick (Bloom, Three notes, Sunrise, Rainbow, Aurora). From the server session: a gain for the four LEDs behind the holders (`led gain`, 3.0 default, NVS), so the ring reads even.
**Mario's pick, second round** (2026-10-14): "Use Rainbow for the startup light". Rainbow replaces Orbit: every LED its own hue (p x 360/7), turning 0.24 deg/ms, the whole ring fading in 200 ms, holding 700, fading out 700 (squared), saturation 0.8, 1.6 s; ported from the mockup widget's JS and tested against values computed from it.
**Mario's rule** (2026-10-14, at the home): "LED should only turn on when the device turns on and when the lever is down and someone's talking. Any other situation, the LED should be off." He saw a light on the whole time: the board's green charge LED (LED1 on the ETA6098 charger's STAT pin, top face next to USB-C), lit while charging, firmware can't touch it (hiding it is his call). Firmware to the rule: Voice now follows the voice from dark (no 15 % base: a held lever in silence is dark), the offline Glow is gone (an offline press is dark), Rainbow at power on unchanged; `led demo glow` removed. Offered over the air the same day at his ask: 21f4f554 (with the F-0027 scan fix) offered 16:39:44Z, downloaded in 18.8 s, self-test and manifest ok, accepted 16:40:42Z on ota_1; the restart silent and dark as designed.

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

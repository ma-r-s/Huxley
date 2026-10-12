"""Fit clearances for Mario's printer: Bambu Lab P2S, PLA, 0.4 mm nozzle, 0.2 mm layers.

Every number is a clearance PER SIDE (radial for bores), in mm. Mario's rule
(2026-10-10): everything must fit first time and stay put by design, no glue
(his one exception, 2026-10-11: the tub's two halves, GLUE).
So a bought part never gets a press fit straight into PLA; it gets enough
clearance to always go in, and crush ribs or the closing case take up the
slack so nothing rattles.

Print axis matters. A wall or bore whose axis is vertical on the bed is drawn
by X/Y motion and lands within about 0.05 mm. A bore lying horizontally comes
out oval (its height snaps to whole layers and its roof sags), so it gets
more. Faces that meet across layers (stacked in Z) can only be one layer
apart at best.

Starting points synthesised from published Bambu / PLA tolerance tests (no
P2S-specific data exists yet); confirm with one test coupon before the first
full print and adjust here, nowhere else:
- Bambu H2D Pro lab guide (sliding 0.15-0.2, free 0.2+, press 0-0.05).
- Sovol FDM tolerance guide (PLA sliding 0.15-0.25).
- Prusa / Bambu forum reports on horizontal-hole ovaling.
- Heat-set insert bores: M2 3.2, M3 4.0 (Accu / spec sheets).
"""

from __future__ import annotations

LAYER = 0.2

# Printed part against printed part.
# Coupon, 2026-10-10: Mario's 6 mm peg slid into every vertical hole, .10 to
# .30 per side, the .10 with a hint of friction. So .15 is free and true.
SLIDE = 0.15  # slides or rotates freely; both faces drawn in X/Y (vertical walls/bores)
# Lever-pin block, 2026-10-11: the printed 2.4 mm pin turns freely in the .20
# hole (horizontal, as the tub and the lever print it).
SLIDE_HBORE = 0.20  # same, but the bore lies horizontal on the bed (oval, sagging roof)
# Kept above the coupon's .10: it fits the faceplate's 64 x 100 mm lip, where
# shrinkage over the length matters more than a 6 mm hole shows.
SNUG = 0.12  # goes together by hand and stays (cap spigot, case tongue)
# The tub's glued lap: slides together dry (like SLIDE, over a 70 x 35 ring
# where shrinkage counts, as for SNUG) and leaves CA a film to fill.
GLUE = 0.15
STACK = LAYER  # faces that meet across layers: one layer is the finest real gap

# Metal pin or screw turning in a printed bore (lever pivot).
PIVOT = 0.25

# Bought part into a printed pocket: always goes in; ribs take up the slack.
PART = 0.30
CRUSH_RIB = 0.45  # rib height into the PART gap: 0.15 mm of interference, crushed on insertion
CRUSH_RIB_W = 0.8  # two perimeters wide so it deforms instead of snapping off

# Heat-set inserts: bore between the smooth tip and the knurls. From the
# FFVRVSS kit label (2026-10-10), matching Mario's caliper reading on the M3:
# M2 knurl 3.2 / tip 2.7, only 2 mm long; M3 knurl 4.5 / tip 3.9, 3, 5 or 7 long.
# Bore depth = insert length + 1 for the melt.
INSERT_BORE = {"M2": 2.8, "M3": 4.0}
INSERT_LENGTHS = {"M2": (2.0,), "M3": (3.0, 5.0, 7.0)}
SCREW_CLEAR = {"M2": 2.4, "M3": 3.4}
SELF_TAP_PILOT = {"M2": 1.7}  # into plain PLA, no insert
# Mario's Kadrick button-head kit (label photo): lengths in stock, plus nuts and
# flat/lock washers for every size.
SCREW_LENGTHS = {"M2": (4.0, 8.0, 12.0, 16.0), "M3": (6.0, 8.0, 12.0, 16.0)}

# Printability.
MIN_WALL = 0.8  # two perimeters of a 0.4 mm nozzle; anything thinner is not printed
FOOT_CHAMFER = 0.4  # bed-side edge chamfer on mating faces (elephant's foot)

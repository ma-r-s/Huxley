"""Shared 'board on top, facing up' head used by HX-2 Baton and HX-4 Can.

The board lies flat under a dot-grille top cap, rotated 180 deg so the mics
face the front (-Y) and USB-C exits through a flat facet at the back (+Y).
The 7 RGB LEDs on the underside light a translucent band ring below the board.
"""

from __future__ import annotations

from dataclasses import dataclass

from build123d import Cylinder, Location, Pos, Rot, Solid

from huxcase import hardware as hw
from huxcase.shapes import bbox_solid, hex_holes, hole_field

R_OUT = 35.0
T = 2.4
TOP_CLEAR = 6.0


@dataclass
class RoundTop:
    z_top: float

    @property
    def pcb_top(self) -> float:
        return self.z_top - T - TOP_CLEAR

    @property
    def pcb_bottom(self) -> float:
        return self.pcb_top - hw.PCB_T

    @property
    def plug_floor(self) -> float:
        """Lowest point of the BAT/SPK plug zone under the board."""
        return self.pcb_bottom - 9.0

    @property
    def board_loc(self) -> Location:
        return Location((0, 0, self.pcb_top), (0, 0, 180))

    def to_device(self, x: float, y: float) -> tuple[float, float]:
        return -x, -y

    def grille(self) -> Solid:
        pts = hex_holes(27.0, 3.0, 1.3, 4.0)
        return Pos(0, 0, self.z_top - T / 2) * hole_field(pts, 1.3, T + 1.0)

    def mic_boots(self) -> Solid:
        out = None
        mic_top = self.pcb_top + 1.0
        length = self.z_top - T - (mic_top + 0.5)
        for mx, my in hw.MICS:
            x, y = self.to_device(mx, my)
            b = Pos(x, y, mic_top + 0.5 + length / 2) * (
                Cylinder(3.0, length) - Cylinder(1.6, length + 1)
            )
            out = b if out is None else out + b
        return out

    def usb_facet(self) -> tuple[Solid, Solid, Solid]:
        """(material to add inside, flat facet to cut, port + plug recess to cut)."""
        z0 = self.pcb_bottom - 2.0
        add = bbox_solid(-18.0, 18.0, 28.7, 33.5, z0, self.z_top - T) & Cylinder(R_OUT, 400)
        facet = bbox_solid(-40, 40, 30.2, 40, z0, self.z_top + 5)
        zc = self.pcb_top + hw.USB_C_CENTER_Z
        port = bbox_solid(-4.8, 4.8, 28.0, 31.0, zc - 1.9, zc + 1.9)
        port += bbox_solid(-6.6, 6.6, 29.2, 31.0, zc - 3.6, zc + 3.6)
        return add, facet, port

    def slide_slot(self) -> Solid:
        """Recessed fingernail slot over the battery ON/OFF slide switch."""
        deg = hw.SLIDE_SWITCH_ANGLE + 180
        z = self.pcb_top + 0.7
        return Rot(0, 0, deg) * bbox_solid(30.5, 37.0, -3.0, 3.0, z - 1.6, z + 1.6)

    def bulkhead(self, below: float) -> Solid:
        """Disc that seals the speaker's back volume from the board zone."""
        return Pos(0, 0, below - 0.8) * Cylinder(R_OUT - T, 1.6)

    def ring_cuts(self) -> tuple[float, float]:
        """z of the two seams: band ring spans from lower seam to upper seam."""
        return self.pcb_bottom - 8.0, self.pcb_bottom

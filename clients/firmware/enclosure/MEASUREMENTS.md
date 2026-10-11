# Measurements: ESP32-S3-AUDIO-Board, speaker, 18650

Everything the case is designed around. Each number says where it came from.
The same numbers live as constants in [`huxcase/hardware.py`](huxcase/hardware.py);
change them there, not here, and keep this table in sync.

## Sources

| Tag         | What                                                                                                       | Where                                        |
| ----------- | ---------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| **STEP**    | Waveshare's official Creo assembly of board + speaker + original case, exported 2026-03-05. Authoritative. | `reference/drawing/ESP32-S3-AUDIO-Board.stp` |
| **PDF**     | Waveshare's dimensioned drawing (same archive)                                                             | `reference/drawing/ESP32-S3-AUDIO-Board.pdf` |
| **SCH**     | Schematic v1.1                                                                                             | `reference/ESP32-S3-AUDIO-Board_1.1.pdf`     |
| **MEASURE** | Not in any official file. Needs calipers.                                                                  | -                                            |

`./fetch_reference.sh` downloads all of these into `reference/` (gitignored:
vendor files, not ours to redistribute). Upstream pages:
[docs.waveshare.com resources](https://docs.waveshare.com/ESP32-S3-AUDIO-Board/Resources-And-Documents)
(has the STEP; the old wiki page does not link it) and the
[wiki](https://www.waveshare.com/wiki/ESP32-S3-AUDIO-Board).

## Board frame

Looking at the component (top) side: origin at the PCB centre, **+x** toward
the TF slot, **+y** toward the mics and the pin header, **-y** toward USB-C,
**z = 0** on the PCB top surface. STEP z maps as `z_board = z_step + 7.6`.

## Board (bare PCBA)

| Feature                            | Value (mm)                                                                                                                  | Source                            |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| Outline                            | Ø57.64 circle with two 11.0 mm flats: y = +28.31 (header side) and y = -28.23 (USB side)                                    | STEP                              |
| PCB thickness                      | **1.20** (not the usual 1.6)                                                                                                | STEP                              |
| Mounting holes                     | 3 × Ø2.90 (M2.5) at (-23.75, 0), (19.18, +14.00), (19.18, -14.00); all on a Ø47.50 circle. The one at (19.18, -14) is Ø3.15 | STEP, PDF (28.00 / 19.18 / 42.93) |
| Original standoffs                 | M2.5 hex, 4.6 across flats                                                                                                  | STEP                              |
| Tallest part above top face        | 5.68 camera connector (centre). Right-angle 2×9 header 5.0, USB-C 3.25, side keys 1.8                                       | STEP                              |
| Deepest part below bottom face     | 5.8 BAT + SPK connectors with plug mated. RTC connector 4.5, flash IC 1.75, LEDs 1.5                                        | STEP                              |
| USB-C                              | Mid-mount, opens toward -y. 8.94 × 3.26. Centre x = 0, z = +1.22. Front face at y = -28.40 (0.17 proud of the flat)         | STEP                              |
| Side buttons (5, right-angle tact) | Tips at r = 29.70, z = +0.6, every 13°: RESET -116.1°, BOOT -129.1°, KEY3 -142.1°, KEY2 -155.0°, KEY1 -168.0°               | STEP                              |
| Battery ON/OFF slide switch        | At -55.2°, knob out to r = 30.91, z -0.7 to +2.0                                                                            | STEP                              |
| Widest point incl. protrusions     | Ø61.8 at the slide switch, Ø59.4 at the keys, Ø57.6 elsewhere                                                               | derived                           |
| TF card slot                       | +x edge, card flush near x = 28                                                                                             | STEP                              |
| Mics (2)                           | Top face, centres (-17.28, 20.55) and (17.20, 20.52), 34.5 apart. No hole in the PCB under them, so top-ported              | STEP (port direction inferred)    |
| Ceramic antenna                    | Top face, (-25.7, 8.3), at the -x edge. IPEX connector at (-21.2, 15.6)                                                     | STEP + SCH                        |
| RGB LEDs (7 × WS2812B)             | Underside, on r = 22.0, every 51.43° starting at +x                                                                         | STEP                              |
| Charge LED                         | Top face, (9.4, -26.0), next to USB-C                                                                                       | STEP                              |
| BAT connector J1                   | Underside, centre (17.8, -6.0), 2-pin 1.25 mm, vertical                                                                     | STEP + SCH                        |
| SPK connector                      | Underside, centre (17.8, +5.2), 2-pin 1.25 mm (GH1.25 per SCH), vertical                                                    | STEP + SCH                        |
| RTC battery connector              | Underside, (-17.5, -6.0), 2-pin 1.0 mm                                                                                      | STEP                              |
| 2×9 pin header                     | 2.54 mm, **right-angle**, top face, x ±11.43, y 13.5 to 26.5; pins point to the +y edge                                     | STEP                              |

## Speaker (SPK-4020-5W, as shipped in the kit)

| Feature                          | Value (mm)                                                                         | Source      |
| -------------------------------- | ---------------------------------------------------------------------------------- | ----------- |
| Front frame                      | Ø40.0 for the first 2.6 mm                                                         | STEP        |
| Basket                           | Tapers to Ø31.6 at 12.3 deep                                                       | STEP        |
| Magnet                           | Ø21.8 from 12.7 to 19.0 deep; cap Ø18.8 to 20.5                                    | STEP        |
| Total depth                      | 20.5                                                                               | STEP        |
| Rating                           | 5 W (part name). Impedance not stated; the NS4150B amp is a 3 W / 4 Ω class-D part | STEP + SCH  |
| Back volume in the original case | roughly 40 to 60 cc, shared with the board                                         | derived     |
| Cable length, plug               | 97 mm, speaker to plug end (cut to 45 for HX-1)                                    | Mario       |

## 18650 cell

HX-1 uses Mario's two single-cell holders whole (photo 2026-10-10), so the
holder sets the space, not the cell. Mario measured it at 76 × 21 × 20.5;
the inside is assumed: 1.2 mm floor and side walls, 3.0 mm end caps, a bare
flat-top cell Ø18.6 × 65, one red and one black lead per holder (`HOLDER_*`,
`hardware.holder()`).

The older concepts (rev A, HX-2 to HX-4) still reserve a bare Ø19.4 × 74 bay
for a protected button-top cell.

The schematic shows a charger (ETA6098) and power-path MOSFETs but no
cell-protection IC (no DW01-class part); see "Battery protection" below.

## Electrical facts that shape the case

- **PTT wiring.** The LCD is unused, so GPIO3 to GPIO9 on the 2×9 header are
  free. An external PTT switch goes to one of them plus GND (header pins 15/16).
  The firmware reads it with the internal pull-up.
- **Battery level.** `BAT_ADC` is on GPIO1, so a "battery low" earcon is possible.
- **Charging through the header.** The header carries `USB_5V`, which feeds the
  charger. A pogo-pin dock could charge through it (not verified on hardware).
- **Antenna.** Keep metal (the 18650 can, the speaker magnet) at least ~10 mm
  from the ceramic antenna at the -x edge. Every concept does.

## Mario's parts

Calipers, Mario, 2026-10-10 (**MARIO**), or assumed generic (**ASSUMED**).

| Part | Value | Source | Constant |
|---|---|---|---|
| 12 x 12 tactile switch | 7.3 base to stem top; stem a 3.8 mm square | **MARIO** | `TACT_*` |
| | body 12 x 12 x 4.0, 4 legs, ~2 N / 0.25 mm actuation | **ASSUMED** | |
| Series spring (SciWil) | 0.4 wire x 4 OD x 15 free, 10 coils, ~0.59 N/mm | **ASSUMED** (kit label) | `SERIES_SPRING_*` |
| Lever pin | M2 x 16 screw + nut | **ASSUMED** | `_bar`, `pin` |
| KCD1-101 rocker | 18.6 from the frame's underside to the tab tips | **MARIO** | `ROCKER_DEPTH` |
| | 21 x 15 bezel, 19 x 13 body x 11.5, tabs 6 mm apart, rocker 5 mm proud | **ASSUMED** | `ROCKER_*` |
| 18650 holders (2) | 76 x 21 x 20.5 outside | **MARIO** | `HOLDER_*` |
| | 1.2 floor and walls, 3.0 end caps, 4.5 spring | **ASSUMED** | |
| 18650 cells | green wrap, probably Sony/Murata VTC: unprotected, flat top, 65 mm | **ASSUMED** | `CELL_*` |
| Speaker lead | 97 from the speaker to the end of the plug | **MARIO** | `SPK_CABLE_L` |
| Mics | pinhole on top of each can: top-ported, as the front mic ports assume | **MARIO** | `MICS` |
| M3 heat-set inserts | 4.4 over the knurls, 3.8 smooth pilot; 3, 5 and 7 long | **MARIO** | `fits.INSERT_*` |
| M3 button-head screws | 6, 8, 12, 16 long | **MARIO** | `fits.SCREW_LENGTHS` |
| Holder leads | black (-) out of the spring end cap, red (+) out of the plate end cap | **MARIO** (photo) | `lead_*` |

The speaker lead is ~75 mm longer than its 22 mm route, and loose wire buzzes
in a sealed back box, so the build cuts it to 45 mm and re-solders it at the
speaker (`SPK_LEAD_CUT`, tested).

## Battery protection

The board's charger (ETA6098) only manages charging. Nothing on the board
limits a short circuit or stops a deep discharge: the schematic shows no
protection IC (no DW01-class part), only the charger and two power-path
MOSFETs. Waveshare's own LiPo has its protection board inside the pack, which
is why the board omits it. Mario chose to skip it for now (pocket reserved in
the case). Bare 18650s properly need a 1S protection board
(DW01 + 8205 class) between the cells and the BAT socket, or protected
cells. Firmware can add a low-voltage shutdown via `BAT_ADC` (GPIO1), which
covers deep discharge but not shorts.

## Still to measure (Mario)

The rev D retention leans on these; each is a quick look or caliper reading:

1. **Holder side walls**: how high they reach (to the cell's centre line?) and
   how thick they are at the top edge. The lips and the bridge grip that edge
   (assumed 1.2 mm thick, at the cell's centre).
2. **KCD1-101**: the body behind the frame (assumed 19 x 13) and where its two
   snap ears sit.
3. **12 x 12 switch legs**: spacing between the leg rows (assumed 12.5 x 5.0,
   the common pattern); the cradle's leg slots follow it.
4. Did the board come with a 1.25 mm battery plug and lead? If not, the
   battery leads solder to the socket's pads.
5. The fit coupon results (see README "Fits").

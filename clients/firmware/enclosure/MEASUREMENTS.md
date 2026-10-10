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
| Cable length, plug               | **unknown**                                                                        | **MEASURE** |

## 18650 cell

Not measured: the design assumes the worst case, a protected button-top cell,
Ø18.6 × 70. A bare cell is Ø18.0 to 18.6 × 65. Each case reserves a
Ø19.4 × 74 bay (cell + spring contact + flat contact).

**Use a protected cell.** The schematic shows a charger (ETA6098) and
power-path MOSFETs, but I found no cell-protection IC (no DW01-class part).

## Electrical facts that shape the case

- **PTT wiring.** The LCD is unused, so GPIO3 to GPIO9 on the 2×9 header are
  free. An external PTT switch goes to one of them plus GND (header pins 15/16).
  The firmware reads it with the internal pull-up.
- **Battery level.** `BAT_ADC` is on GPIO1, so a "battery low" earcon is possible.
- **Charging through the header.** The header carries `USB_5V`, which feeds the
  charger. A pogo-pin dock could charge through it (not verified on hardware).
- **Antenna.** Keep metal (the 18650 can, the speaker magnet) at least ~10 mm
  from the ceramic antenna at the -x edge. Every concept does.

## Scavenged parts (assumed until measured)

| Part | Assumed | Constant |
|---|---|---|
| Mouse microswitch body | 12.8 × 5.8 × 6.5 mm, button 2.8 × 1.6 × 0.8, pretravel 0.3, overtravel 0.5 | `MS_*` |
| Pen spring | Ø4.0, cut to 12 mm free length, ~11 mm installed | `SPRING_*`, `SPRING_REST` |
| Adjuster screw | M2, self-tapping into the lever | `SCREW_PROUD` |
| Lever pin | Ø2 × 21 mm (M2 × 20 screw) | `_bar`, `pin` |

## Still to measure (Mario)

1. Speaker: confirm Ø40 front and 20.5 deep, and **measure the cable length**
   from the speaker to the plug.
2. Which 18650 (bare / protected, flat / button top), and how it connects:
   holder with leads, or a cell with solder tabs. Does the kit include the
   1.25 mm battery pigtail?
3. A glance at the mics: is there a pinhole on top of each metal can?
4. The mouse switch he pulls: body L × W × H and button height.
5. The pen spring: outer diameter and free length.

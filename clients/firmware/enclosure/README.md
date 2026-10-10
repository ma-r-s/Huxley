# Walkie enclosure (HX series)

3D-printable case concepts for the hardware client: the Waveshare
ESP32-S3-AUDIO-Board, its SPK-4020 speaker, one 18650 and an MX-style
push-to-talk switch. Hold to talk, release to hear the answer
([`../../../docs/concepts.md`](../../../docs/concepts.md)).

Status: **rev A concepts**, waiting for Mario's pick. Tracked as F-0010 in
[`../docs/triage.md`](../docs/triage.md).

| Concept      | Shape                                  | Talk button                              |
| ------------ | -------------------------------------- | ---------------------------------------- |
| HX-1 Classic | portrait brick, 66 × 35 × 125          | 2u MX bar + stabiliser, left side, thumb |
| HX-2 Baton   | round head on a grip, 70 × 70 × 148    | hinged trigger on the grip, index finger |
| HX-3 Radio   | landscape box + handle, 128 × 36 × 122 | squeeze bar under the handle rail        |
| HX-4 Can     | cylinder, Ø70 × 89                     | vertical 3u MX bar on the side           |

## Layout

```
huxcase/hardware.py      measured board / speaker / cell / switch (single source of truth)
huxcase/concept.py       Concept container + collision (fit) check + cavity volume
huxcase/concepts/hx*.py  one module per concept, Z up, front face at -Y
huxcase/export.py        build all, refuse on collision, write out/
render/render.py         Blender Cycles studio renders from out/*.glb
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
```

`out/` is gitignored; everything in it regenerates from the code.

## How the fit check works

Each concept declares packaging **envelopes**: the board with 5.9 mm top
clearance, 2.5 mm under it and a 9 mm plug zone under the BAT/SPK connectors;
the speaker solid; the Ø19.4 × 74 cell bay; the MX switch, stabiliser and
key travel. It also declares its printed **shells**. `collisions()`
intersects every envelope with every other envelope and every shell, and the
export refuses to write a concept that collides. Intended contacts (standoffs
touching the PCB, the switch clipped into its plate) are listed per concept
in `allowed`.

## What these models are not (yet)

Concept models. They prove packaging and show form, not a printable part.
Fasteners, snap fits, battery contacts, board standoffs in the round
concepts, print orientation and tolerances come in the detailed design of
the chosen concept.

# Waveshare ESP32-S3-AUDIO-Board — vendored drivers

Verbatim copies of the hardware driver sources from Waveshare's official demo for
the ESP32-S3-AUDIO-Board. Kept here so we have a clean baseline to diff against
when we adapt the code into our own `hux_audio` component.

## Source

- **Wiki page:** https://www.waveshare.com/wiki/ESP32-S3-AUDIO-Board
- **ZIP URL:** https://files.waveshare.com/wiki/ESP32-S3-AUDIO-Board/ESP32-S3-AUDIO-Board-Demo.zip
- **SHA256:** `e63823469cec26ac782b9e8a9f8af22d8c55015362fa8a4fb4e7212a17af149e`
- **Size:** 99,885,975 bytes (~95 MiB)
- **Downloaded:** 2026-04-24
- **Example taken from:** `ESP32-S3-AUDIO-Board-Demo/ESP-IDF/factory_01/main/`
- **Version / commit reference:** The upstream archive has no version tag,
  git metadata, CHANGELOG, or embedded version string in the vendored
  drivers. Waveshare publishes these as an unversioned ZIP; the only
  dating anchors are the ZIP's internal file mtimes (driver files dated
  2025-12-01, archive root dated 2025-11-01). Use the SHA256 above as
  the identity of this drop.

## Upstream license / notices

The Waveshare demo ZIP ships **no top-level** `LICENSE`, `COPYING`, `NOTICE`, or
`README` file at the archive root, and the `ESP-IDF/factory_01/` example that
these sources come from has none either. Nothing could be copied as
`UPSTREAM-LICENSE` — there is simply no upstream license file to preserve. The
bundled Arduino third-party libraries (TCA9555, ESP32-audioI2S, SensorLib, lvgl)
have their own LICENSE files, but those are _not_ the sources vendored here.

Treat the files below as "Waveshare demo code, license unstated." If we
redistribute any of this (including after adaptation), revisit the question and
contact Waveshare for clarity.

## Files vendored

Paths on the left are relative to this directory; paths on the right are
relative to the ZIP root (`ESP32-S3-AUDIO-Board-Demo/`).

| Vendored here                      | From inside the ZIP                                        |
| ---------------------------------- | ---------------------------------------------------------- |
| `hardeware_driver/bsp_board.c`     | `ESP-IDF/factory_01/main/hardeware_driver/bsp_board.c`     |
| `hardeware_driver/bsp_board.h`     | `ESP-IDF/factory_01/main/hardeware_driver/bsp_board.h`     |
| `tca9555_driver/tca9555_driver.c`  | `ESP-IDF/factory_01/main/tca9555_driver/tca9555_driver.c`  |
| `tca9555_driver/tca9555_driver.h`  | `ESP-IDF/factory_01/main/tca9555_driver/tca9555_driver.h`  |
| `button_driver/button_driver.c`    | `ESP-IDF/factory_01/main/button_driver/button_driver.c`    |
| `button_driver/button_driver.h`    | `ESP-IDF/factory_01/main/button_driver/button_driver.h`    |
| `audio_play_driver/audio_driver.c` | `ESP-IDF/factory_01/main/audio_play_driver/audio_driver.c` |
| `audio_play_driver/audio_driver.h` | `ESP-IDF/factory_01/main/audio_play_driver/audio_driver.h` |

The `hardeware_driver` spelling (missing the `w`) is Waveshare's — preserved
verbatim so diffs against upstream stay clean.

## DO NOT EDIT THESE FILES

> **These files are verbatim from the upstream demo. Do NOT edit them here —
> if they need adaptation, copy into the parent `hux_audio` component and edit
> there so diffs against upstream stay clean.**

That means:

- No reformatting, no header blocks, no lint fixes, no typo fixes (yes, even
  `hardeware`).
- No `CMakeLists.txt` inside this tree — the vendored sources are **not**
  compiled as-is. The parent `hux_audio` component owns the build.
- When we need to change behavior, the workflow is: copy the file one level up
  into `hux_audio/` (or a sibling), edit there, and leave this copy untouched
  as the reference.

Refreshing this drop: re-run the download, verify SHA256, replace the files,
update this README.

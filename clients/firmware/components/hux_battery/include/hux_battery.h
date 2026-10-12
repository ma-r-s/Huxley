/**
 * hux_battery -- the cell voltage on the Waveshare ESP32-S3-AUDIO-Board.
 *
 * The board divides the cell by 3 (R2 200K over R11 100K) onto its
 * BAT_ADC net, but stock boards leave that net unconnected: it reaches
 * GPIO1 (via R53 + R78) or GPIO6 (via R78) only once a 0R jumper is
 * soldered ("battery measurement is disabled by default" on the
 * schematic). hux_battery_probe() tells a wired divider from an empty
 * pad, so the firmware never reports a floating pin as a battery level.
 */
#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Open ADC1 and its calibration. False if the ADC is unavailable. */
bool hux_battery_init(void);

/**
 * Read GPIO1 and GPIO6 floating and then pulled down, and log one
 * `battery.probe` line per pin with a wired / not_wired verdict.
 * Blocks ~300 ms; run it from the console task, never the audio path.
 */
void hux_battery_probe(void);

#ifdef __cplusplus
}
#endif

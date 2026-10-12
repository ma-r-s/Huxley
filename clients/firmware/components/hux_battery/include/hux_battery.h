/**
 * hux_battery -- the cell voltage on the Waveshare ESP32-S3-AUDIO-Board.
 *
 * The board divides the cell by 3 (R2 200K over R11 100K) onto its
 * BAT_ADC net, but stock boards leave that net unconnected: it reaches
 * GPIO1 (via R53 + R78) or GPIO6 (via R78) only once a 0R jumper is
 * soldered ("battery measurement is disabled by default" on the
 * schematic); Waveshare's Arduino demo reads GPIO8 instead.
 * hux_battery_probe() tells a wired divider from an empty pad on every
 * free ADC1 pin, so the firmware never reports a floating pin as a level.
 */
#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Open ADC1 and its calibration. False if the ADC is unavailable. */
bool hux_battery_init(void);

/**
 * Read each free ADC1 pin (1-6, 8, 9) floating and then pulled down, and
 * log one `battery.probe` line per pin with a wired / not_wired verdict.
 * Blocks ~1 s; run it from the console task, never the audio path.
 */
void hux_battery_probe(void);

/** Where a reading goes (the app sends `client_event system.battery`). */
typedef void (*hux_battery_sink_fn)(int cell_mv, int pct);

/**
 * Read the level once a minute (F-0025): finds the wired pin (GPIO6, else
 * GPIO1), reads the median of 32 samples while the speaker is quiet (or
 * anyway after 5 min without a quiet minute), and calls `sink` with each
 * reading (once a minute). Without the solder blob it logs
 * `battery.not_wired` once and looks again every 10 min.
 */
void hux_battery_start(hux_battery_sink_fn sink);

/** Call the sink now with the last reading, if any (after a hello). */
void hux_battery_report_now(void);

/** The last reading; false before the first (or never wired). */
bool hux_battery_last(int *mv, int *pct);

/** A multimeter reading of the cell (`battery cal <mv>`): scales every
 * later reading to match, saved in NVS. */
void hux_battery_calibrate(int measured_mv);

#ifdef __cplusplus
}
#endif

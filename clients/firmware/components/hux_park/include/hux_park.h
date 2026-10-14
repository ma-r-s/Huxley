/**
 * hux_park: the low-battery cutoff's park (F-0030, design v2).
 *
 * An extra layer above the cells' protection boards. When the running
 * firmware's readings say the cell is spent (hux_battery_cutoff.h), it
 * says so ("Bateria agotada. Ponme a cargar."), tells the server, arms the
 * park in RTC memory and restarts. At boot, before Wi-Fi, audio and the
 * supervisor draw anything, an armed park (or a resting reading under
 * 3.45 V, which also catches a power cycle that lost RTC memory) keeps the
 * walkie in light sleep, waking every 60 s to read the cell, until 3.60 V
 * (only a charger lifts it there). A press while parked says the sentence
 * once; holding the lever 10 s boots anyway (the escape); a reading that
 * isn't believable never keeps it parked. Logs at warning level so the
 * black box carries the park to the next hello.
 */
#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/** From app_main, after hux_board_init and hux_battery_init and before
 * anything else starts: returns when the walkie may boot (at once if
 * nothing is armed and the cell is fine). True if it parked first: the
 * user switches on a charged walkie, a power on for them (F-0033). */
bool hux_park_at_boot(void);

/** hux_battery's cutoff sink: says it, tells the server, arms the park and
 * restarts. Never returns. */
void hux_park_cutoff(int cell_mv, const char *why);

/** Bench (console `battery park <mv> <minutes>`): park now as if the cell
 * read `mv` (under 3.60 V), for `minutes` of wakes, then boot by itself. */
void hux_park_test(int mv, int minutes);

#ifdef __cplusplus
}
#endif

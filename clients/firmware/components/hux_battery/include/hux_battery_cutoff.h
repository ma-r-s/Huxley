/**
 * hux_battery_cutoff: when to stop drawing from the cell (F-0030).
 *
 * An extra layer above the cells' protection boards (about 2.5-2.75 V,
 * deep enough to age them): the walkie parks itself at a resting 3.40 V
 * and only a charger brings it back. Pure, host-tested
 * (tests/test_hux_battery_cutoff.c); hux_battery feeds it readings, the
 * app acts on its verdict.
 *
 * A reading only counts when it is believable: between 2.9 and 3.4 V (a
 * cracked solder blob reads about 0 V and must never park the walkie for
 * good), with the calibration within 5 %, and only once the cell has been
 * in the 10 % zone for 10 min (a cell doesn't fall from half to empty in
 * one step; a glitch does).
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_CUTOFF_ZONE_MV      3610 /* 10 %: hux_battery_pct's curve */
#define HUX_CUTOFF_QUIET_MV     3400 /* resting (speaker quiet 30 s) */
#define HUX_CUTOFF_LOADED_MV    3250 /* under the amp: a long book never rests */
#define HUX_CUTOFF_LOADED_TIMES 3    /* in a row, a minute apart */
#define HUX_CUTOFF_SANE_MIN_MV  2900
#define HUX_CUTOFF_BOOT_MV      3450 /* a boot below this parks (before Wi-Fi draws) */
#define HUX_CUTOFF_RESUME_MV    3600 /* only a charger lifts a parked cell this far */

typedef struct {
    int64_t zone_since_ms; /* first reading at or under 10 %, -1 if not there */
    int loaded_low;        /* loaded readings under HUX_CUTOFF_LOADED_MV in a row */
    int64_t loaded_low_ms; /* when the last of them counted */
} hux_cutoff_t;

typedef enum {
    HUX_CUTOFF_NONE = 0,
    HUX_CUTOFF_QUIET,  /* a resting reading under 3.40 V */
    HUX_CUTOFF_LOADED, /* three loaded readings under 3.25 V, a minute apart */
} hux_cutoff_why_t;

void hux_cutoff_reset(hux_cutoff_t *c);

/** One reading of the cell (mV, calibrated), `quiet`: the speaker had
 * been silent 30 s. Returns why it is time to park, or NONE. */
hux_cutoff_why_t hux_cutoff_step(hux_cutoff_t *c, int cell_mv, bool quiet, bool cal_ok,
                                 int64_t now_ms);

/** In the 10 % zone: read every 2 min instead of every 20. */
bool hux_cutoff_in_zone(const hux_cutoff_t *c);

/** At boot, before Wi-Fi: park? `flag`: the running firmware decided to
 * (RTC memory). Otherwise only a believable resting reading under 3.45 V. */
bool hux_cutoff_park_at_boot(bool flag, int cell_mv, bool cal_ok);

/** While parked: leave the park and boot? At 3.60 V (a charger), or when
 * the reading is not believable (never park on a broken reading). */
bool hux_cutoff_resume(int cell_mv, bool cal_ok);

const char *hux_cutoff_why_name(hux_cutoff_why_t why);

#ifdef __cplusplus
}
#endif

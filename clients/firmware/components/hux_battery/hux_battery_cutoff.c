#include "hux_battery_cutoff.h"

#define ZONE_DWELL_MS  (10 * 60 * 1000)
#define LOADED_GAP_MS  (60 * 1000)
#define LOADED_SLOP_MS 5000 /* readings come "a minute apart" give or take */

void hux_cutoff_reset(hux_cutoff_t *c) {
    c->zone_since_ms = -1;
    c->loaded_low = 0;
    c->loaded_low_ms = 0;
}

static bool sane(int mv) {
    return mv >= HUX_CUTOFF_SANE_MIN_MV;
}

hux_cutoff_why_t hux_cutoff_step(hux_cutoff_t *c, int cell_mv, bool quiet, bool cal_ok,
                                 int64_t now_ms) {
    if (!cal_ok || !sane(cell_mv)) {
        return HUX_CUTOFF_NONE; /* not believable: changes nothing */
    }
    if (cell_mv > HUX_CUTOFF_ZONE_MV) {
        hux_cutoff_reset(c); /* out of the zone (or charging): start over */
        return HUX_CUTOFF_NONE;
    }
    if (c->zone_since_ms < 0) {
        c->zone_since_ms = now_ms;
    }
    bool dwelt = now_ms - c->zone_since_ms >= ZONE_DWELL_MS;
    if (quiet) {
        c->loaded_low = 0; /* a resting reading says more than any loaded one */
        return dwelt && cell_mv < HUX_CUTOFF_QUIET_MV ? HUX_CUTOFF_QUIET : HUX_CUTOFF_NONE;
    }
    if (cell_mv >= HUX_CUTOFF_LOADED_MV) {
        c->loaded_low = 0;
        return HUX_CUTOFF_NONE;
    }
    if (c->loaded_low == 0 || now_ms - c->loaded_low_ms >= LOADED_GAP_MS - LOADED_SLOP_MS) {
        c->loaded_low++;
        c->loaded_low_ms = now_ms;
    }
    return dwelt && c->loaded_low >= HUX_CUTOFF_LOADED_TIMES ? HUX_CUTOFF_LOADED
                                                             : HUX_CUTOFF_NONE;
}

bool hux_cutoff_in_zone(const hux_cutoff_t *c) {
    return c->zone_since_ms >= 0;
}

bool hux_cutoff_park_at_boot(bool flag, int cell_mv, bool cal_ok) {
    if (flag) {
        return true;
    }
    return cal_ok && sane(cell_mv) && cell_mv < HUX_CUTOFF_BOOT_MV;
}

bool hux_cutoff_resume(int cell_mv, bool cal_ok) {
    return !cal_ok || !sane(cell_mv) || cell_mv >= HUX_CUTOFF_RESUME_MV;
}

const char *hux_cutoff_why_name(hux_cutoff_why_t why) {
    switch (why) {
        case HUX_CUTOFF_QUIET:
            return "quiet";
        case HUX_CUTOFF_LOADED:
            return "loaded";
        default:
            return "none";
    }
}

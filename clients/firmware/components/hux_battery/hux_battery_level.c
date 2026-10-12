#include "hux_battery_level.h"

/* Resting voltage of a typical 18650 Li-ion cell against its charge. */
static const struct {
    int mv;
    int pct;
} CURVE[] = {
    {4150, 100}, {4050, 90}, {3970, 80}, {3900, 70}, {3840, 60}, {3790, 50},
    {3750, 40},  {3710, 30}, {3670, 20}, {3610, 10}, {3500, 5},  {3300, 0},
};
#define NCURVE (int)(sizeof(CURVE) / sizeof(CURVE[0]))

int hux_battery_pct(int cell_mv) {
    if (cell_mv >= CURVE[0].mv) {
        return 100;
    }
    for (int i = 1; i < NCURVE; i++) {
        if (cell_mv >= CURVE[i].mv) {
            int dmv = CURVE[i - 1].mv - CURVE[i].mv;
            int dp = CURVE[i - 1].pct - CURVE[i].pct;
            return CURVE[i].pct + (cell_mv - CURVE[i].mv) * dp / dmv;
        }
    }
    return 0;
}

int hux_battery_smooth(int prev, int now) {
    if (prev < 0 || now < prev || now >= prev + 5) {
        return now;
    }
    return prev;
}

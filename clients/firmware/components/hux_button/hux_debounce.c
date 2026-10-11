#include "hux_debounce.h"

hux_edge_t hux_debounce_step(hux_debounce_t *d, bool raw_pressed, uint8_t samples_needed) {
    if (raw_pressed == d->pressed) {
        d->samples_against = 0;
        return HUX_EDGE_NONE;
    }
    d->samples_against++;
    if (d->samples_against < samples_needed) {
        return HUX_EDGE_NONE; /* Too noisy to trust yet. */
    }
    d->pressed = raw_pressed;
    d->samples_against = 0;
    return raw_pressed ? HUX_EDGE_PRESS : HUX_EDGE_RELEASE;
}

/**
 * hux_debounce: the talk button's debounce and edge detection.
 *
 * Pure: no I/O, no FreeRTOS, no IDF headers. Host-testable in the
 * firmware/tests/ tree. `hux_button` feeds it one raw sample per poll
 * (already OR-ed across the talk inputs) and posts an event on each
 * edge it returns.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    HUX_EDGE_NONE = 0,
    HUX_EDGE_PRESS,
    HUX_EDGE_RELEASE,
} hux_edge_t;

typedef struct {
    bool pressed;             /* current debounced state */
    uint8_t samples_against;  /* consecutive samples that disagree with `pressed` */
} hux_debounce_t;

/**
 * One poll. The debounced state flips only after `samples_needed`
 * consecutive samples disagree with it; then this returns the edge.
 * A single agreeing sample resets the count (bounce never accumulates).
 */
hux_edge_t hux_debounce_step(hux_debounce_t *d, bool raw_pressed, uint8_t samples_needed);

#ifdef __cplusplus
}
#endif

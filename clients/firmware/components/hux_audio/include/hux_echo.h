/**
 * hux_echo: measuring, then hiding, the speaker in the mic during calls
 * (F-0031).
 *
 * During a call the mic streams while the speaker plays the far end, and
 * the mic hears the speaker: the far end hears itself. The ES7210's
 * channel 0 is the speaker's own output looped back, sampled in the same
 * TDM frame as the mic, so no delay needs estimating. Per 20 ms frame
 * this tracks when the speaker is active (above its own noise floor, with
 * a hold over the room's tail) and, when applying, turns the mic down
 * 30 dB then (half duplex, a speakerphone's way). In shadow it changes
 * nothing and only measures: the spread of mic/speaker power over active
 * frames (the echo's strength), peaks and clipping on both, how often it
 * would have gated. The first real call runs in shadow; its numbers pick
 * between this gate and real echo cancellation (F-0031's critic). Pure,
 * host-tested (tests/test_hux_echo.c).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_ECHO_BIN_MIN_DB (-60)
#define HUX_ECHO_BINS       101 /* 1 dB each, -60..+40 */

typedef struct {
    bool apply;            /* on: change the mic; shadow: measure only */
    float hp_mic_x, hp_mic_y, hp_ref_x, hp_ref_y; /* 300 Hz high-pass state */
    float ref_hold;        /* the speaker's power, held over the room's tail */
    float ref_floor;       /* the speaker channel's own noise floor */
    float gain;            /* the mic gain at the end of the last frame */
    /* Since the last take (hux_echo_take_stats). */
    uint32_t frames, active, mic_clips, ref_clips; /* active: gated (or would be) */
    int16_t mic_peak, ref_peak;
    uint16_t hist[HUX_ECHO_BINS]; /* mic/ref power in dB, active frames */
} hux_echo_t;

void hux_echo_reset(hux_echo_t *e, bool apply);

/** One frame: `mic` is modified in place when applying (the gain ramps:
 * closing within 2 ms, opening over about 40 ms); `ref` is the same
 * instants' speaker channel. */
void hux_echo_process(hux_echo_t *e, int16_t *mic, const int16_t *ref, size_t n);

/** The pct-th percentile (0-100) of mic/ref dB over the active frames
 * since the last take, or -999 if there were none. */
int hux_echo_percentile(const hux_echo_t *e, int pct);

/** Clear the stats (after logging them). */
void hux_echo_take_stats(hux_echo_t *e);

/** Mean power of `n` samples, full scale = 1. */
float hux_echo_power(const int16_t *x, size_t n);

#ifdef __cplusplus
}
#endif

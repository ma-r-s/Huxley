/**
 * hux_echo: measuring, then hiding, the speaker in the mic during calls
 * (F-0031).
 *
 * During a call the mic streams while the speaker plays the far end, and
 * the mic hears the speaker: the far end hears itself. The ES7210's
 * channel 0 is the speaker's own output looped back, sampled in the same
 * TDM frame as the mic, so no delay needs estimating. Per 20 ms frame
 * this tracks when the speaker is active (above its own noise floor, with
 * a hold over the room's tail) and, when applying, turns the mic down by
 * the depth then (half duplex, a speakerphone's way: one at a time, like
 * the lever; no double-talk pass). In shadow it changes nothing and only
 * measures: the spread of mic/speaker power over active frames (the echo's
 * strength), peaks and clipping on both, how often it would have gated.
 * The first real call measured the echo 23 dB over the near end's voice
 * (-16 dBFS while the speaker plays, the mic floor -67): a 30 dB gate left
 * it 20 dB over the floor, so the depth is 50 dB by default. Pure,
 * host-tested (tests/test_hux_echo.c).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_ECHO_DEPTH_DB     50 /* the gate, in dB (console `echo depth`, 20-70) */
#define HUX_ECHO_DEPTH_MIN_DB 20
#define HUX_ECHO_DEPTH_MAX_DB 70
#define HUX_ECHO_BIN_MIN_DB (-60)
#define HUX_ECHO_BINS       101 /* 1 dB each, -60..+40 */

typedef struct {
    bool apply;            /* on: change the mic; shadow: measure only */
    float hp_mic_x, hp_mic_y, hp_ref_x, hp_ref_y; /* 300 Hz high-pass state */
    float ref_hold;        /* the speaker's power, held over the room's tail */
    float ref_floor;       /* the speaker channel's own noise floor */
    float gain;            /* the mic gain at the end of the last frame */
    float suppress;        /* the mic gain while gated: the depth in amplitude */
    /* Since the last take (hux_echo_take_stats). */
    uint32_t frames, active, mic_clips, ref_clips; /* active: gated (or would be) */
    int16_t mic_peak, ref_peak;
    uint16_t hist[HUX_ECHO_BINS]; /* mic/ref power in dB, active frames */
    /* Release: from the speaker's last active frame to the mic open again
     * (gain over 0.9), in frames; the last one and the longest since the
     * last take. */
    uint32_t since_active;  /* frames since the reference was last active */
    bool closed;            /* the gate closed since the mic was last open */
    uint32_t release_last, release_max;
} hux_echo_t;

/** `depth_db`: how far the mic goes down while the speaker plays (clamped
 * to 20-70 dB). */
void hux_echo_reset(hux_echo_t *e, bool apply, int depth_db);

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

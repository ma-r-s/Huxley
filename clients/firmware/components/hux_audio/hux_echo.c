#include "hux_echo.h"

#include <math.h>
#include <string.h>

/* The speaker counts as active above -50 dBFS (power 1e-5) and 10 dB over
 * its own channel's floor (a hissing line must not gate the mic all call). */
#define REF_ACTIVE     1e-5f
#define REF_OVER_FLOOR 10.0f
/* The held power falls 60 dB in 300 ms (the room's tail): x 0.4 a frame. */
#define REF_DECAY      0.4f
/* The floor follows a fall at once (speech pauses between syllables) and a
 * rise slowly (2 dB a second: a long phrase never becomes the floor). */
#define FLOOR_RISE     1.0093f
/* Opening: halfway each frame (about 40 ms), across the frame; closing:
 * within 2 ms (no click, no onset leaking through a slow ramp). */
#define OPEN_STEP      0.5f
#define CLOSE_RAMP     48
/* 300 Hz one-pole high-pass at 24 kHz: below it the small speaker makes
 * almost nothing, so bass in the reference would read as a weak echo. */
#define HP_A           0.9274f

void hux_echo_reset(hux_echo_t *e, bool apply, int depth_db) {
    memset(e, 0, sizeof(*e));
    e->apply = apply;
    e->gain = 1.0f;
    if (depth_db < HUX_ECHO_DEPTH_MIN_DB) {
        depth_db = HUX_ECHO_DEPTH_MIN_DB;
    } else if (depth_db > HUX_ECHO_DEPTH_MAX_DB) {
        depth_db = HUX_ECHO_DEPTH_MAX_DB;
    }
    e->suppress = powf(10.0f, -(float)depth_db / 20.0f);
    e->ref_floor = 1.0f; /* falls to the real floor within frames */
}

float hux_echo_power(const int16_t *x, size_t n) {
    if (n == 0) {
        return 0.0f;
    }
    float sum = 0.0f;
    for (size_t i = 0; i < n; i++) {
        float v = (float)x[i] / 32768.0f;
        sum += v * v;
    }
    return sum / (float)n;
}

/* High-passed power of a frame, and its peak and clips. */
static float hp_power(const int16_t *x, size_t n, float *px, float *py, int16_t *peak,
                      uint32_t *clips) {
    float sum = 0.0f, xp = *px, yp = *py;
    for (size_t i = 0; i < n; i++) {
        float v = (float)x[i] / 32768.0f;
        float y = HP_A * (yp + v - xp);
        xp = v;
        yp = y;
        sum += y * y;
        int16_t a = x[i] < 0 ? (int16_t)(x[i] == INT16_MIN ? INT16_MAX : -x[i]) : x[i];
        if (a > *peak) {
            *peak = a;
        }
        if (a >= 32700) {
            (*clips)++;
        }
    }
    *px = xp;
    *py = yp;
    return n > 0 ? sum / (float)n : 0.0f;
}

void hux_echo_process(hux_echo_t *e, int16_t *mic, const int16_t *ref, size_t n) {
    float r = hp_power(ref, n, &e->hp_ref_x, &e->hp_ref_y, &e->ref_peak, &e->ref_clips);
    float m = hp_power(mic, n, &e->hp_mic_x, &e->hp_mic_y, &e->mic_peak, &e->mic_clips);
    e->ref_floor = r < e->ref_floor ? r : e->ref_floor * FLOOR_RISE;
    e->ref_hold = r > e->ref_hold * REF_DECAY ? r : e->ref_hold * REF_DECAY;
    float threshold = e->ref_floor * REF_OVER_FLOOR;
    if (threshold < REF_ACTIVE) {
        threshold = REF_ACTIVE;
    }
    bool active = e->ref_hold >= threshold;
    if (r >= threshold) {
        /* How loud the mic is next to the speaker: the echo's strength while
         * only the far end talks, more when the near end talks over it. */
        int bin = (int)lroundf(10.0f * log10f((m + 1e-12f) / r)) - HUX_ECHO_BIN_MIN_DB;
        bin = bin < 0 ? 0 : bin >= HUX_ECHO_BINS ? HUX_ECHO_BINS - 1 : bin;
        if (e->hist[bin] < UINT16_MAX) {
            e->hist[bin]++;
        }
    }
    float target = active ? e->suppress : 1.0f;
    float from = e->gain;
    float to = target < from ? target : from + (target - from) * OPEN_STEP;
    if (e->apply) {
        size_t ramp = to < from ? (CLOSE_RAMP < n ? CLOSE_RAMP : n) : n;
        for (size_t i = 0; i < n; i++) {
            float g = i < ramp ? from + (to - from) * (float)(i + 1) / (float)ramp : to;
            mic[i] = (int16_t)((float)mic[i] * g);
        }
    }
    e->gain = to;
    e->frames++;
    e->active += active;
    if (r >= threshold) {
        e->since_active = 0;
    } else {
        e->since_active++;
    }
    if (to < 0.5f) {
        e->closed = true;
    } else if (e->closed && to > 0.9f) {
        e->closed = false; /* open again: the speaker's tail plus the ramp */
        e->release_last = e->since_active;
        if (e->since_active > e->release_max) {
            e->release_max = e->since_active;
        }
    }
}

int hux_echo_percentile(const hux_echo_t *e, int pct) {
    uint32_t total = 0;
    for (int i = 0; i < HUX_ECHO_BINS; i++) {
        total += e->hist[i];
    }
    if (total == 0) {
        return -999;
    }
    uint32_t want = (total * (uint32_t)pct + 99) / 100;
    uint32_t seen = 0;
    for (int i = 0; i < HUX_ECHO_BINS; i++) {
        seen += e->hist[i];
        if (seen >= want && seen > 0) {
            return i + HUX_ECHO_BIN_MIN_DB;
        }
    }
    return HUX_ECHO_BINS - 1 + HUX_ECHO_BIN_MIN_DB;
}

void hux_echo_take_stats(hux_echo_t *e) {
    e->frames = e->active = e->mic_clips = e->ref_clips = 0;
    e->release_max = 0;
    e->mic_peak = e->ref_peak = 0;
    memset(e->hist, 0, sizeof(e->hist));
}

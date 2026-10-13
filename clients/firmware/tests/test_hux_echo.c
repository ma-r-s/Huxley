/**
 * Host unit tests for `hux_echo`, the call echo gate and its measurements
 * (F-0031): a simulated room where the mic hears the speaker's output
 * (delayed and scaled), a near-end talker, a noise floor, a hissing line.
 */
#include <math.h>
#include <string.h>

#include "test_helpers.h"

#include "hux_echo.h"

#define N     480 /* 20 ms at 24 kHz */
#define DELAY 96  /* 4 ms acoustic path */

static uint32_t s_seed = 12345;

static float noise(void) { /* uniform -1..1, deterministic */
    s_seed = s_seed * 1664525u + 1013904223u;
    return (float)(int32_t)s_seed / 2147483648.0f;
}

static void burst(int16_t *x, size_t n, float amp, bool on) {
    for (size_t i = 0; i < n; i++) {
        x[i] = on ? (int16_t)(noise() * amp * 32767.0f) : 0;
    }
}

typedef struct {
    int16_t ref_hist[N + DELAY];
} room_t;

/* The mic hears the speaker `echo_amp` times, DELAY samples later, plus
 * `near`, plus a -70 dBFS floor. */
static void room_frame(room_t *rm, const int16_t *ref, const int16_t *near, float echo_amp,
                       int16_t *mic) {
    memmove(rm->ref_hist, rm->ref_hist + N, DELAY * sizeof(int16_t));
    memcpy(rm->ref_hist + DELAY, ref, N * sizeof(int16_t));
    for (size_t i = 0; i < N; i++) {
        float v = rm->ref_hist[i] * echo_amp + (near ? near[i] : 0) + noise() * 10.0f;
        mic[i] = (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
    }
}

static float db(float p) {
    return 10.0f * log10f(p + 1e-12f);
}

static void test_far_end_alone_is_gated(void) {
    hux_echo_t e;
    hux_echo_reset(&e, true);
    room_t rm = {0};
    int16_t ref[N], mic[N];
    float in_p = 0, out_p = 0;
    for (int f = 0; f < 200; f++) {
        bool talking = (f / 25) % 2 == 0; /* 500 ms on, 500 ms off */
        burst(ref, N, 0.3f, talking);
        room_frame(&rm, ref, NULL, 0.5f, mic);
        float before = hux_echo_power(mic, N);
        hux_echo_process(&e, mic, ref, N);
        if (f >= 50 && talking) {
            in_p += before;
            out_p += hux_echo_power(mic, N);
        }
    }
    HX_ASSERT_TRUE(db(out_p) - db(in_p) < -25.0f, "the echo is down by 25 dB or more");
}

static void test_shadow_changes_nothing_and_measures(void) {
    hux_echo_t e;
    hux_echo_reset(&e, false);
    room_t rm = {0};
    int16_t ref[N], mic[N], copy[N];
    bool same = true;
    for (int f = 0; f < 110; f++) {
        burst(ref, N, 0.3f, f % 11 < 8); /* syllables: 160 ms, then 60 ms apart */
        room_frame(&rm, ref, NULL, 0.5f, mic);
        memcpy(copy, mic, sizeof(copy));
        hux_echo_process(&e, mic, ref, N);
        same &= memcmp(copy, mic, sizeof(copy)) == 0;
    }
    HX_ASSERT_TRUE(same, "shadow: the mic is untouched");
    HX_ASSERT_TRUE(e.active > 95, "it would have gated (speech and its tail)");
    /* The echo is 0.5 in amplitude: -6 dB in power. */
    int p50 = hux_echo_percentile(&e, 50);
    HX_ASSERT_TRUE(p50 >= -8 && p50 <= -4, "the median mic/ref is the room's coupling");
    hux_echo_take_stats(&e);
    HX_ASSERT_EQ(hux_echo_percentile(&e, 50), -999, "taken: nothing left");
}

static void test_near_end_alone_passes(void) {
    hux_echo_t e;
    hux_echo_reset(&e, true);
    room_t rm = {0};
    int16_t ref[N], mic[N], near[N], copy[N];
    memset(ref, 0, sizeof(ref));
    bool same = true;
    for (int f = 0; f < 50; f++) {
        burst(near, N, 0.1f, true);
        room_frame(&rm, ref, near, 0.5f, mic);
        memcpy(copy, mic, sizeof(copy));
        hux_echo_process(&e, mic, ref, N);
        same &= memcmp(copy, mic, sizeof(copy)) == 0;
    }
    HX_ASSERT_TRUE(same, "untouched with the speaker silent");
    HX_ASSERT_EQ((int)e.active, 0, "never gated");
}

static void test_a_hissing_line_doesnt_gate(void) {
    hux_echo_t e;
    hux_echo_reset(&e, true);
    room_t rm = {0};
    int16_t ref[N], mic[N];
    for (int f = 0; f < 100; f++) {
        burst(ref, N, 0.02f, true); /* steady hiss at about -39 dBFS */
        room_frame(&rm, ref, NULL, 0.5f, mic);
        hux_echo_process(&e, mic, ref, N);
    }
    hux_echo_take_stats(&e);
    for (int f = 0; f < 50; f++) {
        burst(ref, N, 0.02f, true);
        room_frame(&rm, ref, NULL, 0.5f, mic);
        hux_echo_process(&e, mic, ref, N);
    }
    HX_ASSERT_TRUE(e.active < 5, "the floor learned: hiss isn't the far end talking");
    HX_ASSERT_TRUE(e.gain > 0.9f, "open");
}

static void test_clips_counted(void) {
    hux_echo_t e;
    hux_echo_reset(&e, false);
    int16_t ref[N], mic[N];
    for (size_t i = 0; i < N; i++) {
        ref[i] = (i % 2) ? 32767 : -32768;
        mic[i] = 100;
    }
    hux_echo_process(&e, mic, ref, N);
    HX_ASSERT_TRUE(e.ref_clips >= N - 1, "every full-scale sample counts");
    HX_ASSERT_EQ(e.mic_clips, 0, "the mic didn't clip");
    HX_ASSERT_EQ(e.ref_peak, 32767, "the peak");
}

static void test_no_click(void) {
    hux_echo_t e;
    hux_echo_reset(&e, true);
    int16_t ref[N], mic[N];
    for (size_t i = 0; i < N; i++) {
        mic[i] = 10000;
    }
    burst(ref, N, 0.3f, true);
    hux_echo_process(&e, mic, ref, N); /* closes from 1.0 */
    int maxstep = 0;
    for (size_t i = 1; i < N; i++) {
        int d = mic[i - 1] - mic[i];
        maxstep = d > maxstep ? d : maxstep;
    }
    HX_ASSERT_TRUE(maxstep < 300, "the gain ramps (2 ms), no step");
}

int main(void) {
    HX_RUN(test_far_end_alone_is_gated);
    HX_RUN(test_shadow_changes_nothing_and_measures);
    HX_RUN(test_near_end_alone_passes);
    HX_RUN(test_a_hissing_line_doesnt_gate);
    HX_RUN(test_clips_counted);
    HX_RUN(test_no_click);
    HX_SUMMARY();
}

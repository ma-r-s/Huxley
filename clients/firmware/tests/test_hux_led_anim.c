/**
 * Host unit tests for `hux_led_anim` (F-0034): the light is Mario's mockup,
 * number for number. The reference values below are the mockup's own JS
 * formulas evaluated in double precision (the generator is described in
 * the F-0034 triage entry).
 */
#include <math.h>

#include "test_helpers.h"

#include "hux_led_anim.h"

static const struct { uint32_t t; float b[7]; } ORBIT_REF[] = {
    {0, {0.850000f, 0.003599f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.003599f}},
    {40, {0.582911f, 0.733750f, 0.000517f, 0.000000f, 0.000000f, 0.000517f, 0.733750f}},
    {100, {0.141552f, 0.590052f, 0.715348f, 0.000435f, 0.000435f, 0.715348f, 0.590052f}},
    {230, {0.005380f, 0.085549f, 0.462146f, 0.848210f, 0.848210f, 0.462146f, 0.085549f}},
    {400, {0.001176f, 0.029742f, 0.255516f, 0.745796f, 0.745796f, 0.255516f, 0.029742f}},
    {460, {0.001142f, 0.029126f, 0.252331f, 0.742700f, 0.742700f, 0.252331f, 0.029126f}},
    {471, {0.017809f, 0.045793f, 0.268998f, 0.776034f, 0.776034f, 0.268998f, 0.045793f}},
    {500, {0.551142f, 0.579126f, 0.802331f, 1.000000f, 1.000000f, 0.802331f, 0.579126f}},
    {520, {0.577891f, 0.605875f, 0.829080f, 1.000000f, 1.000000f, 0.829080f, 0.605875f}},
    {521, {0.578115f, 0.578115f, 0.578115f, 1.000000f, 1.000000f, 0.578115f, 0.578115f}},
    {700, {0.535906f, 0.535906f, 0.535906f, 0.846478f, 0.846478f, 0.535906f, 0.535906f}},
    {1000, {0.217372f, 0.217372f, 0.217372f, 0.369410f, 0.369410f, 0.217372f, 0.217372f}},
    {1349, {0.066234f, 0.066234f, 0.066234f, 0.132467f, 0.132467f, 0.066234f, 0.066234f}},
    {1400, {0.058660f, 0.058660f, 0.058660f, 0.117319f, 0.117319f, 0.058660f, 0.058660f}},
};
static const float VOICE_REF[7] = {0.487325f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f};
static const float GLOW_REF[7] = {0.321927f, 0.321927f, 0.321927f, 0.321927f, 0.321927f, 0.321927f, 0.321927f};
static const float VOICE_HELD_REF[7] = {0.890091f, 0.655857f, 0.302351f, 0.000000f, 0.000000f, 0.302351f, 0.655857f};
static const float VOICE_OFF_REF[7] = {0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f};

static bool near(float a, float b) {
    return fabsf(a - b) < 2e-4f;
}

static void test_orbit_is_the_mockup(void) {
    for (size_t k = 0; k < sizeof(ORBIT_REF) / sizeof(ORBIT_REF[0]); k++) {
        float b[HUX_LED_COUNT];
        HX_ASSERT_TRUE(hux_led_orbit(ORBIT_REF[k].t, b), "running");
        for (int i = 0; i < HUX_LED_COUNT; i++) {
            if (!near(b[i], ORBIT_REF[k].b[i])) {
                printf("    t=%u led=%d got %.6f want %.6f\n", (unsigned)ORBIT_REF[k].t, i, b[i],
                       ORBIT_REF[k].b[i]);
            }
            HX_ASSERT_TRUE(near(b[i], ORBIT_REF[k].b[i]), "orbit matches the mockup");
        }
    }
}

static void test_orbit_is_symmetric_and_ends(void) {
    for (uint32_t t = 0; t <= HUX_LED_ORBIT_MS; t += 7) {
        float b[HUX_LED_COUNT];
        hux_led_orbit(t, b);
        bool mirrored = fabsf(b[1] - b[6]) < 1e-5f && fabsf(b[2] - b[5]) < 1e-5f &&
                        fabsf(b[3] - b[4]) < 1e-5f; /* 360 - 308.57 isn't bit-exact 51.43 */
        HX_ASSERT_TRUE(mirrored, "mirror pairs equal");
    }
    float b[HUX_LED_COUNT];
    HX_ASSERT_TRUE(!hux_led_orbit(HUX_LED_ORBIT_MS + 1, b), "over after 1.4 s");
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        HX_ASSERT_TRUE(b[i] == 0.0f, "dark");
    }
}

/* 16 ms frames, as the LED task runs them. */
static bool frames(hux_led_press_t *p, int n, bool held, bool voice, float mic, float b[7]) {
    bool on = false;
    for (int k = 0; k < n; k++) {
        on = hux_led_press_step(p, 16.0f, held, voice, mic, b);
    }
    return on;
}

static void test_voice_is_the_mockup(void) {
    hux_led_press_t p = {0};
    float b[HUX_LED_COUNT];
    frames(&p, 19, true, true, 0.6f, b);
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        HX_ASSERT_TRUE(near(b[i], VOICE_HELD_REF[i]), "voice held matches the mockup");
    }
    frames(&p, 6, false, true, 0.6f, b);
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        HX_ASSERT_TRUE(near(b[i], VOICE_REF[i]), "voice released matches the mockup");
    }
    HX_ASSERT_TRUE(!frames(&p, 200, false, true, 0.6f, b), "then out");
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        HX_ASSERT_TRUE(b[i] == VOICE_OFF_REF[i], "dark");
    }
}

static void test_voice_climbs_with_the_level(void) {
    hux_led_press_t p = {0};
    float b[HUX_LED_COUNT];
    frames(&p, 60, true, true, 0.0f, b);
    HX_ASSERT_TRUE(b[0] > 0.24f && b[0] < 0.26f, "silent: the bottom glows faintly");
    HX_ASSERT_TRUE(b[1] == 0.0f && b[3] == 0.0f, "nothing above");
    frames(&p, 60, true, true, 1.0f, b);
    HX_ASSERT_TRUE(b[3] > 0.69f && b[0] > 0.94f, "loud: all the way up");
}

/* The board found this: a press from dark woke the task with no time gone,
 * the level stayed 0 and the light ended before it began. */
static void test_a_press_from_dark_stays_on(void) {
    hux_led_press_t p = {0};
    float b[HUX_LED_COUNT];
    HX_ASSERT_TRUE(hux_led_press_step(&p, 0.0f, true, false, 0.0f, b), "held, no time gone: on");
    HX_ASSERT_TRUE(frames(&p, 5, true, false, 0.0f, b), "and stays on");
    HX_ASSERT_TRUE(b[0] > 0.2f, "lit");
}

static void test_glow_is_the_mockup(void) {
    hux_led_press_t p = {0};
    float b[HUX_LED_COUNT];
    frames(&p, 10, true, false, 1.0f, b);
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        HX_ASSERT_TRUE(near(b[i], GLOW_REF[i]), "glow matches the mockup, mic ignored");
    }
}

static void test_mic_mapping(void) {
    /* RMS r is mean square r^2; dBFS against 32768. */
    uint32_t at_50 = (uint32_t)powf(32768.0f * powf(10.0f, -50.0f / 20.0f), 2.0f);
    uint32_t at_15 = (uint32_t)powf(32768.0f * powf(10.0f, -15.0f / 20.0f), 2.0f);
    uint32_t at_32 = (uint32_t)powf(32768.0f * powf(10.0f, -32.5f / 20.0f), 2.0f);
    HX_ASSERT_TRUE(hux_led_mic_level(0) == 0.0f, "silence");
    HX_ASSERT_TRUE(hux_led_mic_level(at_50) < 0.01f, "-50 dBFS is 0");
    HX_ASSERT_TRUE(hux_led_mic_level(at_15) > 0.99f, "-15 dBFS is 1");
    HX_ASSERT_TRUE(fabsf(hux_led_mic_level(at_32) - 0.5f) < 0.01f, "halfway in dB");
    HX_ASSERT_TRUE(hux_led_mic_level(0x40000000u) == 1.0f, "full scale clamps");
}

static void test_pixels(void) {
    float b[HUX_LED_COUNT] = {1.0f, 0.5f, 0.0f, 1.0f, 1.0f, 0.0f, 0.5f};
    const uint8_t warm[3] = {255, 206, 158};
    uint8_t out[HUX_LED_COUNT][3];
    hux_led_pixels(b, warm, 0.25f, out);
    HX_ASSERT_EQ(out[0][0], 64, "b 1 at a quarter: 255 -> 64");
    HX_ASSERT_EQ(out[0][2], 40, "158 -> 40");
    HX_ASSERT_EQ(out[1][0], 32, "half of that");
    HX_ASSERT_EQ(out[2][1], 0, "dark is 0");
    hux_led_pixels(b, warm, 0.25f / 3.0f, out);
    HX_ASSERT_EQ(out[0][0], 21, "night: a third");
}

/* -- The frame engine (what the LED task decides each frame) ---------- */

static unsigned step(hux_led_engine_t *e, hux_led_in_t *in, uint32_t now, float bb[7], float bp[7]) {
    in->now = now;
    unsigned ev = hux_led_engine_step(e, in, bb, bp);
    in->boot_req = false; /* a request is taken once */
    return ev;
}

static void test_engine_boot_waits_for_the_sound(void) {
    hux_led_engine_t e = {0};
    hux_led_in_t in = {.played_ms = 500, .boot_req = true, .boot_p0 = 500, .boot_wait = true};
    float bb[7], bp[7];
    hux_led_engine_wake(&e, 1000);
    HX_ASSERT_EQ(step(&e, &in, 1000, bb, bp), 0u, "the sound hasn't left the ring");
    HX_ASSERT_TRUE(hux_led_engine_active(&e), "waiting keeps the task awake");
    in.played_ms = 520; /* its first chunk went to the codec */
    HX_ASSERT_EQ(step(&e, &in, 1048, bb, bp), (unsigned)HUX_LED_EV_BOOT, "Orbit starts");
    HX_ASSERT_TRUE(e.boot_heard && e.boot_wait_ms == 48, "heard after 48 ms");
    HX_ASSERT_TRUE(bb[0] == 0.0f, "dark for the I2S queue's 60 ms");
    step(&e, &in, 1048 + 59, bb, bp);
    HX_ASSERT_TRUE(bb[0] == 0.0f, "still dark at 59 ms");
    step(&e, &in, 1048 + 60, bb, bp);
    HX_ASSERT_TRUE(near(bb[0], ORBIT_REF[0].b[0]), "Orbit's t=0 when the sound is heard");
    uint32_t t = 1108;
    while (hux_led_engine_active(&e) && t < 5000) {
        step(&e, &in, t += 16, bb, bp);
    }
    HX_ASSERT_TRUE(!hux_led_engine_active(&e) && t <= 1108 + 1400 + 16, "over after 1.4 s: sleep");
}

static void test_engine_boot_timeout_and_demo(void) {
    hux_led_engine_t e = {0};
    hux_led_in_t in = {.played_ms = 7, .boot_req = true, .boot_p0 = 7, .boot_wait = true};
    float bb[7], bp[7];
    hux_led_engine_wake(&e, 0);
    step(&e, &in, 0, bb, bp);
    HX_ASSERT_EQ(step(&e, &in, 999, bb, bp), 0u, "waits up to 1 s");
    HX_ASSERT_EQ(step(&e, &in, 1000, bb, bp), (unsigned)HUX_LED_EV_BOOT, "then starts anyway");
    HX_ASSERT_TRUE(!e.boot_heard && near(bb[0], ORBIT_REF[0].b[0]), "at once, no lag");
    hux_led_engine_t d = {0};
    hux_led_in_t dm = {.boot_req = true, .boot_wait = false};
    HX_ASSERT_EQ(step(&d, &dm, 50, bb, bp), (unsigned)HUX_LED_EV_BOOT, "a demo starts at once");
}

static void test_engine_press_from_dark(void) {
    hux_led_engine_t e = {0};
    hux_led_in_t in = {.held = true, .voice = false};
    float bb[7], bp[7];
    hux_led_engine_wake(&e, 5000);
    HX_ASSERT_EQ(step(&e, &in, 5000, bb, bp), (unsigned)HUX_LED_EV_PRESS, "starts");
    HX_ASSERT_TRUE(bp[0] > 0.05f, "lit on the first frame (a frame's worth of rise)");
    for (uint32_t t = 5016; t < 6000; t += 16) {
        step(&e, &in, t, bb, bp);
    }
    HX_ASSERT_TRUE(near(bp[3], 0.42f), "Glow while held");
    in.held = false;
    unsigned all = 0;
    uint32_t t = 6000;
    while (hux_led_engine_active(&e) && t < 9000) {
        all |= step(&e, &in, t += 16, bb, bp);
    }
    HX_ASSERT_TRUE(all & HUX_LED_EV_PRESS_END, "goes out after release");
    HX_ASSERT_TRUE(!hux_led_engine_active(&e) && bp[0] == 0.0f, "dark: sleep");
}

static void test_engine_mic_staleness(void) {
    hux_led_engine_t e = {0};
    uint32_t loud = 0x10000000u; /* about -6 dBFS: level 1 */
    hux_led_in_t in = {.held = true, .voice = true, .mic_ms = loud};
    float bb[7], bp[7];
    hux_led_engine_wake(&e, 1000);
    for (uint32_t t = 1000; t < 1600; t += 16) {
        in.mic_at = t + 1; /* the mic task stored a frame a tick after this frame's clock */
        step(&e, &in, t, bb, bp);
    }
    HX_ASSERT_TRUE(bp[3] > 0.6f, "a mic frame a tick ahead counts: lit to the top");
    in.mic_at = 1600;
    for (uint32_t t = 1600; t < 2400; t += 16) {
        step(&e, &in, t, bb, bp); /* no new frame: stale after 100 ms */
    }
    HX_ASSERT_TRUE(bp[3] < 0.01f && bp[0] > 0.24f, "stale: down to the bottom's glow");
    HX_ASSERT_TRUE(e.v_max > 0.99f, "the loudest level is kept for the log");
}

static void test_engine_demo_ends_and_is_cleared(void) {
    hux_led_engine_t e = {0};
    hux_led_in_t in = {.voice = true, .demo_until = 3000};
    float bb[7], bp[7];
    hux_led_engine_wake(&e, 0);
    HX_ASSERT_TRUE(step(&e, &in, 0, bb, bp) & HUX_LED_EV_PRESS, "a demo is a press");
    HX_ASSERT_TRUE(e.demo, "marked");
    float low = 0.0f, top = 0.0f;
    for (uint32_t t = 16; t < 3000; t += 16) {
        step(&e, &in, t, bb, bp);
        low = bp[1] > low ? bp[1] : low;
        top = bp[3] > top ? bp[3] : top;
    }
    /* The mockup's sentence over its first 3 s: the low pair full (0.70),
     * the top barely (0.04). */
    HX_ASSERT_TRUE(low > 0.69f && top > 0.03f && top < 0.06f, "the mockup's demo, as drawn");
    HX_ASSERT_TRUE(step(&e, &in, 3000, bb, bp) & HUX_LED_EV_DEMO_OVER, "over: clear it");
}

int main(void) {
    HX_RUN(test_orbit_is_the_mockup);
    HX_RUN(test_orbit_is_symmetric_and_ends);
    HX_RUN(test_voice_is_the_mockup);
    HX_RUN(test_voice_climbs_with_the_level);
    HX_RUN(test_a_press_from_dark_stays_on);
    HX_RUN(test_glow_is_the_mockup);
    HX_RUN(test_mic_mapping);
    HX_RUN(test_pixels);
    HX_RUN(test_engine_boot_waits_for_the_sound);
    HX_RUN(test_engine_boot_timeout_and_demo);
    HX_RUN(test_engine_press_from_dark);
    HX_RUN(test_engine_mic_staleness);
    HX_RUN(test_engine_demo_ends_and_is_cleared);
    HX_SUMMARY();
}

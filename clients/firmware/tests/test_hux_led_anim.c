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

int main(void) {
    HX_RUN(test_orbit_is_the_mockup);
    HX_RUN(test_orbit_is_symmetric_and_ends);
    HX_RUN(test_voice_is_the_mockup);
    HX_RUN(test_voice_climbs_with_the_level);
    HX_RUN(test_glow_is_the_mockup);
    HX_RUN(test_mic_mapping);
    HX_RUN(test_pixels);
    HX_SUMMARY();
}

/**
 * Host unit tests for `hux_led_anim` (F-0034): the light is Mario's mockup,
 * number for number (Rainbow at boot, his pick). The reference values
 * below are the mockup's own JS formulas evaluated in double precision
 * (the generator is described in the F-0034 triage entry).
 */
#include <math.h>

#include "test_helpers.h"

#include "hux_led_anim.h"

static const struct { uint32_t t; float rgb[7][3]; } RAINBOW_REF[] = {
    {0, {{0.000000f, 0.000000f, 0.000000f}, {0.000000f, 0.000000f, 0.000000f}, {0.000000f, 0.000000f, 0.000000f}, {0.000000f, 0.000000f, 0.000000f}, {0.000000f, 0.000000f, 0.000000f}, {0.000000f, 0.000000f, 0.000000f}, {0.000000f, 0.000000f, 0.000000f}}},
    {100, {{0.500000f, 0.260000f, 0.100000f}, {0.397143f, 0.500000f, 0.100000f}, {0.100000f, 0.500000f, 0.145714f}, {0.100000f, 0.500000f, 0.488571f}, {0.100000f, 0.168571f, 0.500000f}, {0.374286f, 0.100000f, 0.500000f}, {0.500000f, 0.100000f, 0.282857f}}},
    {200, {{1.000000f, 0.840000f, 0.200000f}, {0.474286f, 1.000000f, 0.200000f}, {0.200000f, 1.000000f, 0.611429f}, {0.200000f, 0.702857f, 1.000000f}, {0.382857f, 0.200000f, 1.000000f}, {1.000000f, 0.200000f, 0.931429f}, {1.000000f, 0.200000f, 0.245714f}}},
    {500, {{0.200000f, 1.000000f, 0.200000f}, {0.200000f, 1.000000f, 0.885714f}, {0.200000f, 0.428571f, 1.000000f}, {0.657143f, 0.200000f, 1.000000f}, {1.000000f, 0.200000f, 0.657143f}, {1.000000f, 0.428571f, 0.200000f}, {0.885714f, 1.000000f, 0.200000f}}},
    {899, {{0.200000f, 0.523200f, 1.000000f}, {0.562514f, 0.200000f, 1.000000f}, {1.000000f, 0.200000f, 0.751771f}, {1.000000f, 0.333943f, 0.200000f}, {0.980343f, 1.000000f, 0.200000f}, {0.294629f, 1.000000f, 0.200000f}, {0.200000f, 1.000000f, 0.791086f}}},
    {900, {{0.200000f, 0.520000f, 1.000000f}, {0.565714f, 0.200000f, 1.000000f}, {1.000000f, 0.200000f, 0.748571f}, {1.000000f, 0.337143f, 0.200000f}, {0.977143f, 1.000000f, 0.200000f}, {0.291429f, 1.000000f, 0.200000f}, {0.200000f, 1.000000f, 0.794286f}}},
    {1200, {{0.274286f, 0.065306f, 0.326531f}, {0.326531f, 0.065306f, 0.154869f}, {0.326531f, 0.199650f, 0.065306f}, {0.229504f, 0.326531f, 0.065306f}, {0.065306f, 0.326531f, 0.125015f}, {0.065306f, 0.304140f, 0.326531f}, {0.065306f, 0.080233f, 0.326531f}}},
    {1599, {{0.000002f, 0.000001f, 0.000000f}, {0.000002f, 0.000002f, 0.000000f}, {0.000000f, 0.000002f, 0.000001f}, {0.000000f, 0.000002f, 0.000002f}, {0.000000f, 0.000001f, 0.000002f}, {0.000002f, 0.000000f, 0.000002f}, {0.000002f, 0.000000f, 0.000001f}}},
    {1600, {{0.000000f, 0.000000f, 0.000000f}, {0.000000f, 0.000000f, 0.000000f}, {0.000000f, 0.000000f, 0.000000f}, {0.000000f, 0.000000f, 0.000000f}, {0.000000f, 0.000000f, 0.000000f}, {0.000000f, 0.000000f, 0.000000f}, {0.000000f, 0.000000f, 0.000000f}}},
};
static const float VOICE_REF[7] = {0.094375f, 0.094375f, 0.094375f, 0.094375f, 0.094375f, 0.094375f, 0.094375f};
static const float VOICE_HELD_REF[7] = {0.554855f, 0.554855f, 0.554855f, 0.554855f, 0.554855f, 0.554855f, 0.554855f};
static const float VOICE_OFF_REF[7] = {0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f, 0.000000f};

static bool near(float a, float b) {
    return fabsf(a - b) < 2e-4f;
}

static void test_rainbow_is_the_mockup(void) {
    for (size_t k = 0; k < sizeof(RAINBOW_REF) / sizeof(RAINBOW_REF[0]); k++) {
        float rgb[HUX_LED_COUNT][3];
        HX_ASSERT_TRUE(hux_led_rainbow(RAINBOW_REF[k].t, rgb), "running");
        for (int i = 0; i < HUX_LED_COUNT; i++) {
            for (int c = 0; c < 3; c++) {
                if (!near(rgb[i][c], RAINBOW_REF[k].rgb[i][c])) {
                    printf("    t=%u led=%d ch=%d got %.6f want %.6f\n", (unsigned)RAINBOW_REF[k].t, i, c,
                           rgb[i][c], RAINBOW_REF[k].rgb[i][c]);
                }
                HX_ASSERT_TRUE(near(rgb[i][c], RAINBOW_REF[k].rgb[i][c]), "rainbow matches the mockup");
            }
        }
    }
}

static void test_rainbow_all_at_once_and_ends(void) {
    float rgb[HUX_LED_COUNT][3];
    hux_led_rainbow(500, rgb);
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        float hi = fmaxf(rgb[i][0], fmaxf(rgb[i][1], rgb[i][2]));
        HX_ASSERT_TRUE(hi > 0.99f, "all seven lit at once, full in the hold");
        int j = (i + 1) % HUX_LED_COUNT;
        HX_ASSERT_TRUE(!(near(rgb[i][0], rgb[j][0]) && near(rgb[i][1], rgb[j][1]) &&
                         near(rgb[i][2], rgb[j][2])),
                       "each its own colour");
    }
    HX_ASSERT_TRUE(!hux_led_rainbow(HUX_LED_BOOT_MS + 1, rgb), "over after 1.6 s");
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        HX_ASSERT_TRUE(rgb[i][0] == 0.0f && rgb[i][1] == 0.0f && rgb[i][2] == 0.0f, "dark");
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
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        HX_ASSERT_TRUE(b[i] == 0.0f, "lever down, silent: dark (Mario: only when someone talks)");
    }
    frames(&p, 60, true, true, 0.5f, b);
    HX_ASSERT_TRUE(b[0] > 0.49f && b[0] < 0.51f && b[3] == b[0], "half: all at 0.5");
    frames(&p, 60, true, true, 1.0f, b);
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        HX_ASSERT_TRUE(b[i] > 0.99f, "loud: all seven full");
    }
}

/* The board found this: a press from dark woke the task with no time gone,
 * the level stayed 0 and the light ended before it began. */
static void test_a_press_from_dark_stays_on(void) {
    hux_led_press_t p = {0};
    float b[HUX_LED_COUNT];
    HX_ASSERT_TRUE(hux_led_press_step(&p, 0.0f, true, true, 1.0f, b), "held, no time gone: on");
    HX_ASSERT_TRUE(frames(&p, 5, true, true, 1.0f, b), "and stays on");
    HX_ASSERT_TRUE(b[0] > 0.2f, "lit once he talks");
}

static void test_offline_press_is_dark(void) {
    hux_led_press_t p = {0};
    float b[HUX_LED_COUNT];
    HX_ASSERT_TRUE(frames(&p, 30, true, false, 1.0f, b), "a press the walkie can't act on: held");
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        HX_ASSERT_TRUE(b[i] == 0.0f, "but dark, whatever the mic hears");
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
    const float w[3] = {255.0f / 255.0f, 206.0f / 255.0f, 158.0f / 255.0f};
    const float lv[HUX_LED_COUNT] = {1.0f, 0.5f, 0.0f, 1.0f, 1.0f, 0.0f, 0.5f};
    float rgb[HUX_LED_COUNT][3];
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        for (int c = 0; c < 3; c++) {
            rgb[i][c] = w[c] * lv[i];
        }
    }
    uint8_t out[HUX_LED_COUNT][3];
    hux_led_pixels(rgb, 0.25f, 1.0f, out);
    HX_ASSERT_EQ(out[0][0], 64, "1.0 at a quarter: 255 -> 64");
    HX_ASSERT_EQ(out[0][2], 40, "158 -> 40");
    HX_ASSERT_EQ(out[1][0], 32, "half of that");
    HX_ASSERT_EQ(out[2][1], 0, "dark is 0");
    hux_led_pixels(rgb, 0.25f / 3.0f, 1.0f, out);
    HX_ASSERT_EQ(out[0][0], 21, "night: a third");
    hux_led_pixels(rgb, 0.25f, 3.0f, out);
    HX_ASSERT_EQ(out[0][0], 64, "seen directly: no gain");
    HX_ASSERT_EQ(out[1][0], 96, "behind a holder, 0.5: x3");
    float full[HUX_LED_COUNT][3];
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        full[i][0] = full[i][1] = full[i][2] = 1.0f;
    }
    hux_led_pixels(full, 0.5f, 3.0f, out);
    HX_ASSERT_EQ(out[2][0], 255, "clipped at 255");
    HX_ASSERT_TRUE(hux_led_hidden(1) && hux_led_hidden(2) && hux_led_hidden(5) && hux_led_hidden(6) &&
                       !hux_led_hidden(0) && !hux_led_hidden(3) && !hux_led_hidden(4),
                   "hidden: 1, 2, 5, 6");
}

/* -- The frame engine (what the LED task decides each frame) ---------- */

static unsigned step(hux_led_engine_t *e, hux_led_in_t *in, uint32_t now, float bb[7][3], float bp[7]) {
    in->now = now;
    unsigned ev = hux_led_engine_step(e, in, bb, bp);
    in->boot_req = false; /* requests are taken once, as the task's exchange does */
    in->walk_req = false;
    return ev;
}

static void test_engine_boot_waits_for_the_sound(void) {
    hux_led_engine_t e = {0};
    hux_led_in_t in = {.played_ms = 500, .boot_req = true, .boot_p0 = 500, .boot_wait = true};
    float bb[7][3], bp[7];
    hux_led_engine_wake(&e, 1000);
    HX_ASSERT_EQ(step(&e, &in, 1000, bb, bp), 0u, "the sound hasn't left the ring");
    HX_ASSERT_TRUE(hux_led_engine_active(&e), "waiting keeps the task awake");
    in.played_ms = 520; /* its first chunk went to the codec */
    HX_ASSERT_EQ(step(&e, &in, 1048, bb, bp), (unsigned)HUX_LED_EV_BOOT, "Orbit starts");
    HX_ASSERT_TRUE(e.boot_heard && e.boot_wait_ms == 48, "heard after 48 ms");
    HX_ASSERT_TRUE(bb[0][0] == 0.0f, "dark for the I2S queue's 60 ms");
    step(&e, &in, 1048 + 59, bb, bp);
    HX_ASSERT_TRUE(bb[0][0] == 0.0f, "still dark at 59 ms");
    step(&e, &in, 1048 + 60 + 100, bb, bp);
    HX_ASSERT_TRUE(near(bb[3][1], RAINBOW_REF[1].rgb[3][1]), "Rainbow's t=100, timed from the sound");
    uint32_t t = 1108;
    while (hux_led_engine_active(&e) && t < 5000) {
        step(&e, &in, t += 16, bb, bp);
    }
    HX_ASSERT_TRUE(!hux_led_engine_active(&e) && t <= 1108 + HUX_LED_BOOT_MS + 16, "over at the end: sleep");
}

static void test_engine_boot_timeout_and_demo(void) {
    hux_led_engine_t e = {0};
    hux_led_in_t in = {.played_ms = 7, .boot_req = true, .boot_p0 = 7, .boot_wait = true};
    float bb[7][3], bp[7];
    hux_led_engine_wake(&e, 0);
    step(&e, &in, 0, bb, bp);
    HX_ASSERT_EQ(step(&e, &in, 999, bb, bp), 0u, "waits up to 1 s");
    HX_ASSERT_EQ(step(&e, &in, 1000, bb, bp), (unsigned)HUX_LED_EV_BOOT, "then starts anyway");
    HX_ASSERT_TRUE(!e.boot_heard && bb[0][0] == 0.0f, "at once, no lag (t=0 is dark)");
    step(&e, &in, 1100, bb, bp);
    HX_ASSERT_TRUE(near(bb[3][1], RAINBOW_REF[1].rgb[3][1]), "t=100 a frame later");
    hux_led_engine_t d = {0};
    hux_led_in_t dm = {.boot_req = true, .boot_wait = false};
    HX_ASSERT_EQ(step(&d, &dm, 50, bb, bp), (unsigned)HUX_LED_EV_BOOT, "a demo starts at once");
}

static void test_engine_press_from_dark(void) {
    hux_led_engine_t e = {0};
    hux_led_in_t in = {.held = true, .voice = true, .mic_ms = 0x10000000u, .mic_at = 5000};
    float bb[7][3], bp[7];
    hux_led_engine_wake(&e, 5000);
    HX_ASSERT_EQ(step(&e, &in, 5000, bb, bp), (unsigned)HUX_LED_EV_PRESS, "starts");
    HX_ASSERT_TRUE(bp[0] > 0.01f, "lit on the first frame (a frame's worth of rise)");
    for (uint32_t t = 5016; t < 6000; t += 16) {
        in.mic_at = t;
        step(&e, &in, t, bb, bp);
    }
    HX_ASSERT_TRUE(bp[3] > 0.95f, "full while held and loud");
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
    float bb[7][3], bp[7];
    hux_led_engine_wake(&e, 1000);
    for (uint32_t t = 1000; t < 1600; t += 16) {
        in.mic_at = t + 1; /* the mic task stored a frame a tick after this frame's clock */
        step(&e, &in, t, bb, bp);
    }
    HX_ASSERT_TRUE(bp[3] > 0.95f, "a mic frame a tick ahead counts: full");
    in.mic_at = 1600;
    for (uint32_t t = 1600; t < 2400; t += 16) {
        step(&e, &in, t, bb, bp); /* no new frame: stale after 100 ms */
    }
    HX_ASSERT_TRUE(bp[3] < 0.01f, "stale: back to dark");
    HX_ASSERT_TRUE(e.v_max > 0.99f, "the loudest level is kept for the log");
}

static void test_engine_demo_ends_and_is_cleared(void) {
    hux_led_engine_t e = {0};
    hux_led_in_t in = {.voice = true, .demo_until = 3000};
    float bb[7][3], bp[7];
    hux_led_engine_wake(&e, 0);
    HX_ASSERT_TRUE(step(&e, &in, 0, bb, bp) & HUX_LED_EV_PRESS, "a demo is a press");
    HX_ASSERT_TRUE(e.demo, "marked");
    float lo = 1.0f, hi = 0.0f;
    for (uint32_t t = 400; t < 3000; t += 16) {
        step(&e, &in, t, bb, bp);
        lo = bp[0] < lo ? bp[0] : lo;
        hi = bp[0] > hi ? bp[0] : hi;
    }
    HX_ASSERT_TRUE(hi > 0.6f && lo < 0.35f, "the made-up sentence swings the whole ring");
    HX_ASSERT_TRUE(step(&e, &in, 3000, bb, bp) & HUX_LED_EV_DEMO_OVER, "over: clear it");
}

/* The chain order, from the schematic and the STEP: GPIO38 -> U9 (102.9
 * deg, position 2) -> U19 -> U14 (+x, the bottom) -> U13 -> U12 -> U11 ->
 * U10. Mario saw Orbit "sideways" when position = chain index. */
static void test_chain_order(void) {
    HX_ASSERT_EQ(HUX_LED_CHAIN_OF[0], 2, "the bottom LED is the chain's third (U14)");
    HX_ASSERT_TRUE(HUX_LED_CHAIN_OF[1] == 1 && HUX_LED_CHAIN_OF[6] == 3, "low pair (1, 3)");
    HX_ASSERT_TRUE(HUX_LED_CHAIN_OF[2] == 0 && HUX_LED_CHAIN_OF[5] == 4, "mid pair (0, 4)");
    HX_ASSERT_TRUE(HUX_LED_CHAIN_OF[3] == 6 && HUX_LED_CHAIN_OF[4] == 5, "top pair (6, 5)");
    for (int c = 0; c < 7; c++) { /* chain index c at position (2 - c) mod 7 */
        HX_ASSERT_EQ(HUX_LED_CHAIN_OF[(2 - c + 7) % 7], c, "clockwise from 102.9 deg");
    }
    uint8_t pos[7][3] = {{0}}, chain[7][3];
    pos[0][0] = 99; /* only the bottom lit */
    hux_led_chain(pos, chain);
    HX_ASSERT_EQ(chain[2][0], 99, "the bottom drives chain index 2");
    HX_ASSERT_EQ(chain[0][0], 0, "not index 0");
}

static void test_engine_walk(void) {
    hux_led_engine_t e = {0};
    hux_led_in_t in = {.walk_req = true};
    float bb[7][3], bp[7];
    hux_led_engine_wake(&e, 0);
    HX_ASSERT_TRUE(step(&e, &in, 0, bb, bp) & HUX_LED_EV_WALK, "starts");
    for (int p = 0; p < 7; p++) {
        step(&e, &in, (uint32_t)p * 600u + 300u, bb, bp);
        int lit = 0;
        for (int i = 0; i < 7; i++) {
            lit += bb[i][0] > 0.0f;
        }
        HX_ASSERT_TRUE(lit == 1 && bb[p][0] == 1.0f && bb[p][2] == 1.0f, "one position at a time, white, bottom first");
    }
    step(&e, &in, 4200, bb, bp);
    HX_ASSERT_TRUE(!hux_led_engine_active(&e), "over after 4.2 s: sleep");
}

int main(void) {
    HX_RUN(test_rainbow_is_the_mockup);
    HX_RUN(test_rainbow_all_at_once_and_ends);
    HX_RUN(test_voice_is_the_mockup);
    HX_RUN(test_voice_climbs_with_the_level);
    HX_RUN(test_a_press_from_dark_stays_on);
    HX_RUN(test_offline_press_is_dark);
    HX_RUN(test_mic_mapping);
    HX_RUN(test_pixels);
    HX_RUN(test_engine_boot_waits_for_the_sound);
    HX_RUN(test_engine_boot_timeout_and_demo);
    HX_RUN(test_engine_press_from_dark);
    HX_RUN(test_engine_mic_staleness);
    HX_RUN(test_engine_demo_ends_and_is_cleared);
    HX_RUN(test_chain_order);
    HX_RUN(test_engine_walk);
    HX_SUMMARY();
}

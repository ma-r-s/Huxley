/**
 * hux_led_anim: the light under the clear back, frame by frame (F-0034).
 * Pure, host-tested (tests/test_hux_led_anim.c): a port of Mario's mockup
 * ("Walkie lights" artifact), brightness b in 0..1 per LED.
 *
 * The ring: LED i at board angle i x 360/7 from board +x, which is device
 * down in the HX-1, so LED 0 is the bottom and the ring is mirror
 * symmetric: height steps bottom {0}, low {1,6}, mid {2,5}, top {3,4}.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_LED_COUNT    7
#define HUX_LED_ORBIT_MS 1400u /* the boot animation's length */

/** Startup "Orbit": two arms climb from the bottom LED to the top pair
 * (460 ms, easing out), a flash on the top, a soft rest gone by 1.35 s.
 * `t_ms` from when the startup sound reaches the speaker. False (all 0)
 * once it is over. */
bool hux_led_orbit(uint32_t t_ms, float b[HUX_LED_COUNT]);

/** A press's light: `level` eases toward 1 while the lever is held (110 ms)
 * and toward 0 after (240 ms); `voice` follows the mic level (70 ms). */
typedef struct {
    float level;
    float voice;
} hux_led_press_t;

/** One frame of `dt_ms`. `held`: the lever is down. `voice_mode`: the press
 * was acted on, so the light follows `mic` (0..1, hux_led_mic_level);
 * otherwise "Glow", a steady soft light. False (all 0) once the light is
 * out: released and the level under 0.003 (never while held, even on a
 * first frame with no time gone). */
bool hux_led_press_step(hux_led_press_t *p, float dt_ms, bool held, bool voice_mode, float mic,
                        float b[HUX_LED_COUNT]);

/** A mic frame's mean square (PCM16) to 0..1: -50 dBFS RMS and under is 0,
 * -15 dBFS and over is 1, linear in dB between. */
float hux_led_mic_level(uint32_t mean_square);

/** Brightness to pixels: each channel `colour` x b x `scale` (the peak:
 * 0.25 is a quarter of full), rounded, 0..255. */
void hux_led_pixels(const float b[HUX_LED_COUNT], const uint8_t colour[3], float scale,
                    uint8_t out[HUX_LED_COUNT][3]);

#ifdef __cplusplus
}
#endif

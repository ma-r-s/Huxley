/**
 * hux_led_anim: the light under the clear back, frame by frame (F-0034).
 * Pure, host-tested (tests/test_hux_led_anim.c): a port of Mario's mockup
 * ("Walkie lights" artifact), brightness b in 0..1 per LED.
 *
 * Everything here is in positions: position p at board angle p x 360/7
 * from board +x, which is device down in the HX-1, so p 0 is the bottom and
 * the ring is mirror symmetric: height steps bottom {0}, low {1,6}, mid
 * {2,5}, top {3,4}. The data chain runs in another order (hux_led_chain).
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_LED_COUNT    7
#define HUX_LED_BOOT_MS 1600u /* the startup light's length */

/** Startup "Rainbow" (Mario's pick, 2026-10-14, from his mockups): every
 * LED its own colour, spread round the ring (hue p x 360/7), turning just
 * over once (0.24 deg/ms) while the whole ring fades in (200 ms), holds
 * (700 ms) and fades out (700 ms, squared); saturation 0.8. `t_ms` from
 * when the startup sound reaches the speaker; rgb 0..1. False (all 0)
 * once it is over. */
bool hux_led_rainbow(uint32_t t_ms, float rgb[HUX_LED_COUNT][3]);

/** A press's light: `level` eases toward 1 while the lever is held (110 ms)
 * and toward 0 after (240 ms); `voice` follows the mic level (70 ms). */
typedef struct {
    float level;
    float voice;
} hux_led_press_t;

/** One frame of `dt_ms`. `held`: the lever is down. `voice_mode`: the press
 * was acted on, so all seven follow `mic` together (0..1,
 * hux_led_mic_level): 15 % silent, full at the loudest; otherwise "Glow",
 * a steady soft light (0.42) on all seven. False (all 0) once the light is
 * out: released and the level under 0.003 (never while held, even on a
 * first frame with no time gone). */
bool hux_led_press_step(hux_led_press_t *p, float dt_ms, bool held, bool voice_mode, float mic,
                        float b[HUX_LED_COUNT]);

/* -- The frame engine: what the LED task decides each frame, pure ------- */

#define HUX_LED_FRAME_MS      16u
#define HUX_LED_SOUND_LAG_MS  60u   /* the I2S DMA queue ahead of a chunk: 6 x 10 ms */
#define HUX_LED_SOUND_WAIT_MS 1000u /* the sound never moved: start anyway */
#define HUX_LED_MIC_STALE_MS  100u  /* no mic frame this long: the level is 0 */

/** What the task reads each frame (its atomics and clocks). */
typedef struct {
    uint32_t now;
    uint32_t played_ms;  /* hux_audio_played_ms() */
    bool boot_req;       /* a boot light was asked since the last frame */
    uint32_t boot_p0;    /* played_ms from before the startup sound was queued */
    bool boot_wait;      /* false: a demo, start at once */
    bool held;           /* the lever is down */
    bool voice;          /* the press was acted on (taken when it starts) */
    uint32_t mic_ms;     /* the last mic frame's mean square */
    uint32_t mic_at;     /* when it came */
    uint32_t demo_until; /* a demo press until then; 0: none */
    bool walk_req;       /* console `led walk`: each position alone, bottom first */
} hux_led_in_t;

enum {
    HUX_LED_EV_BOOT = 1,      /* the startup light is starting: take the peak, log */
    HUX_LED_EV_PRESS = 2,     /* a press light starts */
    HUX_LED_EV_PRESS_END = 4, /* it went out */
    HUX_LED_EV_DEMO_OVER = 8, /* clear demo_until (it would come round on a wrap) */
    HUX_LED_EV_WALK = 16,     /* the walk starts: take the peak, log */
};

#define HUX_LED_WALK_STEP_MS 600u /* each position alone this long, p 0..6 */

typedef struct {
    bool boot_waiting, boot_on, boot_heard;
    bool walk_on;
    uint32_t walk_start;
    uint32_t boot_since, boot_start, boot_p0, boot_wait_ms;
    hux_led_press_t press;
    bool press_on, was_held, voice_mode, demo;
    uint32_t press_since, prev;
    float v_max; /* the press's loudest level, for tuning the mic mapping */
} hux_led_engine_t;

/** After the task slept: the first frame moves a frame's worth, not 0. */
void hux_led_engine_wake(hux_led_engine_t *e, uint32_t now);

/** One frame: the startup light's colours (Rainbow, or the walk in white,
 * which replaces it) and the press's brightness (0 when off), by position,
 * and the events to act on (HUX_LED_EV_*). */
unsigned hux_led_engine_step(hux_led_engine_t *e, const hux_led_in_t *in,
                             float boot_rgb[HUX_LED_COUNT][3], float b_press[HUX_LED_COUNT]);

/** Something shows or waits to: keep rendering frames; else sleep. */
bool hux_led_engine_active(const hux_led_engine_t *e);

/** A mic frame's mean square (PCM16) to 0..1: -50 dBFS RMS and under is 0,
 * -15 dBFS and over is 1, linear in dB between. */
float hux_led_mic_level(uint32_t mean_square);

/** The WS2812B data chain doesn't follow the positions: GPIO38 feeds U9
 * (at 102.9 deg, position 2) first, then clockwise U19, U14 (+x, the
 * bottom), U13, U12, U11, U10 (schematic; angles from Waveshare's STEP).
 * Chain index of position p: {2, 1, 0, 6, 5, 4, 3}. */
extern const uint8_t HUX_LED_CHAIN_OF[HUX_LED_COUNT];

/** Pixels by position to the order the chain takes them. */
void hux_led_chain(const uint8_t pos[HUX_LED_COUNT][3], uint8_t chain[HUX_LED_COUNT][3]);

/** The positions behind the battery holders (1, 2, 5, 6), seen only as
 * spill through the case: their light is multiplied by a gain (console
 * `led gain`, 3.0 by default) so a climb reads as evenly as the LEDs seen
 * directly (0, 3, 4). */
bool hux_led_hidden(int p);

/** Colours (0..1 per channel) to pixels: each channel x 255 x `scale` (the
 * peak: 0.25 is a quarter of full), x `hidden_gain` on the hidden
 * positions, rounded and clipped to 0..255. */
void hux_led_pixels(const float rgb[HUX_LED_COUNT][3], float scale, float hidden_gain,
                    uint8_t out[HUX_LED_COUNT][3]);

#ifdef __cplusplus
}
#endif

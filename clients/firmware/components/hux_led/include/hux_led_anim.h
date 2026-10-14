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
} hux_led_in_t;

enum {
    HUX_LED_EV_BOOT = 1,      /* Orbit is starting: take the peak, log */
    HUX_LED_EV_PRESS = 2,     /* a press light starts */
    HUX_LED_EV_PRESS_END = 4, /* it went out */
    HUX_LED_EV_DEMO_OVER = 8, /* clear demo_until (it would come round on a wrap) */
};

typedef struct {
    bool boot_waiting, boot_on, boot_heard;
    uint32_t boot_since, boot_start, boot_p0, boot_wait_ms;
    hux_led_press_t press;
    bool press_on, was_held, voice_mode, demo;
    uint32_t press_since, prev;
    float v_max; /* the press's loudest level, for tuning the mic mapping */
} hux_led_engine_t;

/** After the task slept: the first frame moves a frame's worth, not 0. */
void hux_led_engine_wake(hux_led_engine_t *e, uint32_t now);

/** One frame: Orbit's and the press's brightness (both 0 when off) and
 * the events to act on (HUX_LED_EV_*). */
unsigned hux_led_engine_step(hux_led_engine_t *e, const hux_led_in_t *in,
                             float b_boot[HUX_LED_COUNT], float b_press[HUX_LED_COUNT]);

/** Something shows or waits to: keep rendering frames; else sleep. */
bool hux_led_engine_active(const hux_led_engine_t *e);

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

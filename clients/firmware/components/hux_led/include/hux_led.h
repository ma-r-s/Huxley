/**
 * hux_led: the light under the clear back (F-0034), for people around the
 * walkie (its user can't see it). The board's 7 WS2812B on GPIO38, driven
 * through the RMT by a low-priority task that sleeps whenever it's dark;
 * every call here only sets a flag and wakes it, so nothing on the audio or
 * press path ever waits for a light.
 *
 * Mario's pick (frames: hux_led_anim.h): at boot "Orbit" in warm white,
 * started when the startup sound reaches the speaker, so it follows the
 * sound's boot rule (F-0033: a real power on or after the battery park);
 * while the lever is held "Voice" in white (the light climbs with the mic
 * level), or "Glow" for a press the walkie can't act on (offline). Peak
 * brightness `led bright <pct>` (saved; 25 % of full by default), a third
 * of it at night (22:00-07:00 Bogota, once the clock is known). Logs
 * `led.anim kind=boot|press mode=orbit|voice|glow`.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** From app_main, before the app task can play the startup sound: the
 * strip (cleared: a restart mid-animation would leave it lit), the saved
 * brightness, the task. A failure is logged and the walkie runs dark. */
void hux_led_start(void);

/** The startup sound was just queued; `played_ms_before` is
 * hux_audio_played_ms() from before it was: Orbit starts when that
 * counter moves (the sound left the ring) plus the I2S queue ahead of it. */
void hux_led_boot(uint32_t played_ms_before);

/** The lever went down (`voice`: the press was acted on, so the mic runs)
 * or up. */
void hux_led_press(bool held, bool voice);

/** From the mic sink, each 20 ms frame: its mean square (PCM16). */
void hux_led_mic(uint32_t mean_square);

/** Console: `orbit`, `glow` or `voice` (3 s, a made-up sentence for the
 * level), to look at them without the startup sound or a real press; or
 * `walk`: each position alone for 0.6 s, bottom first, to check the order. */
void hux_led_demo(const char *what);

/** Console `led bright <pct>`: the peak, 1-100 % of full, saved in NVS. */
void hux_led_set_bright(int pct);

#ifdef __cplusplus
}
#endif

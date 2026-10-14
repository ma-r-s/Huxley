#include "hux_led_anim.h"

#include <math.h>

static float clamp01(float v) {
    return v < 0.0f ? 0.0f : v > 1.0f ? 1.0f : v;
}

/* The mockup's hsv(): hue in degrees, s and v 0..1. */
static void hsv(float h, float s, float v, float rgb[3]) {
    h = fmodf(fmodf(h, 360.0f) + 360.0f, 360.0f) / 60.0f;
    float c = v * s;
    float x = c * (1.0f - fabsf(fmodf(h, 2.0f) - 1.0f));
    float m = v - c;
    float r, g, b;
    if (h < 1.0f) {
        r = c, g = x, b = 0.0f;
    } else if (h < 2.0f) {
        r = x, g = c, b = 0.0f;
    } else if (h < 3.0f) {
        r = 0.0f, g = c, b = x;
    } else if (h < 4.0f) {
        r = 0.0f, g = x, b = c;
    } else if (h < 5.0f) {
        r = x, g = 0.0f, b = c;
    } else {
        r = c, g = 0.0f, b = x;
    }
    rgb[0] = r + m;
    rgb[1] = g + m;
    rgb[2] = b + m;
}

/* Fade in over `up`, hold, fade out over `down` (squared). */
static float envelope(float t, float up, float hold, float down) {
    if (t < up) {
        return t / up;
    }
    if (t < up + hold) {
        return 1.0f;
    }
    float v = clamp01(1.0f - (t - up - hold) / down);
    return v * v;
}

bool hux_led_rainbow(uint32_t t_ms, float rgb[HUX_LED_COUNT][3]) {
    if (t_ms > HUX_LED_BOOT_MS) {
        for (int i = 0; i < HUX_LED_COUNT; i++) {
            rgb[i][0] = rgb[i][1] = rgb[i][2] = 0.0f;
        }
        return false;
    }
    float t = (float)t_ms;
    float v = envelope(t, 200.0f, 700.0f, 700.0f);
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        hsv((float)i * 360.0f / HUX_LED_COUNT + t * 0.24f, 0.8f, v, rgb[i]);
    }
    return true;
}

bool hux_led_press_step(hux_led_press_t *p, float dt_ms, bool held, bool voice_mode, float mic,
                        float b[HUX_LED_COUNT]) {
    float target = held ? 1.0f : 0.0f;
    p->level += (target - p->level) * (1.0f - expf(-dt_ms / (held ? 110.0f : 240.0f)));
    float v_target = held && voice_mode ? mic : 0.0f;
    p->voice += (v_target - p->voice) * (1.0f - expf(-dt_ms / 70.0f));
    if (!held && p->level < 0.003f) { /* out only after release: a first frame's dt is 0 */
        for (int i = 0; i < HUX_LED_COUNT; i++) {
            b[i] = 0.0f;
        }
        return false;
    }
    /* Mario, 2026-10-14: the whole ring at once, its intensity following
     * the voice (Voice), or steady (Glow). */
    float v = voice_mode ? p->level * (0.15f + 0.85f * clamp01(p->voice)) : 0.42f * p->level;
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        b[i] = v;
    }
    return true;
}

float hux_led_mic_level(uint32_t mean_square) {
    if (mean_square == 0) {
        return 0.0f;
    }
    float dbfs = 10.0f * log10f((float)mean_square / (32768.0f * 32768.0f));
    return clamp01((dbfs + 50.0f) / 35.0f);
}

const uint8_t HUX_LED_CHAIN_OF[HUX_LED_COUNT] = {2, 1, 0, 6, 5, 4, 3};

void hux_led_chain(const uint8_t pos[HUX_LED_COUNT][3], uint8_t chain[HUX_LED_COUNT][3]) {
    for (int p = 0; p < HUX_LED_COUNT; p++) {
        for (int c = 0; c < 3; c++) {
            chain[HUX_LED_CHAIN_OF[p]][c] = pos[p][c];
        }
    }
}

bool hux_led_hidden(int p) {
    return p == 1 || p == 2 || p == 5 || p == 6;
}

void hux_led_pixels(const float rgb[HUX_LED_COUNT][3], float scale, float hidden_gain,
                    uint8_t out[HUX_LED_COUNT][3]) {
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        float gain = hux_led_hidden(i) ? hidden_gain : 1.0f;
        for (int c = 0; c < 3; c++) {
            float v = 255.0f * clamp01(rgb[i][c]) * gain * scale + 0.5f;
            out[i][c] = (uint8_t)(v > 255.0f ? 255.0f : v);
        }
    }
}

/* -- The frame engine ------------------------------------------------- */

/* The mockup's made-up sentence: syllables that drive the demo's level. */
static float demo_syllables(uint32_t since) {
    float s = (float)since;
    float v = sinf(s / 95.0f) * sinf(s / 410.0f + 1.0f) * 0.9f + 0.15f * sinf(s / 37.0f);
    return v > 0.0f ? v : 0.0f;
}

void hux_led_engine_wake(hux_led_engine_t *e, uint32_t now) {
    e->prev = now - HUX_LED_FRAME_MS;
}

bool hux_led_engine_active(const hux_led_engine_t *e) {
    return e->boot_waiting || e->boot_on || e->walk_on || e->press_on;
}

unsigned hux_led_engine_step(hux_led_engine_t *e, const hux_led_in_t *in,
                             float boot_rgb[HUX_LED_COUNT][3], float b_press[HUX_LED_COUNT]) {
    unsigned ev = 0;
    uint32_t now = in->now;
    float dt = (float)(now - e->prev);
    e->prev = now;
    if (dt > 64.0f) {
        dt = 64.0f; /* as the mockup: a late frame doesn't jump */
    }
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        boot_rgb[i][0] = boot_rgb[i][1] = boot_rgb[i][2] = 0.0f;
        b_press[i] = 0.0f;
    }

    if (in->boot_req) {
        e->boot_waiting = true;
        e->boot_on = false;
        e->boot_since = now;
        e->boot_p0 = in->boot_p0;
    }
    if (e->boot_waiting) {
        /* The sound left the ring when the played counter moved; it is
         * heard once the I2S queue ahead of it has gone out. */
        bool heard = in->boot_wait && in->played_ms != e->boot_p0;
        if (!in->boot_wait || heard || now - e->boot_since >= HUX_LED_SOUND_WAIT_MS) {
            e->boot_waiting = false;
            e->boot_on = true;
            e->boot_heard = heard;
            e->boot_wait_ms = now - e->boot_since;
            e->boot_start = now + (heard ? HUX_LED_SOUND_LAG_MS : 0);
            ev |= HUX_LED_EV_BOOT;
        }
    }
    if (in->walk_req) {
        e->walk_on = true;
        e->walk_start = now;
        e->boot_waiting = false;
        e->boot_on = false; /* the walk replaces the startup light */
        ev |= HUX_LED_EV_WALK;
    }
    if (e->boot_on) {
        int32_t t = (int32_t)(now - e->boot_start);
        if (t >= 0 && !hux_led_rainbow((uint32_t)t, boot_rgb)) {
            e->boot_on = false;
        }
    }
    if (e->walk_on) {
        uint32_t p = (now - e->walk_start) / HUX_LED_WALK_STEP_MS;
        if (p < HUX_LED_COUNT) {
            boot_rgb[p][0] = boot_rgb[p][1] = boot_rgb[p][2] = 1.0f; /* white */
        } else {
            e->walk_on = false;
        }
    }

    bool demo = in->demo_until != 0 && (int32_t)(in->demo_until - now) > 0;
    if (in->demo_until != 0 && !demo) {
        ev |= HUX_LED_EV_DEMO_OVER;
    }
    bool held = in->held || demo;
    if (held && !e->was_held) {
        e->press_on = true;
        e->voice_mode = in->voice;
        e->demo = demo;
        e->press_since = now;
        e->v_max = 0.0f;
        ev |= HUX_LED_EV_PRESS;
    }
    e->was_held = held;
    if (e->press_on) {
        float mic = 0.0f;
        int32_t age = (int32_t)(now - in->mic_at); /* the mic task may be a tick ahead */
        if (e->demo) {
            mic = demo_syllables(now - e->press_since);
        } else if (age < (int32_t)HUX_LED_MIC_STALE_MS && age > -1000) {
            mic = hux_led_mic_level(in->mic_ms);
        }
        if (held && mic > e->v_max) {
            e->v_max = mic;
        }
        if (!hux_led_press_step(&e->press, dt, held, e->voice_mode, mic, b_press)) {
            e->press_on = false;
            e->press.voice = 0.0f;
            ev |= HUX_LED_EV_PRESS_END;
        }
    }
    return ev;
}

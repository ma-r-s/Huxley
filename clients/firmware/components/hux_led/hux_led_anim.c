#include "hux_led_anim.h"

#include <math.h>

/* Height step of each LED: bottom, low, mid, top (the mockup's LEVEL). */
static const int LEVEL[HUX_LED_COUNT] = {0, 1, 2, 3, 3, 2, 1};

static float clamp01(float v) {
    return v < 0.0f ? 0.0f : v > 1.0f ? 1.0f : v;
}

/* A sharp rise over `attack` from `t0`, then an exponential decay. */
static float pulse(float t, float t0, float attack, float decay) {
    if (t < t0) {
        return 0.0f;
    }
    float d = t - t0;
    return d < attack ? d / attack : expf(-(d - attack) / decay);
}

/* A soft swell from `t0` that is gone by `end`. */
static float after(float t, float t0, float rise, float end) {
    if (t < t0) {
        return 0.0f;
    }
    if (t < t0 + rise) {
        return (t - t0) / rise;
    }
    float v = clamp01(1.0f - (t - t0 - rise) / (end - t0 - rise));
    return v * v;
}

bool hux_led_orbit(uint32_t t_ms, float b[HUX_LED_COUNT]) {
    if (t_ms > HUX_LED_ORBIT_MS) {
        for (int i = 0; i < HUX_LED_COUNT; i++) {
            b[i] = 0.0f;
        }
        return false;
    }
    float t = (float)t_ms;
    float u = 1.0f - clamp01(t / 460.0f);
    float head = 180.0f * (1.0f - u * u * u); /* bottom to top, easing out */
    float flash = pulse(t, 470.0f, 30.0f, 420.0f);
    float rest = 0.3f * after(t, 480.0f, 120.0f, 1350.0f);
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        float ang = (float)i * 360.0f / HUX_LED_COUNT;
        float a = ang <= 180.0f ? ang : 360.0f - ang; /* each arm climbs its own side */
        float d = head - a;
        float arm = 0.0f;
        if (t <= 520.0f) {
            float w = d >= 0.0f ? d / 70.0f : d / 22.0f;
            arm = expf(-(w * w));
        }
        b[i] = clamp01(0.85f * arm + (LEVEL[i] == 3 ? flash : 0.5f * flash) + rest);
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
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        if (!voice_mode) {
            b[i] = 0.42f * p->level; /* Glow */
            continue;
        }
        static const float NEED[4] = {0.0f, 0.25f, 0.5f, 0.75f};
        float lit = clamp01((p->voice - NEED[LEVEL[i]]) / 0.2f);
        b[i] = p->level * clamp01((LEVEL[i] == 0 ? 0.25f : 0.0f) + 0.7f * lit);
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

void hux_led_pixels(const float b[HUX_LED_COUNT], const uint8_t colour[3], float scale,
                    uint8_t out[HUX_LED_COUNT][3]) {
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        for (int c = 0; c < 3; c++) {
            float v = (float)colour[c] * clamp01(b[i]) * scale + 0.5f;
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
    return e->boot_waiting || e->boot_on || e->press_on;
}

unsigned hux_led_engine_step(hux_led_engine_t *e, const hux_led_in_t *in,
                             float b_boot[HUX_LED_COUNT], float b_press[HUX_LED_COUNT]) {
    unsigned ev = 0;
    uint32_t now = in->now;
    float dt = (float)(now - e->prev);
    e->prev = now;
    if (dt > 64.0f) {
        dt = 64.0f; /* as the mockup: a late frame doesn't jump */
    }
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        b_boot[i] = 0.0f;
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
    if (e->boot_on) {
        int32_t t = (int32_t)(now - e->boot_start);
        if (t >= 0 && !hux_led_orbit((uint32_t)t, b_boot)) {
            e->boot_on = false;
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

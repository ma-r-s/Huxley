#include "hux_led.h"

#include <math.h>
#include <stdatomic.h>
#include <string.h>
#include <time.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "led_strip.h"
#include "nvs.h"

#include "hux_alert_logic.h"
#include "hux_audio.h"
#include "hux_led_anim.h"

static const char *TAG = "hux_led";

#define LED_GPIO        38   /* bsp_board.h LED_STRIP_GPIO_PIN */
#define FRAME_MS        16
#define SOUND_LAG_MS    60   /* the I2S DMA queue ahead of a chunk: 6 x 10 ms */
#define SOUND_WAIT_MS   1000 /* the sound never moved: start anyway */
#define MIC_STALE_MS    100  /* no frame this long: the mic stopped, level 0 */
#define DEMO_MS         3000
#define BRIGHT_DEFAULT  25
#define TASK_PRIO       2    /* under everything that makes or moves sound */
#define TASK_STACK      3072
#define NVS_NS          "hux_led"
#define NVS_KEY_BRIGHT  "pct"

static const uint8_t WARM[3] = {255, 206, 158};  /* Orbit */
static const uint8_t WHITE[3] = {236, 240, 255}; /* Voice, Glow */

static led_strip_handle_t s_strip;
static TaskHandle_t s_task;
static _Atomic int s_bright = BRIGHT_DEFAULT;
static atomic_bool s_boot_req = false;
static _Atomic uint32_t s_boot_p0 = 0;
static atomic_bool s_boot_wait = true; /* false: a demo, no sound to wait for */
static atomic_bool s_held = false;
static atomic_bool s_voice = false;
static _Atomic uint32_t s_mic_ms = 0; /* mean square of the last mic frame */
static _Atomic uint32_t s_mic_at = 0; /* when it came */
static _Atomic uint32_t s_demo_until = 0;

static uint32_t now_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void wake(void) {
    if (s_task != NULL) {
        xTaskNotifyGive(s_task);
    }
}

/* The peak for an animation starting now: a third at night. */
static float peak_now(bool *night) {
    *night = hux_alert_quiet((int64_t)time(NULL));
    float scale = (float)atomic_load(&s_bright) / 100.0f;
    return *night ? scale / 3.0f : scale;
}

/* The mockup's made-up sentence: syllables that drive the demo's level. */
static float demo_syllables(uint32_t since) {
    float s = (float)since;
    float v = sinf(s / 95.0f) * sinf(s / 410.0f + 1.0f) * 0.9f + 0.15f * sinf(s / 37.0f);
    return v > 0.0f ? v : 0.0f;
}

static void show(uint8_t px[HUX_LED_COUNT][3]) {
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        led_strip_set_pixel(s_strip, (uint32_t)i, px[i][0], px[i][1], px[i][2]);
    }
    esp_err_t err = led_strip_refresh(s_strip);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "led.refresh_failed err=%s", esp_err_to_name(err));
    }
}

static void led_task(void *arg) {
    (void)arg;
    bool boot_waiting = false, boot_on = false;
    uint32_t boot_since = 0, boot_start = 0, boot_p0 = 0;
    float boot_peak = 0.0f, press_peak = 0.0f;
    hux_led_press_t press = {0};
    bool press_on = false, was_held = false, voice_mode = false;
    uint32_t press_since = 0;
    float v_max = 0.0f;
    uint8_t shown[HUX_LED_COUNT][3];
    memset(shown, 0, sizeof(shown));
    uint32_t prev = now_ms();
    TickType_t wake_at = xTaskGetTickCount();
    for (;;) {
        if (!boot_waiting && !boot_on && !press_on) {
            ulTaskNotifyTake(pdTRUE, portMAX_DELAY); /* dark: sleep until asked */
            prev = now_ms();
            wake_at = xTaskGetTickCount();
        }
        uint32_t now = now_ms();
        float dt = (float)(now - prev);
        prev = now;
        if (dt > 64.0f) {
            dt = 64.0f; /* as the mockup: a late frame doesn't jump */
        }

        if (atomic_exchange(&s_boot_req, false)) {
            boot_waiting = true;
            boot_on = false;
            boot_since = now;
            boot_p0 = atomic_load(&s_boot_p0);
        }
        if (boot_waiting) {
            bool wait = atomic_load(&s_boot_wait);
            bool heard = wait && hux_audio_played_ms() > boot_p0;
            if (!wait || heard || now - boot_since >= SOUND_WAIT_MS) {
                bool night;
                boot_peak = peak_now(&night);
                boot_waiting = false;
                boot_on = true;
                boot_start = now + (heard ? SOUND_LAG_MS : 0);
                ESP_LOGI(TAG, "led.anim kind=boot mode=orbit wait_ms=%u heard=%d night=%d pct=%d",
                         (unsigned)(now - boot_since), heard, night, atomic_load(&s_bright));
            }
        }

        uint32_t demo_until = atomic_load(&s_demo_until);
        bool demo = demo_until != 0 && (int32_t)(demo_until - now) > 0;
        if (demo_until != 0 && !demo) {
            /* Over: cleared, or the deadline would come round again when
             * the ms counter wraps (unless a new demo just replaced it). */
            atomic_compare_exchange_strong(&s_demo_until, &demo_until, 0);
        }
        bool held = atomic_load(&s_held) || demo;
        if (held && !was_held) {
            bool night;
            press_peak = peak_now(&night);
            voice_mode = atomic_load(&s_voice);
            press_on = true;
            press_since = now;
            v_max = 0.0f;
            ESP_LOGI(TAG, "led.anim kind=press mode=%s night=%d pct=%d demo=%d",
                     voice_mode ? "voice" : "glow", night, atomic_load(&s_bright), demo);
        }
        was_held = held;

        float b_boot[HUX_LED_COUNT] = {0}, b_press[HUX_LED_COUNT] = {0};
        if (boot_on) {
            int32_t t = (int32_t)(now - boot_start);
            if (t >= 0 && !hux_led_orbit((uint32_t)t, b_boot)) {
                boot_on = false;
            }
        }
        if (press_on) {
            float mic = 0.0f;
            if (demo) {
                mic = demo_syllables(now - press_since);
            } else if (now - atomic_load(&s_mic_at) < MIC_STALE_MS) {
                mic = hux_led_mic_level(atomic_load(&s_mic_ms));
            }
            if (held && mic > v_max) {
                v_max = mic;
            }
            if (!hux_led_press_step(&press, dt, held, voice_mode, mic, b_press)) {
                press_on = false;
                press.voice = 0.0f;
                ESP_LOGI(TAG, "led.anim.end kind=press mode=%s ms=%u v_max_pct=%d",
                         voice_mode ? "voice" : "glow", (unsigned)(now - press_since),
                         (int)(v_max * 100.0f + 0.5f));
            }
        }

        /* Both at once (a press during the boot light): the brighter of
         * the two, channel by channel, as the mockup combines them. */
        uint8_t px[HUX_LED_COUNT][3], pp[HUX_LED_COUNT][3];
        hux_led_pixels(b_boot, WARM, boot_peak, px);
        hux_led_pixels(b_press, WHITE, press_peak, pp);
        for (int i = 0; i < HUX_LED_COUNT; i++) {
            for (int c = 0; c < 3; c++) {
                if (pp[i][c] > px[i][c]) {
                    px[i][c] = pp[i][c];
                }
            }
        }
        if (memcmp(px, shown, sizeof(px)) != 0) {
            show(px);
            memcpy(shown, px, sizeof(px));
        }
        if (boot_waiting || boot_on || press_on) {
            vTaskDelayUntil(&wake_at, pdMS_TO_TICKS(FRAME_MS));
        }
    }
}

void hux_led_start(void) {
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        uint8_t pct = 0;
        if (nvs_get_u8(h, NVS_KEY_BRIGHT, &pct) == ESP_OK && pct >= 1 && pct <= 100) {
            atomic_store(&s_bright, pct);
        }
        nvs_close(h);
    }
    led_strip_config_t cfg = {
        .strip_gpio_num = LED_GPIO,
        .max_leds = HUX_LED_COUNT,
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
    };
    /* DMA: the waveform doesn't depend on interrupts being served in time
     * (Wi-Fi's are many), which would flicker the colours. */
    led_strip_rmt_config_t rmt = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
        .flags.with_dma = true,
    };
    esp_err_t err = led_strip_new_rmt_device(&cfg, &rmt, &s_strip);
    bool dma = err == ESP_OK;
    if (!dma) {
        rmt.flags.with_dma = false;
        err = led_strip_new_rmt_device(&cfg, &rmt, &s_strip);
    }
    if (err == ESP_OK) {
        err = led_strip_clear(s_strip);
    }
    if (err != ESP_OK || xTaskCreate(led_task, "hux_led", TASK_STACK, NULL, TASK_PRIO, &s_task) != pdPASS) {
        ESP_LOGE(TAG, "led.start_failed err=%s: the walkie runs dark", esp_err_to_name(err));
        s_task = NULL;
        return;
    }
    ESP_LOGI(TAG, "led.started gpio=%d dma=%d pct=%d", LED_GPIO, dma, atomic_load(&s_bright));
}

static void boot_request(uint32_t p0, bool wait) {
    atomic_store(&s_boot_p0, p0);
    atomic_store(&s_boot_wait, wait);
    atomic_store(&s_boot_req, true);
    wake();
}

void hux_led_boot(uint32_t played_ms_before) {
    boot_request(played_ms_before, true);
}

void hux_led_press(bool held, bool voice) {
    if (held) {
        atomic_store(&s_voice, voice);
    }
    atomic_store(&s_held, held);
    wake();
}

void hux_led_mic(uint32_t mean_square) {
    atomic_store(&s_mic_ms, mean_square);
    atomic_store(&s_mic_at, now_ms());
}

void hux_led_demo(const char *what) {
    if (strcmp(what, "orbit") == 0) {
        boot_request(0, false);
        return;
    }
    bool voice = strcmp(what, "voice") == 0;
    if (!voice && strcmp(what, "glow") != 0) {
        ESP_LOGW(TAG, "led.demo unknown=%s (orbit, glow, voice)", what);
        return;
    }
    atomic_store(&s_voice, voice);
    uint32_t until = now_ms() + DEMO_MS;
    atomic_store(&s_demo_until, until == 0 ? 1 : until);
    wake();
}

void hux_led_set_bright(int pct) {
    if (pct < 1 || pct > 100) {
        ESP_LOGW(TAG, "led.bright refused pct=%d (1-100)", pct);
        return;
    }
    atomic_store(&s_bright, pct);
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_u8(h, NVS_KEY_BRIGHT, (uint8_t)pct);
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    ESP_LOGI(TAG, "led.bright pct=%d saved=%s", pct, esp_err_to_name(err));
}

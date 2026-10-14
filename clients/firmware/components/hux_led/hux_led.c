#include "hux_led.h"

#include <stdatomic.h>
#include <string.h>
#include <time.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/task.h"
#include "led_strip.h"
#include "nvs.h"

#include "hux_alert_logic.h"
#include "hux_audio.h"
#include "hux_led_anim.h"

static const char *TAG = "hux_led";

#define LED_GPIO        38   /* bsp_board.h LED_STRIP_GPIO_PIN */
#define DEMO_MS         3000
#define BRIGHT_DEFAULT  25
#define TASK_PRIO       2    /* under everything that makes or moves sound */
#define TASK_STACK      4096 /* PSRAM; logging with floats around (the critic's count: ~2.6 KB) */
#define REFRESH_RETRIES 30   /* a failed refresh is tried again for ~0.5 s */
/* With DMA, room for a whole frame (7 x 24 bits + the reset) in one buffer:
 * no refill interrupt mid-frame for Wi-Fi's interrupts to delay. */
#define DMA_SYMBOLS     256
#define NVS_NS          "hux_led"
#define NVS_KEY_BRIGHT  "pct"
#define NVS_KEY_GAIN    "gain10"
#define GAIN10_DEFAULT  30   /* x3.0 behind the holders */

static const float WHITE[3] = {236.0f / 255.0f, 240.0f / 255.0f, 1.0f}; /* Voice, Glow */

static led_strip_handle_t s_strip;
static TaskHandle_t s_task;
static _Atomic int s_bright = BRIGHT_DEFAULT;
static _Atomic int s_gain10 = GAIN10_DEFAULT; /* the hidden positions' gain, x10 */
static atomic_bool s_boot_req = false;
static _Atomic uint32_t s_boot_p0 = 0;
static atomic_bool s_boot_wait = true; /* false: a demo, no sound to wait for */
static atomic_bool s_held = false;
static atomic_bool s_voice = false;
static _Atomic uint32_t s_mic_ms = 0; /* mean square of the last mic frame */
static _Atomic uint32_t s_mic_at = 0; /* when it came */
static _Atomic uint32_t s_demo_until = 0;
static atomic_bool s_walk_req = false;

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

/* `px` by position; the strip takes them in its chain's order. */
static bool show(uint8_t px[HUX_LED_COUNT][3]) {
    uint8_t chain[HUX_LED_COUNT][3];
    hux_led_chain(px, chain);
    for (int i = 0; i < HUX_LED_COUNT; i++) {
        led_strip_set_pixel(s_strip, (uint32_t)i, chain[i][0], chain[i][1], chain[i][2]);
    }
    return led_strip_refresh(s_strip) == ESP_OK;
}

static void led_task(void *arg) {
    (void)arg;
    hux_led_engine_t e;
    memset(&e, 0, sizeof(e));
    float boot_peak = 0.0f, press_peak = 0.0f;
    uint8_t shown[HUX_LED_COUNT][3];
    memset(shown, 0, sizeof(shown));
    int retries = 0;
    TickType_t wake_at = xTaskGetTickCount();
    for (;;) {
        if (!hux_led_engine_active(&e) && retries == 0) {
            ulTaskNotifyTake(pdTRUE, portMAX_DELAY); /* dark: sleep until asked */
            hux_led_engine_wake(&e, now_ms());
            wake_at = xTaskGetTickCount();
        }
        hux_led_in_t in = {
            .now = now_ms(),
            .played_ms = hux_audio_played_ms(),
            .boot_req = atomic_exchange(&s_boot_req, false),
            .boot_p0 = atomic_load(&s_boot_p0),
            .boot_wait = atomic_load(&s_boot_wait),
            .held = atomic_load(&s_held),
            .voice = atomic_load(&s_voice),
            .mic_ms = atomic_load(&s_mic_ms),
            .mic_at = atomic_load(&s_mic_at),
            .demo_until = atomic_load(&s_demo_until),
            .walk_req = atomic_exchange(&s_walk_req, false),
        };
        float boot_rgb[HUX_LED_COUNT][3], b_press[HUX_LED_COUNT];
        unsigned ev = hux_led_engine_step(&e, &in, boot_rgb, b_press);
        if (ev & HUX_LED_EV_BOOT) {
            bool night;
            boot_peak = peak_now(&night);
            ESP_LOGI(TAG, "led.anim kind=boot mode=rainbow wait_ms=%u heard=%d night=%d pct=%d",
                     (unsigned)e.boot_wait_ms, e.boot_heard, night, atomic_load(&s_bright));
        }
        if (ev & HUX_LED_EV_WALK) {
            bool night;
            boot_peak = peak_now(&night);
            ESP_LOGI(TAG, "led.anim kind=walk step_ms=%u night=%d pct=%d (positions 0-6, bottom first)",
                     (unsigned)HUX_LED_WALK_STEP_MS, night, atomic_load(&s_bright));
        }
        if (ev & HUX_LED_EV_PRESS) {
            bool night;
            press_peak = peak_now(&night);
            ESP_LOGI(TAG, "led.anim kind=press mode=%s night=%d pct=%d demo=%d",
                     e.voice_mode ? "voice" : "glow", night, atomic_load(&s_bright), e.demo);
        }
        if (ev & HUX_LED_EV_PRESS_END) {
            ESP_LOGI(TAG, "led.anim.end kind=press mode=%s ms=%u v_max_pct=%d",
                     e.voice_mode ? "voice" : "glow", (unsigned)(in.now - e.press_since),
                     (int)(e.v_max * 100.0f + 0.5f));
        }
        if (ev & HUX_LED_EV_DEMO_OVER) {
            /* Unless a new demo just replaced it. */
            atomic_compare_exchange_strong(&s_demo_until, &in.demo_until, 0);
        }

        /* Both at once (a press during the boot light): the brighter of
         * the two, channel by channel, as the mockup combines them. */
        uint8_t px[HUX_LED_COUNT][3], pp[HUX_LED_COUNT][3];
        float gain = (float)atomic_load(&s_gain10) / 10.0f;
        float press_rgb[HUX_LED_COUNT][3];
        for (int i = 0; i < HUX_LED_COUNT; i++) {
            for (int c = 0; c < 3; c++) {
                press_rgb[i][c] = WHITE[c] * b_press[i];
            }
        }
        hux_led_pixels(boot_rgb, boot_peak, gain, px);
        hux_led_pixels(press_rgb, press_peak, gain, pp);
        for (int i = 0; i < HUX_LED_COUNT; i++) {
            for (int c = 0; c < 3; c++) {
                if (pp[i][c] > px[i][c]) {
                    px[i][c] = pp[i][c];
                }
            }
        }
        if (memcmp(px, shown, sizeof(px)) != 0) {
            if (show(px)) {
                memcpy(shown, px, sizeof(px));
                retries = 0;
            } else if (++retries >= REFRESH_RETRIES) {
                /* Logged once per run of failures; the next change tries again. */
                ESP_LOGW(TAG, "led.refresh_failed tries=%d", retries);
                retries = 0;
            }
        }
        if (hux_led_engine_active(&e) || retries > 0) {
            vTaskDelayUntil(&wake_at, pdMS_TO_TICKS(HUX_LED_FRAME_MS));
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
        uint8_t g10 = 0;
        if (nvs_get_u8(h, NVS_KEY_GAIN, &g10) == ESP_OK && g10 >= 10 && g10 <= 100) {
            atomic_store(&s_gain10, g10);
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
        .mem_block_symbols = DMA_SYMBOLS,
        .flags.with_dma = true,
    };
    esp_err_t err = led_strip_new_rmt_device(&cfg, &rmt, &s_strip);
    bool dma = err == ESP_OK;
    if (!dma) {
        rmt.flags.with_dma = false;
        rmt.mem_block_symbols = 0; /* the default block */
        err = led_strip_new_rmt_device(&cfg, &rmt, &s_strip);
    }
    if (err == ESP_OK) {
        err = led_strip_clear(s_strip);
    }
    if (err != ESP_OK || xTaskCreateWithCaps(led_task, "hux_led", TASK_STACK, NULL, TASK_PRIO,
                                             &s_task, MALLOC_CAP_SPIRAM) != pdPASS) {
        ESP_LOGE(TAG, "led.start_failed err=%s: the walkie runs dark", esp_err_to_name(err));
        s_task = NULL;
        return;
    }
    int g10 = atomic_load(&s_gain10);
    ESP_LOGI(TAG, "led.started gpio=%d dma=%d pct=%d hidden_gain=%d.%d", LED_GPIO, dma,
             atomic_load(&s_bright), g10 / 10, g10 % 10);
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
    if (strcmp(what, "rainbow") == 0 || strcmp(what, "boot") == 0) {
        boot_request(0, false);
        return;
    }
    if (strcmp(what, "walk") == 0) {
        atomic_store(&s_walk_req, true);
        wake();
        return;
    }
    bool voice = strcmp(what, "voice") == 0;
    if (!voice && strcmp(what, "glow") != 0) {
        ESP_LOGW(TAG, "led.demo unknown=%s (rainbow, glow, voice, walk)", what);
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

void hux_led_set_gain(int tenths) {
    if (tenths < 10 || tenths > 100) {
        ESP_LOGW(TAG, "led.gain refused tenths=%d (1.0-10.0)", tenths);
        return;
    }
    atomic_store(&s_gain10, tenths);
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_u8(h, NVS_KEY_GAIN, (uint8_t)tenths);
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    ESP_LOGI(TAG, "led.gain hidden=%d.%d saved=%s", tenths / 10, tenths % 10, esp_err_to_name(err));
}

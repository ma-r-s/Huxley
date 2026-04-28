#include "hux_button.h"

#include <stdbool.h>
#include <stdint.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "hux_app.h"
#include "hux_board.h"

static const char *TAG = "hux_button";

#define POLL_INTERVAL_MS  10
#define DEBOUNCE_SAMPLES  2   /* ~20 ms effective — below perceptible, above contact bounce */
#define TASK_STACK_BYTES  3072
#define TASK_PRIO         4   /* Below app_task (5); above idle. */

typedef struct {
    uint8_t pin;                           /* TCA9555 bit (0..15) */
    const char *name;                      /* for logs */
    hux_app_event_kind_t press_event;
    hux_app_event_kind_t release_event;
    bool pressed;                          /* current debounced state */
    uint8_t samples_against;               /* consecutive samples that disagree with `pressed` */
} button_t;

static button_t s_buttons[] = {
    {HUX_TCA_BTN_K2, "K2",
     HUX_APP_EV_BUTTON_K2_PRESSED, HUX_APP_EV_BUTTON_K2_RELEASED,
     false, 0},
    /* K1, K3 reserved — add entries here (and event kinds in
     * hux_app.h) when a consumer lands. */
};

static void button_poll_one(button_t *btn) {
    /* Buttons are active-low: logic-low on the expander input means
     * the button is pulled to ground, i.e. pressed. */
    bool pressed_now = !hux_board_tca_read(btn->pin);

    if (pressed_now == btn->pressed) {
        btn->samples_against = 0;
        return;
    }

    btn->samples_against++;
    if (btn->samples_against < DEBOUNCE_SAMPLES) {
        return; /* Too noisy to trust yet. */
    }

    btn->pressed = pressed_now;
    btn->samples_against = 0;

    hux_app_event_t ev = {
        .kind = pressed_now ? btn->press_event : btn->release_event,
    };
    if (!hux_app_post_event(&ev, false)) {
        /* Queue full — control plane is backed up. Logging here is
         * visible via the log streamer; app_task will catch up. */
        ESP_LOGW(TAG, "button.%s edge_dropped %s", btn->name,
                 pressed_now ? "press" : "release");
        return;
    }
    ESP_LOGI(TAG, "button.%s %s", btn->name,
             pressed_now ? "press" : "release");
}

static void button_task(void *unused) {
    (void)unused;
    ESP_LOGI(TAG, "button_task started poll=%dms debounce=%dsamples count=%u",
             POLL_INTERVAL_MS, DEBOUNCE_SAMPLES,
             (unsigned)(sizeof(s_buttons) / sizeof(s_buttons[0])));
    for (;;) {
        for (size_t i = 0; i < sizeof(s_buttons) / sizeof(s_buttons[0]); i++) {
            button_poll_one(&s_buttons[i]);
        }
        vTaskDelay(pdMS_TO_TICKS(POLL_INTERVAL_MS));
    }
}

void hux_button_start(void) {
    BaseType_t ok = xTaskCreate(button_task, "hux_button", TASK_STACK_BYTES,
                                NULL, TASK_PRIO, NULL);
    configASSERT(ok == pdPASS);
}

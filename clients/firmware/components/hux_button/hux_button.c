#include "hux_button.h"

#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>

#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "hux_app.h"
#include "hux_board.h"
#include "hux_debounce.h"

static const char *TAG = "hux_button";

#define POLL_INTERVAL_MS  10
#define DEBOUNCE_SAMPLES  2   /* ~20 ms effective — below perceptible, above contact bounce */
#define TASK_STACK_BYTES  4096  /* 3072 left 532 B free at idle (I2C + logging) */
#define TASK_PRIO         4   /* Below app_task (5); above idle. */

/* The HX-1 case's talk lever closes a switch from header pin 2 (GPIO7)
 * to GND (the enclosure README, "Talk input", on its branch). The
 * on-board K2 stays a talk input too, for the bare board on the bench.
 * Either one held is "talk"; the events keep their K2 names. */
#define TALK_GPIO         GPIO_NUM_7

static hux_debounce_t s_talk;

/* A third talk input with no hardware: the serial console's `talk` /
 * `release` (hux_console), so a bench test can hold talk from the Mac.
 * It goes through the same debounce as the lever and K2, so the three
 * together are still one button with one press and one release. */
static _Atomic bool s_serial_talk = false;

/* Active-low inputs. Reads BOTH expander ports every poll: that keeps the
 * expander's INT# (wired to GPIO0, the boot strap) released, so a reset
 * can't boot into download mode (F-0020). A read that fails on the I2C
 * bus counts as not pressed: an unreadable button must never open the
 * mic. Port 0 changes are logged (what else moves on the expander). */
static uint16_t s_last_inputs = 0;
static uint32_t s_port0_changes = 0;

static bool k2_pressed(void) {
    uint16_t in = 0;
    if (!hux_board_tca_read_inputs(&in)) {
        return false;
    }
    uint8_t p0 = in & 0xFF;
    uint8_t was = s_last_inputs & 0xFF;
    if (s_port0_changes > 0 && p0 != was && (s_port0_changes < 20 || s_port0_changes % 100 == 0)) {
        ESP_LOGI(TAG, "tca.port0 change 0x%02x -> 0x%02x (n=%u)", was, p0, (unsigned)s_port0_changes);
    }
    if (p0 != was || s_port0_changes == 0) {
        s_port0_changes++;
    }
    s_last_inputs = in;
    return !((in >> HUX_TCA_BTN_K2) & 1);
}

static bool lever_pressed(void) {
    return gpio_get_level(TALK_GPIO) == 0;
}

static void talk_poll(void) {
    bool k2 = k2_pressed();
    bool lever = lever_pressed();
    bool serial = atomic_load_explicit(&s_serial_talk, memory_order_relaxed);
    hux_edge_t edge = hux_debounce_step(&s_talk, k2 || lever || serial, DEBOUNCE_SAMPLES);
    if (edge == HUX_EDGE_NONE) {
        return;
    }
    bool press = edge == HUX_EDGE_PRESS;
    hux_app_event_t ev = {
        .kind = press ? HUX_APP_EV_BUTTON_K2_PRESSED : HUX_APP_EV_BUTTON_K2_RELEASED,
    };
    /* On a press, which input it was; on a release, what is still held
     * at that sample (nothing). */
    const char *src = lever && k2 ? "lever+k2"
                      : lever     ? "lever"
                      : k2        ? "k2"
                      : serial    ? "serial"
                                  : "-";
    if (!hux_app_post_event(&ev, false)) {
        /* Queue full — control plane is backed up. Logging here is
         * visible via the log streamer; app_task will catch up. */
        ESP_LOGW(TAG, "button.talk edge_dropped %s src=%s", press ? "press" : "release", src);
        return;
    }
    ESP_LOGI(TAG, "button.talk %s src=%s", press ? "press" : "release", src);
}

static void button_task(void *unused) {
    (void)unused;
    ESP_LOGI(TAG, "button_task started poll=%dms debounce=%dsamples talk=k2+gpio%d",
             POLL_INTERVAL_MS, DEBOUNCE_SAMPLES, (int)TALK_GPIO);
    for (;;) {
        talk_poll();
        vTaskDelay(pdMS_TO_TICKS(POLL_INTERVAL_MS));
    }
}

void hux_button_set_serial_talk(bool held) {
    atomic_store_explicit(&s_serial_talk, held, memory_order_relaxed);
}

void hux_button_start(void) {
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << TALK_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&cfg));
    if (lever_pressed()) {
        /* Held at boot, or the wire is shorted to GND: talk would start on
         * its own. Logged so a stuck lever is visible from the stream. */
        ESP_LOGW(TAG, "talk.gpio%d low at boot: lever held, or its wire shorted to GND",
                 (int)TALK_GPIO);
    }
    BaseType_t ok = xTaskCreate(button_task, "hux_button", TASK_STACK_BYTES,
                                NULL, TASK_PRIO, NULL);
    configASSERT(ok == pdPASS);
}

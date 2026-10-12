#include "hux_ota.h"

#include <stdatomic.h>
#include <string.h>

#include "esp_app_desc.h"
#include "esp_http_client.h"
#include "esp_https_ota.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "hux_ota";

#define OTA_TASK_STACK 8192
#define OTA_TASK_PRIO  3  /* below audio and the app */

static _Atomic bool s_busy = false;
static char s_url[256];
static bool s_force = false;

static const char *state_name(esp_ota_img_states_t s) {
    switch (s) {
        case ESP_OTA_IMG_NEW:            return "new";
        case ESP_OTA_IMG_PENDING_VERIFY: return "pending";
        case ESP_OTA_IMG_VALID:          return "valid";
        case ESP_OTA_IMG_INVALID:        return "invalid";
        case ESP_OTA_IMG_ABORTED:        return "aborted";
        default:                         return "undefined";
    }
}

const char *hux_ota_running_slot(void) {
    const esp_partition_t *run = esp_ota_get_running_partition();
    return run != NULL ? run->label : "?";
}

const char *hux_ota_running_state(void) {
    esp_ota_img_states_t st;
    if (esp_ota_get_state_partition(esp_ota_get_running_partition(), &st) != ESP_OK) {
        return "undefined"; /* flashed over USB: no otadata entry, nothing to verify */
    }
    return state_name(st);
}

/* advanced_https_ota's check, plus the project name: an image for another
 * board (Fleet's, say) must never land in the walkie. */
static esp_err_t validate(const esp_app_desc_t *incoming) {
    const esp_app_desc_t *running = esp_app_get_description();
    if (strncmp(incoming->project_name, running->project_name, sizeof(incoming->project_name)) != 0) {
        ESP_LOGE(TAG, "ota.refused project=%s (this is %s)", incoming->project_name,
                 running->project_name);
        return ESP_FAIL;
    }
    if (!s_force && strncmp(incoming->version, running->version, sizeof(incoming->version)) == 0) {
        ESP_LOGW(TAG, "ota.refused version=%s is already running (ota <url> force)",
                 incoming->version);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "ota.image version=%s (running %s)", incoming->version, running->version);
    return ESP_OK;
}

static void ota_task(void *unused) {
    (void)unused;
    int64_t t0 = esp_timer_get_time();
    ESP_LOGW(TAG, "ota.start url=%s from_slot=%s", s_url, hux_ota_running_slot());
    esp_http_client_config_t http = {
        .url = s_url,
        .timeout_ms = 10000,
        .keep_alive_enable = true,
    };
    esp_https_ota_config_t cfg = {.http_config = &http};
    esp_https_ota_handle_t h = NULL;
    esp_err_t err = esp_https_ota_begin(&cfg, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ota.failed step=begin err=%s", esp_err_to_name(err));
        goto done;
    }
    esp_app_desc_t desc;
    err = esp_https_ota_get_img_desc(h, &desc);
    if (err != ESP_OK || validate(&desc) != ESP_OK) {
        esp_https_ota_abort(h);
        goto done;
    }
    size_t last_kb = 0;
    while ((err = esp_https_ota_perform(h)) == ESP_ERR_HTTPS_OTA_IN_PROGRESS) {
        size_t kb = (size_t)esp_https_ota_get_image_len_read(h) / 1024;
        if (kb >= last_kb + 256) {
            ESP_LOGI(TAG, "ota.progress kb=%u", (unsigned)kb);
            last_kb = kb;
        }
    }
    if (err != ESP_OK || !esp_https_ota_is_complete_data_received(h)) {
        ESP_LOGE(TAG, "ota.failed step=download err=%s", esp_err_to_name(err));
        esp_https_ota_abort(h);
        goto done;
    }
    err = esp_https_ota_finish(h); /* checks the image, sets it to boot next */
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ota.failed step=finish err=%s", esp_err_to_name(err));
        goto done;
    }
    ESP_LOGW(TAG, "ota.done version=%s ms=%u: restarting into it", desc.version,
             (unsigned)((esp_timer_get_time() - t0) / 1000));
    vTaskDelay(pdMS_TO_TICKS(300)); /* let the log line reach the server */
    esp_restart();

done:
    atomic_store(&s_busy, false);
    vTaskDelete(NULL);
}

bool hux_ota_start(const char *url, bool force) {
    if (url == NULL || url[0] == '\0' || atomic_exchange(&s_busy, true)) {
        ESP_LOGW(TAG, "ota.not_started (%s)", url == NULL || url[0] == '\0' ? "no url" : "busy");
        return false;
    }
    strncpy(s_url, url, sizeof(s_url) - 1);
    s_url[sizeof(s_url) - 1] = '\0';
    s_force = force;
    if (xTaskCreate(ota_task, "hux_ota", OTA_TASK_STACK, NULL, OTA_TASK_PRIO, NULL) != pdPASS) {
        atomic_store(&s_busy, false);
        ESP_LOGE(TAG, "ota.not_started (no memory for the task)");
        return false;
    }
    return true;
}

static void selftest_done(void *arg) {
    (void)arg;
    /* Ran HUX_OTA_SELFTEST_S without crashing: keep this image. */
    esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
    ESP_LOGW(TAG, "ota.confirmed slot=%s err=%s", hux_ota_running_slot(), esp_err_to_name(err));
}

void hux_ota_boot_check(void) {
    const esp_app_desc_t *app = esp_app_get_description();
    const char *state = hux_ota_running_state();
    ESP_LOGI(TAG, "ota.boot slot=%s state=%s version=%s", hux_ota_running_slot(), state,
             app->version);
    if (strcmp(state, "pending") == 0) {
        esp_timer_handle_t t;
        const esp_timer_create_args_t args = {.callback = selftest_done, .name = "ota_selftest"};
        if (esp_timer_create(&args, &t) == ESP_OK) {
            esp_timer_start_once(t, (uint64_t)HUX_OTA_SELFTEST_S * 1000000);
            ESP_LOGW(TAG, "ota.pending: confirming in %d s unless it crashes first",
                     HUX_OTA_SELFTEST_S);
        }
    }
}

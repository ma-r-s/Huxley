#include "hux_health.h"

#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "hux_audio.h"
#include "hux_battery.h"
#include "hux_net.h"
#include "hux_ota.h"

static const char *TAG = "hux_health";

#define HEALTH_PERIOD_MS 60000

/* What hasn't reached the server yet. */
static struct {
    uint32_t underruns;
    uint32_t underrun_ms;
    uint32_t discarded_ms;
    uint32_t connects;
    hux_net_count_t wifi[8];
    hux_net_count_t tls[8];
} s_acc;

static void merge(hux_net_count_t *into, const hux_net_count_t *from, size_t n) {
    for (size_t i = 0; i < n; i++) {
        if (from[i].count == 0) {
            continue;
        }
        for (size_t j = 0; j < n; j++) {
            if (into[j].count == 0 || into[j].code == from[i].code) {
                into[j].code = from[i].code;
                into[j].count += from[i].count;
                break;
            }
        }
    }
}

static cJSON *counts(const hux_net_count_t *c, size_t n, bool hex) {
    cJSON *o = cJSON_CreateObject();
    char key[16];
    for (size_t i = 0; i < n; i++) {
        if (c[i].count > 0) {
            snprintf(key, sizeof(key), hex ? "0x%x" : "%d", c[i].code);
            cJSON_AddNumberToObject(o, key, c[i].count);
        }
    }
    return o;
}

static char *build(const hux_net_health_t *net) {
    cJSON *root = cJSON_CreateObject();
    cJSON *d = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "type", "client_event");
    cJSON_AddStringToObject(root, "event", "device.health");
    cJSON_AddNumberToObject(d, "uptime_s", (double)(esp_timer_get_time() / 1000000));
    cJSON_AddNumberToObject(d, "heap_internal_min",
                            heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL));
    cJSON_AddNumberToObject(d, "heap_internal_largest",
                            heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
    cJSON_AddNumberToObject(d, "psram_min", heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM));
    if (net->have_ap) {
        char bssid[18];
        snprintf(bssid, sizeof(bssid), "%02x:%02x:%02x:%02x:%02x:%02x", net->bssid[0],
                 net->bssid[1], net->bssid[2], net->bssid[3], net->bssid[4], net->bssid[5]);
        cJSON_AddNumberToObject(d, "rssi", net->rssi);
        cJSON_AddNumberToObject(d, "channel", net->channel);
        cJSON_AddStringToObject(d, "bssid", bssid);
    }
    cJSON_AddItemToObject(d, "wifi_disconnects", counts(s_acc.wifi, 8, false));
    cJSON_AddItemToObject(d, "tls_errors", counts(s_acc.tls, 8, true));
    cJSON_AddNumberToObject(d, "reconnects", s_acc.connects);
    cJSON_AddNumberToObject(d, "underruns", s_acc.underruns);
    cJSON_AddNumberToObject(d, "underrun_ms", s_acc.underrun_ms);
    cJSON_AddNumberToObject(d, "discarded_ms", s_acc.discarded_ms);
    cJSON_AddNumberToObject(d, "ws_rx_age_ms", net->ws_rx_age_ms);
    cJSON_AddNumberToObject(d, "ring_used_ms", (double)(hux_audio_spk_ring_used() / 48));
    cJSON_AddNumberToObject(d, "volume", hux_audio_get_volume());
    cJSON_AddNumberToObject(d, "quiet_left_s", hux_audio_quiet_left_s());
    cJSON_AddStringToObject(d, "ota_state", hux_ota_running_state());
    int mv = 0, pct = 0;
    if (hux_battery_last(&mv, &pct)) {
        cJSON *b = cJSON_CreateObject();
        cJSON_AddNumberToObject(b, "mv", mv);
        cJSON_AddNumberToObject(b, "pct", pct);
        cJSON_AddItemToObject(d, "battery", b); /* `charging` once the 5V divider is wired */
    }
    cJSON_AddItemToObject(root, "data", d);
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return json;
}

static void health_task(void *arg) {
    (void)arg;
    uint32_t unsent = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(HEALTH_PERIOD_MS));
        uint32_t u, ums, dms;
        hux_audio_take_health(&u, &ums, &dms);
        s_acc.underruns += u;
        s_acc.underrun_ms += ums;
        s_acc.discarded_ms += dms;
        hux_net_health_t net;
        hux_net_take_health(&net);
        s_acc.connects += net.connects;
        merge(s_acc.wifi, net.wifi_disconnects, 8);
        merge(s_acc.tls, net.tls_errors, 8);
        char *json = build(&net);
        bool sent = json != NULL && hux_net_send_text(json, strlen(json));
        if (sent) {
            memset(&s_acc, 0, sizeof(s_acc));
            if (unsent > 0) {
                ESP_LOGI(TAG, "health.sent after_unsent=%u", (unsigned)unsent);
            }
            unsent = 0;
        } else if (unsent++ == 0) {
            ESP_LOGI(TAG, "health.unsent: offline, counts fold into the next line");
        }
        cJSON_free(json);
    }
}

void hux_health_start(void) {
    if (xTaskCreate(health_task, "hux_health", 4096, NULL, 2, NULL) != pdPASS) {
        ESP_LOGE(TAG, "health.task_create_failed");
    }
}

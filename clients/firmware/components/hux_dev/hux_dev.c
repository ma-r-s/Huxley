#include "hux_dev.h"

#include <stdio.h>
#include <string.h>

#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "esp_timer.h"

#include "hux_audio.h"
#include "hux_console.h"
#include "hux_net.h"
#include "hux_ota.h"

static const char *TAG = "hux_dev";

#define CMD_MAX_BYTES 200

static const char *s_token = NULL; /* from main/secrets.h, via main.c */

/* Same length and every byte compared: no early exit to time. */
static bool authorized(httpd_req_t *req) {
    char got[80] = {0};
    bool ok = false;
    if (httpd_req_get_hdr_value_str(req, "X-Huxley-Token", got, sizeof(got)) == ESP_OK) {
        const char *want = s_token;
        size_t n = strlen(want);
        unsigned diff = strlen(got) != n;
        for (size_t i = 0; i < n && i < sizeof(got); i++) {
            diff |= (unsigned)(got[i] ^ want[i]);
        }
        ok = diff == 0;
    }
    if (!ok) {
        ESP_LOGW(TAG, "dev.denied uri=%s", req->uri);
        httpd_resp_set_status(req, "401 Unauthorized");
        httpd_resp_sendstr(req, "token required\n");
    }
    return ok;
}

static esp_err_t info_get(httpd_req_t *req) {
    if (!authorized(req)) {
        return ESP_OK;
    }
    char buf[512];
    int n = snprintf(buf, sizeof(buf),
                     "{\"version\":\"%s\",\"uptime_s\":%u,\"reset_reason\":%d,"
                     "\"heap_internal\":%u,\"heap_internal_min\":%u,\"psram\":%u,"
                     "\"ring_used\":%u,\"volume\":%d,\"rssi\":%d,\"ws_rx_age_ms\":%u,"
                     "\"slot\":\"%s\",\"slot_state\":\"%s\"}\n",
                     esp_app_get_description()->version,
                     (unsigned)(esp_timer_get_time() / 1000000), (int)esp_reset_reason(),
                     (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                     (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
                     (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
                     (unsigned)hux_audio_spk_ring_used(), hux_audio_get_volume(), hux_net_rssi(),
                     (unsigned)hux_net_rx_age_ms(), hux_ota_running_slot(),
                     hux_ota_running_state());
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, buf, n);
}

static esp_err_t cmd_post(httpd_req_t *req) {
    if (!authorized(req)) {
        return ESP_OK;
    }
    char line[CMD_MAX_BYTES + 1] = {0};
    int len = req->content_len < CMD_MAX_BYTES ? (int)req->content_len : CMD_MAX_BYTES;
    int got = 0;
    while (got < len) {
        int r = httpd_req_recv(req, line + got, len - got);
        if (r <= 0) {
            httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "no body");
            return ESP_OK;
        }
        got += r;
    }
    line[got] = '\0';
    for (char *p = line; *p; p++) {
        if (*p == '\r' || *p == '\n') {
            *p = '\0';
            break;
        }
    }
    ESP_LOGI(TAG, "dev.cmd %s", line);
    /* Reply first: `reboot` and `ota` end this boot, `say` takes seconds. */
    httpd_resp_sendstr(req, "ok\n");
    hux_console_run(line);
    return ESP_OK;
}

static esp_err_t coredump_get(httpd_req_t *req) {
    if (!authorized(req)) {
        return ESP_OK;
    }
    const esp_partition_t *p = esp_partition_find_first(ESP_PARTITION_TYPE_DATA,
                                                        ESP_PARTITION_SUBTYPE_DATA_COREDUMP, NULL);
    if (p == NULL) {
        httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "no coredump partition");
        return ESP_OK;
    }
    httpd_resp_set_type(req, "application/octet-stream");
    static char chunk[4096];
    for (size_t off = 0; off < p->size; off += sizeof(chunk)) {
        if (esp_partition_read(p, off, chunk, sizeof(chunk)) != ESP_OK ||
            httpd_resp_send_chunk(req, chunk, sizeof(chunk)) != ESP_OK) {
            break;
        }
    }
    ESP_LOGI(TAG, "dev.coredump sent bytes=%u", (unsigned)p->size);
    return httpd_resp_send_chunk(req, NULL, 0);
}

void hux_dev_start(const char *token) {
    if (token == NULL || token[0] == '\0') {
        ESP_LOGW(TAG, "dev.off: no HUX_DEV_TOKEN in secrets.h, no developer access over Wi-Fi");
        return;
    }
    s_token = token;
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.stack_size = 8192;       /* console commands run on this task */
    cfg.task_priority = 3;       /* below audio and the app */
    cfg.lru_purge_enable = true;
    httpd_handle_t srv = NULL;
    esp_err_t err = httpd_start(&srv, &cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "dev.start_failed err=%s", esp_err_to_name(err));
        return;
    }
    const httpd_uri_t uris[] = {
        {.uri = "/info", .method = HTTP_GET, .handler = info_get},
        {.uri = "/cmd", .method = HTTP_POST, .handler = cmd_post},
        {.uri = "/coredump", .method = HTTP_GET, .handler = coredump_get},
    };
    for (size_t i = 0; i < sizeof(uris) / sizeof(uris[0]); i++) {
        httpd_register_uri_handler(srv, &uris[i]);
    }
    ESP_LOGI(TAG, "dev.start port=%d (GET /info, POST /cmd, GET /coredump; token required)",
             cfg.server_port);
}

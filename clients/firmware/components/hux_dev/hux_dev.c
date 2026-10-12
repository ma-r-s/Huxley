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
    char sha[10];
    esp_app_get_elf_sha256(sha, sizeof(sha)); /* 9 hex chars, as in a core dump */
    char buf[600];
    int n = snprintf(buf, sizeof(buf),
                     "{\"version\":\"%s\",\"elf_sha\":\"%s\",\"uptime_s\":%u,\"reset_reason\":%d,"
                     "\"heap_internal\":%u,\"heap_internal_min\":%u,\"psram\":%u,"
                     "\"ring_used\":%u,\"volume\":%d,\"rssi\":%d,\"ws_rx_age_ms\":%u,"
                     "\"slot\":\"%s\",\"slot_state\":\"%s\",\"last_invalid\":\"%s\"}\n",
                     esp_app_get_description()->version, sha,
                     (unsigned)(esp_timer_get_time() / 1000000), (int)esp_reset_reason(),
                     (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                     (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
                     (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
                     (unsigned)hux_audio_spk_ring_used(), hux_audio_get_volume(), hux_net_rssi(),
                     (unsigned)hux_net_rx_age_ms(), hux_ota_running_slot(),
                     hux_ota_running_state(), hux_ota_last_invalid());
    if (n < 0 || n >= (int)sizeof(buf)) {
        n = (int)strlen(buf); /* truncated: still never past the buffer */
    }
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, buf, n);
}

static esp_err_t cmd_post(httpd_req_t *req) {
    if (!authorized(req)) {
        return ESP_OK;
    }
    char line[CMD_MAX_BYTES + 1] = {0};
    if (req->content_len > CMD_MAX_BYTES) {
        /* Never a cut line (F-0027): refused whole. */
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "line too long");
        return ESP_OK;
    }
    int len = (int)req->content_len;
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
    ESP_LOGI(TAG, "dev.cmd %s", hux_console_is_secret(line) ? "<redacted>" : line);
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

/* The bench clips (tools/clips.py) into the storage partition, over
 * Wi-Fi: no USB to change what `say <n>` says. No restart; the next
 * `say` maps the new clips. */
static esp_err_t clips_post(httpd_req_t *req) {
    if (!authorized(req)) {
        return ESP_OK;
    }
    const esp_partition_t *p =
        esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "storage");
    size_t len = req->content_len;
    if (p == NULL || len < 8 || len > p->size) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "no storage partition, or bad size");
        return ESP_OK;
    }
    if (!hux_audio_clips_release()) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "a clip is playing");
        return ESP_OK;
    }
    size_t erase = (len + 4095) & ~(size_t)4095;
    esp_err_t err = esp_partition_erase_range(p, 0, erase);
    static char chunk[4096];
    size_t done = 0;
    while (err == ESP_OK && done < len) {
        int r = httpd_req_recv(req, chunk, len - done < sizeof(chunk) ? len - done : sizeof(chunk));
        if (r <= 0) {
            err = ESP_FAIL;
            break;
        }
        err = esp_partition_write(p, done, chunk, (size_t)r);
        done += (size_t)r;
    }
    ESP_LOG_LEVEL(err == ESP_OK ? ESP_LOG_INFO : ESP_LOG_ERROR, TAG,
                  "dev.clips bytes=%u of %u err=%s", (unsigned)done, (unsigned)len,
                  esp_err_to_name(err));
    if (err != ESP_OK) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "write failed");
        return ESP_OK;
    }
    return httpd_resp_sendstr(req, "ok\n");
}

void hux_dev_start(const char *token) {
    if (token == NULL || strlen(token) < 16 || strcmp(token, "change-me") == 0) {
        /* The repo is public: the example token must never open a board. */
        ESP_LOGE(TAG, "dev.off: HUX_DEV_TOKEN in secrets.h missing or weak (16+ random chars); "
                      "no developer access over Wi-Fi");
        return;
    }
    s_token = token;
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.stack_size = 8192;       /* console commands run on this task */
    cfg.task_priority = 3;       /* below audio and the app */
    cfg.lru_purge_enable = true;
    /* 3, not 7: the WebSocket and an update's download need sockets too
     * (CONFIG_LWIP_MAX_SOCKETS is 10; idle keep-alives are purged LRU). */
    cfg.max_open_sockets = 3;
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
        {.uri = "/clips", .method = HTTP_POST, .handler = clips_post},
    };
    for (size_t i = 0; i < sizeof(uris) / sizeof(uris[0]); i++) {
        httpd_register_uri_handler(srv, &uris[i]);
    }
    ESP_LOGI(TAG, "dev.start port=%d (GET /info, POST /cmd, GET /coredump, POST /clips; token required)",
             cfg.server_port);
}

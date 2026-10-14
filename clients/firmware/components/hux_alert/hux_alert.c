#include "hux_alert.h"

#include <stdio.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>

#include "esp_attr.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"
#include "hux_alert_logic.h"
#include "hux_net.h"

static const char *TAG = "hux_alert";

#define TICK_MS     30000
#define TASK_STACK  8192 /* a TLS handshake runs on it */
#define TOPIC_MAX   64
#define CLICK_MAX   96

/* RTC memory: the ladder's hourly reboot keeps it (a power cut doesn't).
 * The magic carries the layout's size. */
typedef struct {
    uint32_t magic;
    hux_alert_state_t s;
} alert_rtc_t;
#define ALERT_MAGIC (0x414c5400u ^ (uint32_t)sizeof(alert_rtc_t)) /* "ALT" */
RTC_NOINIT_ATTR static alert_rtc_t s_rtc;

/* NVS too (namespace hux_alert): a power cut after a "needs you" must still
 * end in a "fixed". Written only on a transition. */
#define NVS_NS  "hux_alert"
#define NVS_KEY "state"
typedef struct {
    uint32_t down_sent;
    int64_t down_since;
} alert_nvs_t;

static void nvs_save(void) {
    alert_nvs_t v = {.down_sent = s_rtc.s.down_sent, .down_since = s_rtc.s.down_since};
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_blob(h, NVS_KEY, &v, sizeof(v));
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "alert.nvs_save_failed err=%s", esp_err_to_name(err));
    }
}

static void nvs_load(void) {
    alert_nvs_t v;
    size_t len = sizeof(v);
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK) {
        return;
    }
    if (nvs_get_blob(h, NVS_KEY, &v, &len) == ESP_OK && len == sizeof(v)) {
        s_rtc.s.down_sent = v.down_sent != 0;
        s_rtc.s.down_since = v.down_since;
    }
    nvs_close(h);
}

static char s_topic[TOPIC_MAX];
static char s_click[CLICK_MAX];
static TaskHandle_t s_task = NULL;
static volatile bool s_test_pending = false; /* `alert test`, run on this task's stack */

static uint32_t uptime_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}

/* One POST to ntfy.sh. Returns the HTTP status, or -1 with *err set. */
static int post(const char *title, const char *priority, const char *tags, const char *body,
                esp_err_t *err_out) {
    char url[24 + TOPIC_MAX];
    snprintf(url, sizeof(url), "https://ntfy.sh/%s", s_topic);
    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 15000,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t c = esp_http_client_init(&cfg);
    *err_out = ESP_ERR_NO_MEM;
    if (c == NULL) {
        return -1;
    }
    esp_http_client_set_header(c, "Title", title);
    esp_http_client_set_header(c, "Priority", priority);
    esp_http_client_set_header(c, "Tags", tags);
    if (s_click[0] != '\0') {
        esp_http_client_set_header(c, "Click", s_click);
    }
    esp_http_client_set_post_field(c, body, (int)strlen(body));
    esp_err_t err = esp_http_client_perform(c);
    *err_out = err;
    int status = err == ESP_OK ? esp_http_client_get_status_code(c) : -1;
    esp_http_client_cleanup(c);
    memset(url, 0, sizeof(url)); /* the topic doesn't linger */
    return status;
}

static void log_sent(const char *kind, int status, esp_err_t err) {
    ESP_LOGW(TAG,
             "alert.sent kind=%s status=%d err=%s heap_internal_min=%u largest=%u stack_free=%u",
             kind, status, esp_err_to_name(err),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
             (unsigned)uxTaskGetStackHighWaterMark(NULL));
}

static bool push(hux_alert_action_t what, int64_t now_s) {
    char body[200], since[6], at[6];
    hux_alert_hhmm(s_rtc.s.down_since, since);
    esp_err_t err = ESP_OK;
    int status;
    if (what == HUX_ALERT_SEND_DOWN) {
        snprintf(body, sizeof(body),
                 "The walkie can't reach Huxley's server since %s (Bogota): the Pi, its power, "
                 "the house internet or the tunnel is down.",
                 since);
        status = post("Walkie: needs you", "high", "warning", body, &err);
    } else {
        hux_alert_hhmm(s_rtc.s.fixed_at, at);
        snprintf(body, sizeof(body),
                 "The walkie reached Huxley's server again at %s (Bogota), down since %s.", at,
                 since);
        /* At night a silent push: no stale alarm till morning, no ring. */
        status = post("Walkie: fixed", hux_alert_quiet(now_s) ? "low" : "default",
                      "white_check_mark", body, &err);
    }
    log_sent(what == HUX_ALERT_SEND_DOWN ? "down" : "fixed", status, err);
    return status >= 200 && status < 300;
}

/* Why a due "needs you" waits, for one line per change (held alerts
 * explain themselves in the log). */
static const char *held_why(int64_t now_s, uint32_t lost_ms, uint32_t uptime_ms) {
    if (s_rtc.s.down_sent || lost_ms < HUX_ALERT_DOWN_AFTER_MS) {
        return NULL;
    }
    if (!hux_net_have_ip()) {
        return "no_ip";
    }
    if (hux_alert_quiet(now_s)) {
        return "quiet_hours";
    }
    if (s_rtc.s.retry_ms != 0 && uptime_ms - s_rtc.s.failed_at_ms < s_rtc.s.retry_ms) {
        return "retry_wait";
    }
    return NULL;
}

static void alert_task(void *arg) {
    (void)arg;
    const char *held = NULL;
    bool noticed = false;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(TICK_MS));
        if (s_test_pending) {
            s_test_pending = false;
            esp_err_t err = ESP_OK;
            int status = post("Walkie: test", "low", "test_tube",
                              "A test from the walkie: its own alerts reach this phone.", &err);
            log_sent("test", status, err);
            continue;
        }
        struct timeval tv;
        gettimeofday(&tv, NULL);
        uint32_t lost = hux_net_server_lost_ms();
        uint32_t up = uptime_ms();
        hux_alert_action_t what = hux_alert_step(&s_rtc.s, (int64_t)tv.tv_sec, up,
                                                 hux_net_have_ip(), lost == 0, lost);
        if (!noticed && s_rtc.s.down_since != 0 && !s_rtc.s.down_sent &&
            lost >= 3u * 60u * 1000u) { /* not the first 2 min of a new connection */
            char since[6];
            hux_alert_hhmm(s_rtc.s.down_since, since);
            ESP_LOGW(TAG, "alert.server_lost since=%s lost_s=%u", since, (unsigned)(lost / 1000));
        }
        noticed = s_rtc.s.down_since != 0 && (noticed || lost >= 3u * 60u * 1000u);
        const char *why = held_why((int64_t)tv.tv_sec, lost, up);
        if (why != held) {
            if (why != NULL) {
                ESP_LOGW(TAG, "alert.held why=%s lost_s=%u", why, (unsigned)(lost / 1000));
            }
            held = why;
        }
        if (what != HUX_ALERT_NONE) {
            bool ok = push(what, (int64_t)tv.tv_sec);
            hux_alert_sent(&s_rtc.s, what, ok, uptime_ms());
            if (ok) {
                nvs_save();
            }
        }
    }
}

void hux_alert_start(const char *topic, const char *click) {
    if (s_rtc.magic != ALERT_MAGIC) {
        memset(&s_rtc, 0, sizeof(s_rtc)); /* a power cut, or another layout */
        s_rtc.magic = ALERT_MAGIC;
        nvs_load(); /* a "needs you" sent before the power cut still wants its "fixed" */
    }
    s_rtc.s.retry_ms = 0; /* a fresh boot tries at once */
    size_t len = topic != NULL ? strlen(topic) : 0;
    bool valid = len > 0 && len < TOPIC_MAX;
    for (size_t i = 0; valid && i < len; i++) {
        char ch = topic[i];
        valid = (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') ||
                ch == '_' || ch == '-';
    }
    if (!valid) {
        /* Never the topic itself: a URL that fails to parse would be logged whole. */
        ESP_LOGW(TAG, "alert.disabled why=%s", len == 0 ? "no_topic" : "topic_invalid");
        return;
    }
    strlcpy(s_topic, topic, sizeof(s_topic));
    strlcpy(s_click, click != NULL ? click : "", sizeof(s_click));
    /* Wall-clock time for quiet hours and "since HH:MM"; it syncs once
     * there's an IP (and the logs gain nothing from it: they keep uptime). */
    esp_sntp_config_t sc = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    esp_err_t err = esp_netif_sntp_init(&sc);
    if (xTaskCreate(alert_task, "hux_alert", TASK_STACK, NULL, 2, &s_task) != pdPASS) {
        ESP_LOGE(TAG, "alert.task_create_failed");
        return;
    }
    ESP_LOGI(TAG, "alert.started sntp=%s down_sent=%u", esp_err_to_name(err),
             (unsigned)s_rtc.s.down_sent);
}

void hux_alert_test(void) {
    if (s_task == NULL) {
        ESP_LOGW(TAG, "alert.test refused why=%s", s_topic[0] == '\0' ? "no_topic" : "no_task");
        return;
    }
    s_test_pending = true; /* the TLS handshake needs the alert task's stack */
    xTaskNotifyGive(s_task);
    ESP_LOGI(TAG, "alert.test queued");
}

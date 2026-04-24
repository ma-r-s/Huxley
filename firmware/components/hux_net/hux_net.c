#include "hux_net.h"

#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"

#include "hux_app.h"
#include "hux_log.h"

static const char *TAG = "hux_net";

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1
#define WIFI_RETRY_MAX     5
/* Network ring buffer sized for the largest single WS text frame we
 * reasonably expect in v0 (hello / state / status / audio deltas are
 * all under 4 KB). Revisit when inbound audio chunks grow. */
#define WS_BUFFER_SIZE     8192
#define WS_RECONNECT_MS    2000
/* Bounded send timeout — if TCP stalls, the 50 Hz audio sender task
 * must fail fast, not wedge. Longer than any reasonable flush on a
 * healthy LAN (~1 ms); short enough that a stall becomes a warning
 * we can act on before the watchdog does. */
#define WS_SEND_TIMEOUT_MS 200

static EventGroupHandle_t s_wifi_events = NULL;
static esp_websocket_client_handle_t s_ws = NULL;
static int s_wifi_retries = 0;
static char s_server_uri[128] = {0};

/* Audio data-plane seam — see firmware/docs/architecture.md §"Data
 * plane vs control plane". Inbound `audio` messages bypass
 * hux_app's queue entirely; at 50 Hz the control-plane queue cannot
 * keep up with per-frame JSON parses + heap copies. Sink pointer
 * reads cross CPU cores (set from app_main, called on the WS client
 * task) so it's atomic. */
static _Atomic(hux_net_audio_sink_fn) s_audio_sink = NULL;
/* Decode scratch — single-writer (the WS client task), so no lock is
 * needed. Big enough to hold the PCM of any single WS text frame
 * the server sends today (WS_BUFFER_SIZE base64 bytes decode to at
 * most 3/4 as much PCM). */
#define AUDIO_SCRATCH_BYTES ((WS_BUFFER_SIZE * 3) / 4 + 32)
static uint8_t s_audio_scratch[AUDIO_SCRATCH_BYTES];

static void post_app(hux_app_event_kind_t kind) {
    hux_app_event_t ev = {.kind = kind};
    hux_app_post_event(&ev, false);
}

/* ------------------------------------------------------------------ */
/*  Wi-Fi                                                             */
/* ------------------------------------------------------------------ */

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_wifi_retries < WIFI_RETRY_MAX) {
            s_wifi_retries++;
            ESP_LOGW(TAG, "wifi.disconnected retry=%d/%d",
                     s_wifi_retries, WIFI_RETRY_MAX);
            esp_wifi_connect();
        } else {
            ESP_LOGE(TAG, "wifi.retries_exhausted");
            xEventGroupSetBits(s_wifi_events, WIFI_FAIL_BIT);
        }
        post_app(HUX_APP_EV_NET_WIFI_DOWN);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "wifi.got_ip ip=" IPSTR, IP2STR(&event->ip_info.ip));
        s_wifi_retries = 0;
        xEventGroupSetBits(s_wifi_events, WIFI_CONNECTED_BIT);
        post_app(HUX_APP_EV_NET_WIFI_UP);
    }
}

static void wifi_init_sta(const char *ssid, const char *password) {
    s_wifi_events = xEventGroupCreate();
    configASSERT(s_wifi_events != NULL);

    ESP_ERROR_CHECK(esp_netif_init());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));

    wifi_config_t wifi_config = {0};
    strncpy((char *)wifi_config.sta.ssid, ssid,
            sizeof(wifi_config.sta.ssid) - 1);
    strncpy((char *)wifi_config.sta.password, password,
            sizeof(wifi_config.sta.password) - 1);
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_config.sta.pmf_cfg.capable = true;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "wifi.init ssid=\"%s\"", ssid);
}

/* ------------------------------------------------------------------ */
/*  WebSocket                                                          */
/* ------------------------------------------------------------------ */

/* Cheap probe: is this message an `audio` type? Avoids parsing the
 * whole JSON on the hot path. Relies on the server always serialising
 * `"type"` as the first key; if that ever changes, the check falls
 * through and the message takes the control-plane path. Correct but
 * slow — a belt-and-braces fallback. */
static bool looks_like_audio(const char *json, size_t len) {
    if (len < 16) {
        return false;
    }
    /* Bound the scan — the `"type":"audio"` pattern, if present,
     * lives in the first ~40 bytes. */
    size_t probe = len < 64 ? len : 64;
    const char *p = json;
    for (size_t i = 0; i + 14 <= probe; i++) {
        if (memcmp(p + i, "\"type\":\"audio\"", 14) == 0) {
            return true;
        }
    }
    return false;
}

/* Full audio path. Parses the envelope, extracts `data`, decodes
 * base64 into `s_audio_scratch`, hands the view to the registered
 * sink. Runs on the WS client task; the sink must not block. */
static void dispatch_audio(const char *json, size_t len) {
    hux_net_audio_sink_fn sink =
        atomic_load_explicit(&s_audio_sink, memory_order_acquire);
    if (sink == NULL) {
        return; /* No consumer yet — drop silently. Expected in v0.1.x. */
    }

    cJSON *root = cJSON_ParseWithLength(json, len);
    if (root == NULL) {
        ESP_LOGW(TAG, "ws.rx.audio.parse_failed len=%u", (unsigned)len);
        return;
    }
    const cJSON *b64 = cJSON_GetObjectItemCaseSensitive(root, "data");
    if (!cJSON_IsString(b64) || b64->valuestring == NULL) {
        cJSON_Delete(root);
        ESP_LOGW(TAG, "ws.rx.audio.no_data");
        return;
    }

    size_t pcm_len = 0;
    int rc = mbedtls_base64_decode(s_audio_scratch, sizeof(s_audio_scratch),
                                   &pcm_len, (const unsigned char *)b64->valuestring,
                                   strlen(b64->valuestring));
    cJSON_Delete(root);
    if (rc != 0) {
        ESP_LOGW(TAG, "ws.rx.audio.b64_decode_failed rc=-0x%04x", -rc);
        return;
    }
    sink(s_audio_scratch, pcm_len);
}

/* Handles a WS DATA event. Audio messages take the zero-copy data-
 * plane path straight to the registered sink; everything else is
 * copied onto the heap and posted to hux_app's control queue. */
static void forward_ws_text(const esp_websocket_event_data_t *data) {
    if (data->op_code != 0x01 /* TEXT */) {
        /* PING (0x9) / PONG (0xA) / CLOSE (0x8) are WS control frames
         * handled by esp_websocket_client internally — silent skip.
         * Binary (0x2) would be real but the protocol is JSON-only, so
         * log it loud if it ever shows up. */
        if (data->op_code == 0x02) {
            ESP_LOGW(TAG, "ws.rx.binary len=%d (unexpected)", data->data_len);
        }
        return;
    }
    if (data->payload_offset != 0 || data->data_len != data->payload_len) {
        ESP_LOGW(TAG, "ws.rx.fragmented off=%d len=%d/%d — dropped",
                 data->payload_offset, data->data_len, data->payload_len);
        return;
    }

    if (looks_like_audio(data->data_ptr, (size_t)data->data_len)) {
        dispatch_audio(data->data_ptr, (size_t)data->data_len);
        return;
    }

    char *copy = malloc(data->data_len + 1);
    if (copy == NULL) {
        ESP_LOGE(TAG, "ws.rx.oom len=%d", data->data_len);
        return;
    }
    memcpy(copy, data->data_ptr, data->data_len);
    copy[data->data_len] = '\0';

    hux_app_event_t ev = {
        .kind = HUX_APP_EV_NET_WS_MESSAGE,
        .payload.ws_message = {.data = copy, .len = (size_t)data->data_len},
    };
    if (!hux_app_post_event(&ev, false)) {
        /* Post failed; own the memory we just allocated. */
        free(copy);
    }
}

static void ws_event_handler(void *arg, esp_event_base_t base,
                             int32_t id, void *data) {
    esp_websocket_event_data_t *ev = (esp_websocket_event_data_t *)data;
    switch (id) {
        case WEBSOCKET_EVENT_CONNECTED:
            ESP_LOGI(TAG, "ws.connected uri=%s", s_server_uri);
            post_app(HUX_APP_EV_NET_WS_CONNECTED);
            break;
        case WEBSOCKET_EVENT_DISCONNECTED:
            ESP_LOGW(TAG, "ws.disconnected");
            post_app(HUX_APP_EV_NET_WS_DISCONNECTED);
            break;
        case WEBSOCKET_EVENT_DATA:
            forward_ws_text(ev);
            break;
        case WEBSOCKET_EVENT_ERROR:
            ESP_LOGE(TAG, "ws.error");
            break;
        default:
            break;
    }
}

static void ws_start(const char *uri) {
    strncpy(s_server_uri, uri, sizeof(s_server_uri) - 1);
    esp_websocket_client_config_t cfg = {
        .uri = s_server_uri,
        .buffer_size = WS_BUFFER_SIZE,
        .reconnect_timeout_ms = WS_RECONNECT_MS,
        .network_timeout_ms = 10000,
    };
    s_ws = esp_websocket_client_init(&cfg);
    configASSERT(s_ws != NULL);
    ESP_ERROR_CHECK(esp_websocket_register_events(
        s_ws, WEBSOCKET_EVENT_ANY, ws_event_handler, NULL));
    ESP_ERROR_CHECK(esp_websocket_client_start(s_ws));
    ESP_LOGI(TAG, "ws.init uri=%s", s_server_uri);
}

/* ------------------------------------------------------------------ */
/*  Public API                                                         */
/* ------------------------------------------------------------------ */

void hux_net_start(const hux_net_config_t *cfg) {
    configASSERT(cfg != NULL && cfg->wifi_ssid != NULL &&
                 cfg->wifi_password != NULL && cfg->server_uri != NULL);
    wifi_init_sta(cfg->wifi_ssid, cfg->wifi_password);
    /* WS client auto-connects once Wi-Fi has an IP (esp_websocket_client
     * retries internally until DNS + TCP succeed). Starting it here keeps
     * the lifecycle simple — no separate "wifi is up, start WS" hook. */
    ws_start(cfg->server_uri);
}

bool hux_net_send_text(const char *data, size_t len) {
    if (s_ws == NULL || !esp_websocket_client_is_connected(s_ws)) {
        return false;
    }
    int sent = esp_websocket_client_send_text(s_ws, data, (int)len,
                                              pdMS_TO_TICKS(WS_SEND_TIMEOUT_MS));
    if (sent != (int)len) {
        /* Partial / timed-out send: don't retry here — the caller
         * (mic task, log drain) decides whether to back off or drop.
         * Logging is also restrained to WARN not ERROR so a single
         * flaky Wi-Fi moment doesn't flood the stream. */
        ESP_LOGW(TAG, "ws.send.incomplete requested=%u sent=%d",
                 (unsigned)len, sent);
        return false;
    }
    return true;
}

void hux_net_set_audio_sink(hux_net_audio_sink_fn sink) {
    /* Release-store pairs with the acquire-load in `dispatch_audio`
     * on the WS client task. */
    atomic_store_explicit(&s_audio_sink, sink, memory_order_release);
}

void hux_net_send_log(const hux_log_entry_t *entry) {
    if (entry == NULL || s_ws == NULL ||
        !esp_websocket_client_is_connected(s_ws)) {
        return; /* Best-effort — serial already has the line. */
    }

    /* cJSON handles quoting / escaping of anything weird in the log
     * line (quotes, backslashes, control chars). Hand-rolled JSON
     * formatting here would silently corrupt messages that contain
     * those. The alloc cost is tolerable on the drain task — it runs
     * at low priority and only fires for WARN+ lines. */
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        return;
    }
    cJSON *data = cJSON_CreateObject();
    if (data == NULL) {
        cJSON_Delete(root);
        return;
    }
    const char level_str[2] = {entry->level, '\0'};
    cJSON_AddStringToObject(data, "level", level_str);
    cJSON_AddStringToObject(data, "tag", entry->tag);
    cJSON_AddStringToObject(data, "line", entry->line);
    cJSON_AddNumberToObject(data, "ts", (double)entry->ts_ms);

    cJSON_AddStringToObject(root, "type", "client_event");
    cJSON_AddStringToObject(root, "event", "huxley.firmware_log");
    cJSON_AddItemToObject(root, "data", data);

    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (json == NULL) {
        return;
    }

    (void)hux_net_send_text(json, strlen(json));
    cJSON_free(json);
}

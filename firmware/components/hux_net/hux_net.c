#include "hux_net.h"

#include <stdlib.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

#include "hux_app.h"

static const char *TAG = "hux_net";

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1
#define WIFI_RETRY_MAX     5
/* Network ring buffer sized for the largest single WS text frame we
 * reasonably expect in v0 (hello / state / status / audio deltas are
 * all under 4 KB). Revisit when inbound audio chunks grow. */
#define WS_BUFFER_SIZE     8192
#define WS_RECONNECT_MS    2000

static EventGroupHandle_t s_wifi_events = NULL;
static esp_websocket_client_handle_t s_ws = NULL;
static int s_wifi_retries = 0;
static char s_server_uri[128] = {0};

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

/* Handles a WS DATA event. Single-frame text messages are delivered to
 * the app queue; fragmented or binary frames are warn-logged (not
 * reached today — server sends single-frame text JSON per protocol).
 * When real fragmentation appears we accumulate per-op_code into a
 * scratch buffer; until then the simpler path is correct and smaller. */
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
                                              portMAX_DELAY);
    return sent == (int)len;
}

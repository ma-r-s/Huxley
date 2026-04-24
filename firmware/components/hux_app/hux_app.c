#include "hux_app.h"

#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "hux_proto.h"

static const char *TAG = "hux_app";

/* Queue depth — control events are bursty but small; 32 is comfortable
 * and costs ~1 KB. PCM frames go through a separate dedicated path, not
 * this queue. */
#define EVENT_QUEUE_DEPTH 32
#define APP_TASK_STACK    6144
#define APP_TASK_PRIO     5

typedef enum {
    ST_BOOT = 0,
    ST_WAITING_WIFI,
    ST_WAITING_WS,
    ST_WAITING_HELLO,
    ST_READY,
} app_state_t;

static QueueHandle_t s_event_q = NULL;
static app_state_t s_state = ST_BOOT;

static const char *state_name(app_state_t s) {
    switch (s) {
        case ST_BOOT:          return "BOOT";
        case ST_WAITING_WIFI:  return "WAITING_WIFI";
        case ST_WAITING_WS:    return "WAITING_WS";
        case ST_WAITING_HELLO: return "WAITING_HELLO";
        case ST_READY:         return "READY";
    }
    return "?";
}

static void transition(app_state_t next) {
    if (next == s_state) {
        return;
    }
    ESP_LOGI(TAG, "state %s -> %s", state_name(s_state), state_name(next));
    s_state = next;
}

static void handle_ws_message(hux_app_ws_message_t *msg) {
    hux_msg_t parsed;
    if (!hux_proto_parse(msg->data, msg->len, &parsed)) {
        ESP_LOGW(TAG, "ws.rx.malformed len=%u", (unsigned)msg->len);
        return;
    }

    switch (parsed.kind) {
        case HUX_MSG_HELLO:
            ESP_LOGI(TAG, "ws.rx.hello protocol=%d (expected=%d)",
                     parsed.as.hello.protocol, HUX_PROTOCOL_VERSION);
            if (parsed.as.hello.protocol != HUX_PROTOCOL_VERSION) {
                ESP_LOGE(TAG, "protocol mismatch — server is incompatible");
                /* Stay in WAITING_HELLO; net layer will reconnect and we
                 * will reevaluate. No auto-recovery beyond reconnect
                 * until a capability-handshake negotiation lands. */
                return;
            }
            transition(ST_READY);
            break;

        default:
            /* Kinds we receive but don't act on yet — logging the kind
             * name is enough for now. Each handler lands as a new case. */
            ESP_LOGI(TAG, "ws.rx.%s (unhandled in v0)",
                     hux_proto_kind_name(parsed.kind));
            break;
    }
}

static void dispatch(hux_app_event_t *ev) {
    switch (ev->kind) {
        case HUX_APP_EV_NET_WIFI_UP:
            ESP_LOGI(TAG, "net.wifi.up");
            transition(ST_WAITING_WS);
            break;

        case HUX_APP_EV_NET_WIFI_DOWN:
            ESP_LOGW(TAG, "net.wifi.down");
            transition(ST_WAITING_WIFI);
            break;

        case HUX_APP_EV_NET_WS_CONNECTED:
            ESP_LOGI(TAG, "net.ws.connected");
            transition(ST_WAITING_HELLO);
            break;

        case HUX_APP_EV_NET_WS_DISCONNECTED:
            ESP_LOGW(TAG, "net.ws.disconnected");
            /* Preserve WIFI state — if wifi is still up, we go to
             * WAITING_WS; if wifi dropped, a wifi_down event got here
             * first and we're already in WAITING_WIFI. */
            if (s_state != ST_WAITING_WIFI) {
                transition(ST_WAITING_WS);
            }
            break;

        case HUX_APP_EV_NET_WS_MESSAGE:
            handle_ws_message(&ev->payload.ws_message);
            free(ev->payload.ws_message.data);
            ev->payload.ws_message.data = NULL;
            break;

        case HUX_APP_EV_BUTTON_K2_PRESSED:
            /* v0.2.0: log only — PTT semantics land in v0.2 when the
             * mic pipeline exists. hux_button_log already covers the
             * "edge detected" line; this entry marks the handoff into
             * the state machine so future traces can see the press
             * land at the app boundary. */
            ESP_LOGI(TAG, "app.ptt.pressed");
            break;

        case HUX_APP_EV_BUTTON_K2_RELEASED:
            ESP_LOGI(TAG, "app.ptt.released");
            break;
    }
}

static void app_task(void *unused) {
    (void)unused;
    ESP_LOGI(TAG, "app_task started");
    transition(ST_WAITING_WIFI);

    hux_app_event_t ev;
    for (;;) {
        if (xQueueReceive(s_event_q, &ev, portMAX_DELAY) == pdTRUE) {
            dispatch(&ev);
        }
    }
}

void hux_app_start(void) {
    if (s_event_q != NULL) {
        ESP_LOGE(TAG, "hux_app_start called twice — ignored");
        return;
    }
    s_event_q = xQueueCreate(EVENT_QUEUE_DEPTH, sizeof(hux_app_event_t));
    configASSERT(s_event_q != NULL);
    BaseType_t ok = xTaskCreate(app_task, "hux_app", APP_TASK_STACK, NULL,
                                APP_TASK_PRIO, NULL);
    configASSERT(ok == pdPASS);
}

bool hux_app_post_event(const hux_app_event_t *event, bool from_isr) {
    if (s_event_q == NULL || event == NULL) {
        return false;
    }
    BaseType_t ok;
    if (from_isr) {
        BaseType_t hpw = pdFALSE;
        ok = xQueueSendFromISR(s_event_q, event, &hpw);
        portYIELD_FROM_ISR(hpw);
    } else {
        ok = xQueueSend(s_event_q, event, 0);
    }
    if (ok != pdTRUE) {
        ESP_LOGW(TAG, "event queue full — dropped kind=%d", event->kind);
        return false;
    }
    return true;
}

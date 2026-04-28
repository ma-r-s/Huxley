#include "hux_app.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"

#include "hux_audio.h"
#include "hux_net.h"
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

/* ------------------------------------------------------------------ */
/*  PTT + mic streaming glue                                          */
/* ------------------------------------------------------------------ */

/* Outbound audio-frame sink. Runs on the mic task (prio 8), not here.
 * Static buffers are safe because the mic task is the only caller —
 * one-frame-at-a-time. BSS-resident, zero stack impact.
 *
 * ~960 B PCM -> ~1280 B base64 -> ~1306 B JSON envelope. Sized
 * generously so a future frame-size bump (40 ms?) doesn't require a
 * doc update. */
static uint8_t s_b64_buf[1500];
static char    s_envelope[1700];

/* --- Decoupled audio sender ---------------------------------------
 *
 * Evidence from v0.2 testing: calling `hux_net_send_text` directly
 * from the mic sink (50 Hz, 1.3 KB per send) saturates
 * esp_websocket_client's internal TX path so badly the connection
 * drops within milliseconds of `mic.start`. Root cause is that the
 * prio-8 mic task hogs the socket; the WS client's own pinger + RX
 * handlers starve, and either side of the TCP socket declares it
 * dead.
 *
 * Fix: mic_sink does a cheap memcpy into a bounded FIFO; a dedicated
 * sender task (lower prio than mic) drains it, does the expensive
 * base64 + envelope, and calls `hux_net_send_text`. Capture is
 * decoupled from send latency entirely; I2S DMA never waits on TCP.
 *
 * Overflow policy: drop-oldest isn't worth implementing yet — we log
 * the drop count and let the newest frame be rejected. A healthy LAN
 * drains faster than the mic fills; the queue only fills during
 * hiccups, and a brief hiccup of lost audio is better than the WS
 * going down for seconds.
 *
 * Depth bumped from 10 (200 ms) to 50 (1 sec) in v0.3.2 after a WS
 * stall ate 95 of 130 frames in a 2-second window — losing most of
 * a user utterance. 50 frames × 960 B/frame = 48 KB of internal RAM,
 * negligible. Beyond ~1 sec we'd be sending stale audio to OpenAI
 * after the network recovers, which is its own problem; if the WS
 * is dead longer than that the keep-alive fires and the queue gets
 * reset on reconnect.
 */
#define AUDIO_FRAME_SAMPLES 480                 /* 20 ms at 24 kHz — matches hux_audio */
#define AUDIO_QUEUE_DEPTH   50                  /* 1 sec buffer at 50 Hz */
#define AUDIO_SENDER_STACK  8192
#define AUDIO_SENDER_PRIO   6                   /* mic task (8) > this > app (5) */

typedef struct {
    int16_t pcm[AUDIO_FRAME_SAMPLES];
} audio_frame_t;

static QueueHandle_t s_audio_q = NULL;

/* Per-PTT counters. mic_sink writes, audio_sender_task writes, app
 * task reads at release — single-writer per counter, no locks. */
static uint32_t s_mic_enqueued = 0;
static uint32_t s_mic_enqueue_dropped = 0;
static uint32_t s_tx_sent = 0;
static uint32_t s_tx_failed = 0;
static uint32_t s_tx_last_stats_tick = 0;

static void mic_sink(const int16_t *pcm, size_t samples) {
    if (s_audio_q == NULL || samples != AUDIO_FRAME_SAMPLES) {
        return;
    }
    audio_frame_t frame;
    memcpy(frame.pcm, pcm, samples * sizeof(int16_t));
    if (xQueueSend(s_audio_q, &frame, 0) != pdTRUE) {
        s_mic_enqueue_dropped++;
        return;
    }
    s_mic_enqueued++;
}

static void audio_sender_task(void *unused) {
    (void)unused;
    ESP_LOGI(TAG, "audio_sender_task started depth=%d prio=%d",
             AUDIO_QUEUE_DEPTH, AUDIO_SENDER_PRIO);

    audio_frame_t frame;
    for (;;) {
        if (xQueueReceive(s_audio_q, &frame, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        const size_t pcm_bytes = AUDIO_FRAME_SAMPLES * sizeof(int16_t);
        size_t b64_len = 0;
        int rc = mbedtls_base64_encode(s_b64_buf, sizeof(s_b64_buf), &b64_len,
                                       (const unsigned char *)frame.pcm, pcm_bytes);
        if (rc != 0) {
            s_tx_failed++;
            continue;
        }
        int n = snprintf(s_envelope, sizeof(s_envelope),
                         "{\"type\":\"audio\",\"data\":\"%.*s\"}",
                         (int)b64_len, (const char *)s_b64_buf);
        if (n <= 0 || (size_t)n >= sizeof(s_envelope)) {
            s_tx_failed++;
            continue;
        }

        if (hux_net_send_text(s_envelope, (size_t)n)) {
            s_tx_sent++;
        } else {
            s_tx_failed++;
        }

        /* Emit stats every ~500 ms of wall time — tick-based so a
         * stalled stream still reports. Promoted to WARN level only
         * when something has actually gone wrong (frames dropped at
         * enqueue or a send failed); steady-state happy-path logs at
         * INFO so the console isn't a wall of yellow during normal
         * PTT. */
        uint32_t now = (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS);
        if (now - s_tx_last_stats_tick >= 500) {
            esp_log_level_t lvl =
                (s_mic_enqueue_dropped > 0 || s_tx_failed > 0)
                    ? ESP_LOG_WARN
                    : ESP_LOG_INFO;
            ESP_LOG_LEVEL(lvl, TAG,
                          "mic.tx.stats enq=%u enq_drop=%u tx_ok=%u tx_fail=%u q_waiting=%u",
                          (unsigned)s_mic_enqueued,
                          (unsigned)s_mic_enqueue_dropped,
                          (unsigned)s_tx_sent, (unsigned)s_tx_failed,
                          (unsigned)uxQueueMessagesWaiting(s_audio_q));
            s_tx_last_stats_tick = now;
        }
    }
}

/* Non-heap control-message sender. Used for tiny JSON messages
 * (ptt_start / ptt_stop / wake_word / reset) where the payload is a
 * static literal. */
static void send_control(const char *literal) {
    (void)hux_net_send_text(literal, strlen(literal));
}

static void enter_ptt(void) {
    if (s_state != ST_READY) {
        ESP_LOGW(TAG, "app.ptt.ignored_press state=%s", state_name(s_state));
        return;
    }
    /* Reset per-press counters so the stats line is scoped to this
     * PTT press, not the lifetime of the firmware. */
    s_mic_enqueued = 0;
    s_mic_enqueue_dropped = 0;
    s_tx_sent = 0;
    s_tx_failed = 0;
    s_tx_last_stats_tick = 0;

    /* Barge-in: drain any buffered model speech immediately so the
     * user hears their own input, not the previous reply finishing
     * out of the ring. The server will also receive `ptt_start` and
     * stop emitting new audio, but it can't recall what's already
     * sitting in our PSRAM ring (up to ~10 sec at 24 kHz mono). Without
     * this clear, pressing K2 mid-reply leaves Huxley audibly talking
     * over the user's question — exactly what we don't want.
     *
     * Order: clear ring FIRST so the next spk_task iteration sees an
     * empty buffer and falls through to silence; THEN announce PTT to
     * the server; THEN arm the mic. */
    hux_audio_spk_clear();

    /* Order is: announce -> arm mic. The server tolerates a frame or
     * two arriving before `ptt_start` but shouldn't have to. */
    send_control("{\"type\":\"ptt_start\"}");
    hux_audio_mic_start();
}

static void leave_ptt(void) {
    /* Stop the mic first so no more audio frames race the commit.
     * The server's PTT-stop handler commits the buffer to OpenAI —
     * late frames would be ignored, but the log noise is worse. */
    hux_audio_mic_stop();
    ESP_LOGI(TAG, "app.ptt.final_stats enq=%u enq_drop=%u tx_ok=%u tx_fail=%u",
             (unsigned)s_mic_enqueued, (unsigned)s_mic_enqueue_dropped,
             (unsigned)s_tx_sent, (unsigned)s_tx_failed);
    send_control("{\"type\":\"ptt_stop\"}");
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
            /* Kick the server into CONVERSING so PTT presses work
             * immediately. `wake_word` is named legacy-style in the
             * protocol (docs/protocol.md) — it's really a "start
             * session" signal. Sending it once per connection matches
             * the web dev client's behaviour. */
            send_control("{\"type\":\"wake_word\"}");
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
            ESP_LOGI(TAG, "app.ptt.pressed");
            enter_ptt();
            break;

        case HUX_APP_EV_BUTTON_K2_RELEASED:
            ESP_LOGI(TAG, "app.ptt.released");
            leave_ptt();
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

    /* Audio PCM ring — mic task fills, audio_sender drains. Must
     * exist before the mic sink is registered so there's never a
     * window where sink runs with NULL queue. */
    s_audio_q = xQueueCreate(AUDIO_QUEUE_DEPTH, sizeof(audio_frame_t));
    configASSERT(s_audio_q != NULL);
    ok = xTaskCreate(audio_sender_task, "hux_audio_tx", AUDIO_SENDER_STACK,
                     NULL, AUDIO_SENDER_PRIO, NULL);
    configASSERT(ok == pdPASS);

    /* Register the mic sink once — it stays registered for the life
     * of the firmware. Gating is via hux_audio_mic_start/stop from
     * enter_ptt/leave_ptt, not via swapping the sink. Safe to set
     * before hux_audio_init() runs: hux_audio stores the pointer
     * atomically, and the mic task only dereferences it after
     * `hux_audio_mic_start()` is called. */
    hux_audio_set_mic_sink(mic_sink);
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

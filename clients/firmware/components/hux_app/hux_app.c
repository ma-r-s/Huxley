#include "hux_app.h"

#include <math.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "cJSON.h"
#include "mbedtls/base64.h"

#include "hux_audio.h"
#include "hux_log.h"
#include "hux_net.h"
#include "hux_opus.h"
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

/* Talk + mic ownership (app task only). The mic streams while the talk
 * button is held, or continuously while a skill owns it (`input_mode`
 * = skill_continuous, e.g. a call). Server-authoritative: it re-sends
 * `input_mode` on every connect. */
static bool s_ptt_held = false;          /* a press we acted on (sent ptt_start) */
static bool s_mic_continuous = false;    /* input_mode == skill_continuous */
static hux_server_state_t s_server_state = HUX_SERVER_STATE_UNKNOWN;
static bool s_model_speaking = false;      /* server's model_speaking */
/* Another client took the server: the WS client is paused until the user
 * presses talk (as an evicted PWA tab waits until it is shown again;
 * reconnecting at once would evict that client, and the two would trade
 * places forever). */
static bool s_evicted = false;

static const char *server_state_name(hux_server_state_t s) {
    switch (s) {
        case HUX_SERVER_STATE_IDLE:       return "IDLE";
        case HUX_SERVER_STATE_CONNECTING: return "CONNECTING";
        case HUX_SERVER_STATE_CONVERSING: return "CONVERSING";
        default:                          return "UNKNOWN";
    }
}

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
 * Depth: 10 (200 ms), then 50 (1 s, v0.3.2, after a stall ate 95 of
 * 130 frames), now 500 (10 s, in PSRAM, F-0022). It's push to talk:
 * a question that reaches the server a few seconds late but whole beats
 * one with its middle missing, so a slow uplink fills this queue instead
 * of dropping audio (and, until F-0022, the connection).
 *
 * One lane, in order: `ptt_start`, the frames, `ptt_stop` all go through
 * this queue. When ptt_stop went straight from the app task it overtook
 * frames still queued, and under a backlog the server committed the
 * question without its end.
 */
#define AUDIO_FRAME_SAMPLES 480                 /* 20 ms at 24 kHz — matches hux_audio */
#define AUDIO_QUEUE_DEPTH   500                 /* 10 s at 50 Hz, PSRAM (~480 KB) */
/* How long the app task waits to put ptt_start/ptt_stop in a full queue
 * (a dead link drains it within WS_SEND_TIMEOUT_MS per frame). */
#define UPLINK_CONTROL_WAIT_MS 6000
#define AUDIO_SENDER_STACK  (40 * 1024) /* the Opus encoder (codec README: ~40 KB); PSRAM */
#define AUDIO_SENDER_PRIO   6                   /* mic task (8) > this > app (5) */

typedef enum { UPLINK_AUDIO = 0, UPLINK_PTT_START, UPLINK_PTT_STOP } uplink_kind_t;

typedef struct {
    uint8_t kind; /* uplink_kind_t */
    int16_t pcm[AUDIO_FRAME_SAMPLES];
} audio_frame_t;

static QueueHandle_t s_audio_q = NULL;
/* The supervisor's counters (F-0027): uplink items taken off s_audio_q,
 * and app loop wakes (at least one a second, an event or the timeout). */
static _Atomic uint32_t s_uplink_done = 0;
static _Atomic uint32_t s_app_beats = 0;

/* Per-PTT counters. mic_sink writes, audio_sender_task writes, app
 * task reads at release — single-writer per counter, no locks. */
static uint32_t s_mic_enqueued = 0;
static uint32_t s_mic_enqueue_dropped = 0;
static uint32_t s_tx_sent = 0;
static uint32_t s_tx_failed = 0;
static uint32_t s_tx_last_stats_tick = 0;
/* Loudest mic sample this press (absolute). Near 0 = the mic heard
 * nothing; near 32767 = clipping. Written by mic_sink, read at release. */
static uint32_t s_mic_peak = 0;
/* Samples at full scale (clipped) and the sum of squares, for this
 * press: a peak of 0 dBFS alone can't tell one click from a clipped
 * question (F-0022 audit: 76 of 80 presses peaked at 0 dBFS). */
static uint32_t s_mic_clipped = 0;
static uint64_t s_mic_sumsq = 0; /* integer: the S3 has no double FPU */
static uint32_t s_mic_samples = 0;

/* Who this device is, sent as `client_event: device` on every connect
 * (the PWA sends the same event) so the server log says which firmware is
 * talking. Set once by app_main before Wi-Fi starts. */
static hux_app_device_info_t s_device;

static void mic_sink(const int16_t *pcm, size_t samples) {
    if (s_audio_q == NULL || samples != AUDIO_FRAME_SAMPLES) {
        return;
    }
    for (size_t i = 0; i < samples; i++) {
        uint32_t a = (uint32_t)(pcm[i] < 0 ? -(int32_t)pcm[i] : pcm[i]);
        if (a > s_mic_peak) {
            s_mic_peak = a;
        }
        if (a >= 32767) {
            s_mic_clipped++;
        }
        s_mic_sumsq += (uint64_t)((int32_t)pcm[i] * (int32_t)pcm[i]);
    }
    s_mic_samples += (uint32_t)samples;
    audio_frame_t frame;
    frame.kind = UPLINK_AUDIO;
    memcpy(frame.pcm, pcm, samples * sizeof(int16_t));
    if (xQueueSend(s_audio_q, &frame, 0) != pdTRUE) {
        s_mic_enqueue_dropped++;
        return;
    }
    s_mic_enqueued++;
}

/* Opus (F-0024): 20 ms mic frames gathered into one 60 ms packet. This
 * task only. */
EXT_RAM_BSS_ATTR static int16_t s_opus_pcm[HUX_OPUS_FRAME_SAMPLES];
static size_t s_opus_fill;    /* samples gathered */
EXT_RAM_BSS_ATTR static uint8_t s_opus_pkt[HUX_OPUS_MAX_PACKET];
static uint32_t s_opus_bytes; /* sent this press, for the stats */

/* Encode and send what is gathered; a short last packet is padded with
 * silence (the end of a question must not be lost). */
static void opus_flush(void) {
    if (s_opus_fill == 0) {
        return;
    }
    uint32_t frames = (uint32_t)(s_opus_fill / AUDIO_FRAME_SAMPLES);
    memset(s_opus_pcm + s_opus_fill, 0, (HUX_OPUS_FRAME_SAMPLES - s_opus_fill) * sizeof(int16_t));
    s_opus_fill = 0;
    int n = hux_opus_encode(s_opus_pcm, s_opus_pkt, sizeof(s_opus_pkt));
    if (n > 0 && hux_net_send_binary(s_opus_pkt, (size_t)n)) {
        s_tx_sent += frames;
        s_opus_bytes += (uint32_t)n;
    } else {
        s_tx_failed += frames;
    }
}

/* Today's wire format, for a server that didn't agree to Opus: one 20 ms
 * frame as base64 in a JSON `audio` message. */
static void send_pcm_json(const audio_frame_t *frame) {
    const size_t pcm_bytes = AUDIO_FRAME_SAMPLES * sizeof(int16_t);
    size_t b64_len = 0;
    int rc = mbedtls_base64_encode(s_b64_buf, sizeof(s_b64_buf), &b64_len,
                                   (const unsigned char *)frame->pcm, pcm_bytes);
    if (rc != 0) {
        s_tx_failed++;
        return;
    }
    int n = snprintf(s_envelope, sizeof(s_envelope),
                     "{\"type\":\"audio\",\"data\":\"%.*s\"}",
                     (int)b64_len, (const char *)s_b64_buf);
    if (n <= 0 || (size_t)n >= sizeof(s_envelope)) {
        s_tx_failed++;
        return;
    }

    if (hux_net_send_text(s_envelope, (size_t)n)) {
        s_tx_sent++;
    } else {
        s_tx_failed++;
    }
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
        atomic_fetch_add(&s_uplink_done, 1);
        if (frame.kind != UPLINK_AUDIO) {
            /* In order with the frames: everything said before the release
             * is on the wire before ptt_stop. */
            bool stop = frame.kind == UPLINK_PTT_STOP;
            if (stop) {
                opus_flush(); /* the last partial packet, before ptt_stop */
            } else {
                s_opus_fill = 0; /* a new press: a fresh Opus stream */
                s_opus_bytes = 0;
                hux_opus_encode_reset();
            }
            const char *msg = stop ? "{\"type\":\"ptt_stop\"}" : "{\"type\":\"ptt_start\"}";
            bool ok = hux_net_send_text(msg, strlen(msg));
            if (stop) {
                ESP_LOGI(TAG, "mic.tx.done ptt_stop_sent=%d enq=%u enq_drop=%u tx_ok=%u tx_fail=%u "
                              "opus_bytes=%u",
                         ok, (unsigned)s_mic_enqueued, (unsigned)s_mic_enqueue_dropped,
                         (unsigned)s_tx_sent, (unsigned)s_tx_failed, (unsigned)s_opus_bytes);
            } else if (!ok) {
                ESP_LOGW(TAG, "mic.tx.ptt_start_failed");
            }
            continue;
        }

        if (hux_net_audio_opus()) {
            memcpy(s_opus_pcm + s_opus_fill, frame.pcm, AUDIO_FRAME_SAMPLES * sizeof(int16_t));
            s_opus_fill += AUDIO_FRAME_SAMPLES;
            if (s_opus_fill == HUX_OPUS_FRAME_SAMPLES) {
                opus_flush();
            }
        } else {
            send_pcm_json(&frame);
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
                          "mic.tx.stats enq=%u enq_drop=%u tx_ok=%u tx_fail=%u q_waiting=%u "
                          "opus_bytes=%u enc_us=%u",
                          (unsigned)s_mic_enqueued,
                          (unsigned)s_mic_enqueue_dropped,
                          (unsigned)s_tx_sent, (unsigned)s_tx_failed,
                          (unsigned)uxQueueMessagesWaiting(s_audio_q), (unsigned)s_opus_bytes,
                          (unsigned)hux_opus_take_enc_us());
            s_tx_last_stats_tick = now;
        }
    }
}

/* Start or stop the mic to match who owns it now. */
static void mic_update(const char *why) {
    bool want = s_ptt_held || s_mic_continuous;
    if (want == hux_audio_mic_is_running()) {
        return;
    }
    ESP_LOGI(TAG, "app.mic %s why=%s ptt=%d continuous=%d", want ? "on" : "off", why,
             s_ptt_held, s_mic_continuous);
    if (want) {
        hux_audio_mic_start();
    } else {
        hux_audio_mic_stop();
    }
}

/* Non-heap control-message sender. Used for tiny JSON messages
 * (wake_word / reset) where the payload is a static literal. ptt_start
 * and ptt_stop go through uplink_control instead. */
static void send_control(const char *literal) {
    (void)hux_net_send_text(literal, strlen(literal));
}

/* ptt_start / ptt_stop, queued behind the press's audio (see the uplink
 * comment above). */
static void uplink_control(uplink_kind_t kind) {
    static audio_frame_t marker; /* app task only; the pcm is ignored */
    marker.kind = (uint8_t)kind;
    if (xQueueSend(s_audio_q, &marker, pdMS_TO_TICKS(UPLINK_CONTROL_WAIT_MS)) != pdTRUE) {
        ESP_LOGE(TAG, "mic.tx.queue_full %s not sent",
                 kind == UPLINK_PTT_STOP ? "ptt_stop" : "ptt_start");
    }
}

static void send_device_info(void) {
    char json[320];
    int n = snprintf(json, sizeof(json),
                     "{\"type\":\"client_event\",\"event\":\"device\",\"data\":{"
                     "\"shell\":\"firmware\",\"board\":\"esp32s3-audio-board\","
                     "\"version\":\"%s\",\"mac\":\"%s\",\"reset\":\"%s\","
                     "\"boot_counter\":%u,\"rssi\":%d,\"volume\":%d}}",
                     s_device.version ? s_device.version : "?", s_device.mac,
                     s_device.reset ? s_device.reset : "?", (unsigned)s_device.boot_counter,
                     hux_net_rssi(), hux_audio_get_volume());
    if (n > 0 && (size_t)n < sizeof(json)) {
        send_control(json);
    }
}

/* The previous boot's last warnings and errors (RTC memory), once, after
 * the hello: the lines that explain a reset otherwise die with it
 * (F-0027). Kept until a send succeeds. */
static void send_blackbox(void) {
    uint32_t dropped = 0;
    const char *lines = hux_log_blackbox(&dropped);
    if (lines == NULL) {
        return;
    }
    cJSON *root = cJSON_CreateObject();
    cJSON *data = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "type", "client_event");
    cJSON_AddStringToObject(root, "event", "device.blackbox");
    cJSON_AddStringToObject(data, "lines", lines);
    cJSON_AddNumberToObject(data, "dropped", dropped);
    cJSON_AddItemToObject(root, "data", data);
    char *json = cJSON_PrintUnformatted(root);
    bool ok = json != NULL && hux_net_send_text(json, strlen(json));
    ESP_LOGI(TAG, "app.blackbox bytes=%u dropped=%u sent=%d", (unsigned)strlen(lines),
             (unsigned)dropped, ok);
    if (ok) {
        hux_log_blackbox_sent();
    }
    cJSON_free(json);
    cJSON_Delete(root);
}

/* The device can't reach the server (no Wi-Fi, no WebSocket, no hello
 * yet), so nobody else can tell the user: a short low falling pair of
 * tones, made here. The user can't see a light; silence would read as
 * "it heard me" or "it's broken". */
static void play_offline_cue(void) {
    hux_audio_tone(440, 120);
    hux_audio_tone(0, 60);
    hux_audio_tone(330, 220);
}

static void enter_ptt(void) {
    if (s_state != ST_READY) {
        ESP_LOGW(TAG, "app.ptt.ignored_press state=%s evicted=%d client_running=%d cue=offline",
                 state_name(s_state), s_evicted, hux_net_is_running());
        /* Stop whatever is still playing (the ring can hold a whole reply
         * from before the drop), then the cue: a clear drops queued tones,
         * and tones only play once the ring is empty. */
        hux_audio_spk_clear();
        play_offline_cue();
        if (s_evicted || !hux_net_is_running()) {
            /* The user wants the walkie back: take the server again (or
             * restart a client that ended and couldn't restart itself). */
            s_evicted = false;
            hux_net_resume();
        }
        return;
    }
    if (s_server_state != HUX_SERVER_STATE_CONVERSING) {
        /* The server drops audio until its session is up again (it
         * reconnects on its own, and tones if that keeps failing). Logged
         * so a press that got no answer explains itself. */
        ESP_LOGW(TAG, "app.ptt.press_while_server=%s", server_state_name(s_server_state));
    }
    /* Reset per-press counters so the stats line is scoped to this
     * PTT press, not the lifetime of the firmware. */
    s_mic_enqueued = 0;
    s_mic_enqueue_dropped = 0;
    s_tx_sent = 0;
    s_tx_failed = 0;
    s_tx_last_stats_tick = 0;
    s_mic_peak = 0;
    s_mic_clipped = 0;
    s_mic_sumsq = 0;
    s_mic_samples = 0;

    /* Barge-in: drain any buffered model speech immediately so the
     * user hears their own input, not the previous reply finishing
     * out of the ring. The server will also receive `ptt_start` and
     * stop emitting new audio, but it can't recall what's already
     * sitting in our PSRAM ring (up to ~10 sec at 24 kHz mono). Without
     * this clear, pressing K2 mid-reply leaves Huxley audibly talking
     * over the user's question — exactly what we don't want.
     *
     * Order: hold + clear the ring FIRST so the next spk_task iteration
     * sees an empty buffer and falls through to silence; THEN announce
     * PTT to the server; THEN arm the mic. */
    /* The reply's audio still on its way is dropped until the server's
     * audio_clear (hux_audio). The hold goes on before the clear: a write
     * waiting for room in a full ring must not land in the emptied one. */
    if (s_model_speaking) {
        hux_audio_spk_hold();
    }
    hux_audio_spk_clear();

    /* Order is: announce -> arm mic. The server tolerates a frame or
     * two arriving before `ptt_start` but shouldn't have to. During a
     * skill's claim (a call) the mic is already on; the press tells the
     * server to end the claim. */
    uplink_control(UPLINK_PTT_START);
    s_ptt_held = true;
    mic_update("ptt_start");
}

static void leave_ptt(void) {
    if (!s_ptt_held) {
        /* Its press was ignored (not connected): no ptt_start went out,
         * so no ptt_stop either. */
        return;
    }
    /* Stop the mic first so no more audio frames race the commit.
     * The server's PTT-stop handler commits the buffer to OpenAI —
     * late frames would be ignored, but the log noise is worse. A
     * skill still owning the mic keeps it on. */
    s_ptt_held = false;
    mic_update("ptt_stop");
    /* The audio sender may still be draining the queue: tx_ok here can
     * trail enq by the frames still in flight (mic.tx.done has the
     * totals, when ptt_stop goes out behind them). */
    int peak_dbfs = s_mic_peak == 0 ? -96 : (int)lroundf(20.0f * log10f((float)s_mic_peak / 32768.0f));
    double ms = s_mic_samples > 0 ? s_mic_sumsq / (double)s_mic_samples : 0.0;
    int rms_dbfs = ms <= 0.0 ? -96 : (int)lround(10.0 * log10(ms / (32768.0 * 32768.0)));
    ESP_LOGI(TAG, "app.ptt.final_stats enq=%u enq_drop=%u tx_ok=%u tx_fail=%u peak=%u peak_dbfs=%d "
                  "rms_dbfs=%d clipped=%u",
             (unsigned)s_mic_enqueued, (unsigned)s_mic_enqueue_dropped,
             (unsigned)s_tx_sent, (unsigned)s_tx_failed, (unsigned)s_mic_peak, peak_dbfs, rms_dbfs,
             (unsigned)s_mic_clipped);
    uplink_control(UPLINK_PTT_STOP);
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
            if (!parsed.as.hello.session_ready) {
                /* T1.16: the server has no live session (persona failed
                 * to load, ...). Talk presses will get no answer. */
                ESP_LOGW(TAG, "ws.rx.hello session_ready=false: the server cannot answer yet");
            }
            /* F-0024: Opus both ways if the server agreed to our ?audio=opus;
             * an older server keeps base64 PCM. */
            hux_net_set_audio_opus(parsed.as.hello.audio_opus);
            hux_net_hello_ok(); /* a pending server move is proven (F-0027) */
            ESP_LOGI(TAG, "app.audio format=%s", parsed.as.hello.audio_opus ? "opus" : "pcm");
            transition(ST_READY);
            send_device_info();
            send_blackbox();
            /* Kick the server into CONVERSING so PTT presses work
             * immediately. `wake_word` is named legacy-style in the
             * protocol (docs/protocol.md) — it's really a "start
             * session" signal. Sending it once per connection matches
             * the web dev client's behaviour. */
            send_control("{\"type\":\"wake_word\"}");
            break;

        case HUX_MSG_STATE:
            if (parsed.as.state.value != s_server_state) {
                ESP_LOGI(TAG, "ws.rx.state %s -> %s", server_state_name(s_server_state),
                         server_state_name(parsed.as.state.value));
                s_server_state = parsed.as.state.value;
            }
            break;

        case HUX_MSG_MODEL_SPEAKING:
            if (parsed.as.model_speaking.known && parsed.as.model_speaking.value != s_model_speaking) {
                s_model_speaking = parsed.as.model_speaking.value;
                hux_audio_set_stream_active(s_model_speaking);
                ESP_LOGI(TAG, "ws.rx.model_speaking %s", s_model_speaking ? "true" : "false");
                if (!s_model_speaking) {
                    /* It ended before our press reached it: no audio_clear.
                     * With Opus the old reply's packets may still be queued
                     * for the decoder: release after them, not before. */
                    if (hux_net_audio_opus()) {
                        hux_opus_rx_unhold();
                    } else {
                        hux_audio_spk_unhold("model_speaking_false");
                    }
                }
            }
            break;

        case HUX_MSG_SET_VOLUME:
            /* The server's system skill ("sube el volumen"): absolute 0..100. */
            if (parsed.as.set_volume.level < 0) {
                ESP_LOGW(TAG, "ws.rx.set_volume without a level, ignored");
            } else {
                hux_audio_set_volume(parsed.as.set_volume.level);
            }
            break;

        case HUX_MSG_INPUT_MODE:
            if (!parsed.as.input_mode.known) {
                ESP_LOGW(TAG, "ws.rx.input_mode unknown mode, keeping %s",
                         s_mic_continuous ? "skill_continuous" : "assistant_ptt");
                break;
            }
            if (parsed.as.input_mode.continuous != s_mic_continuous) {
                ESP_LOGI(TAG, "ws.rx.input_mode %s",
                         parsed.as.input_mode.continuous ? "skill_continuous" : "assistant_ptt");
                s_mic_continuous = parsed.as.input_mode.continuous;
                mic_update("input_mode");
            }
            break;

        case HUX_MSG_AUDIO_CLEAR:
            /* Normally handled on the WS task, in order with the audio
             * (hux_net). One only gets here if that probe missed it: clear
             * anyway, late rather than never. */
            ESP_LOGW(TAG, "ws.rx.audio_clear on the control plane (probe missed it)");
            hux_audio_on_audio_clear();
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
            /* Once per outage: every failed retry posts this too, and an
             * outage's lines wait in the 256-line queue (hux_net logs the
             * attempts, thinned). */
            if (s_state != ST_WAITING_WS) {
                ESP_LOGW(TAG, "net.ws.disconnected state=%s", state_name(s_state));
            }
            /* Nothing can be sent: stop the mic. The server re-sends its
             * state and input_mode on the next connect; a held button
             * has to be pressed again. */
            s_ptt_held = false;
            s_mic_continuous = false;
            s_server_state = HUX_SERVER_STATE_UNKNOWN;
            s_model_speaking = false;
            hux_audio_set_stream_active(false);
            hux_audio_spk_unhold("ws_disconnected");
            mic_update("ws_disconnected");
            /* Preserve WIFI state — if wifi is still up, we go to
             * WAITING_WS; if wifi dropped, a wifi_down event got here
             * first and we're already in WAITING_WIFI. */
            if (s_state != ST_WAITING_WIFI) {
                transition(ST_WAITING_WS);
            }
            break;

        case HUX_APP_EV_NET_WS_EVICTED:
            ESP_LOGW(TAG, "net.ws.evicted: another client has the server; reconnecting on the "
                          "next talk press");
            s_evicted = true;
            hux_net_pause();
            /* The library redials 2 s after a close: a CONNECTED and a hello
             * from that redial may already be queued behind this event.
             * This disconnect lands after them, so the app doesn't end up
             * READY on the client it just stopped. */
            {
                hux_app_event_t down = {.kind = HUX_APP_EV_NET_WS_DISCONNECTED};
                hux_app_post_event(&down, false);
            }
            break;

        case HUX_APP_EV_DEBUG_HANG:
            ESP_LOGW(TAG, "app.debug_hang: the app task stops here (supervisor test)");
            for (;;) {
                vTaskDelay(portMAX_DELAY);
            }
            break;

        case HUX_APP_EV_NET_WS_FINISHED:
            /* A FINISH can arrive after a press already restarted the
             * client: resume() does nothing while one is running. */
            if (!s_evicted && !hux_net_is_running()) {
                ESP_LOGW(TAG, "net.ws.finished unexpectedly: restarting the client");
                hux_net_resume();
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
        /* A 1 s timeout so the supervisor sees the loop alive while idle. */
        if (xQueueReceive(s_event_q, &ev, pdMS_TO_TICKS(1000)) == pdTRUE) {
            dispatch(&ev);
        }
        atomic_fetch_add(&s_app_beats, 1);
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
    s_audio_q = xQueueCreateWithCaps(AUDIO_QUEUE_DEPTH, sizeof(audio_frame_t), MALLOC_CAP_SPIRAM);
    configASSERT(s_audio_q != NULL);
    ok = xTaskCreateWithCaps(audio_sender_task, "hux_audio_tx", AUDIO_SENDER_STACK, NULL,
                             AUDIO_SENDER_PRIO, NULL, MALLOC_CAP_SPIRAM);
    configASSERT(ok == pdPASS);

    /* Register the mic sink once — it stays registered for the life
     * of the firmware. Gating is via hux_audio_mic_start/stop from
     * enter_ptt/leave_ptt, not via swapping the sink. Safe to set
     * before hux_audio_init() runs: hux_audio stores the pointer
     * atomically, and the mic task only dereferences it after
     * `hux_audio_mic_start()` is called. */
    hux_audio_set_mic_sink(mic_sink);
}

void hux_app_set_device_info(const hux_app_device_info_t *info) {
    if (info != NULL) {
        s_device = *info;
    }
}

uint32_t hux_app_beats(void) {
    return atomic_load(&s_app_beats);
}

void hux_app_uplink_progress(uint32_t *done, uint32_t *pending) {
    *done = atomic_load(&s_uplink_done);
    *pending = s_audio_q != NULL ? (uint32_t)uxQueueMessagesWaiting(s_audio_q) : 0;
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

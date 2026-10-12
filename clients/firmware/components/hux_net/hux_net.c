#include "hux_net.h"

#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_attr.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"

#include "hux_app.h"
#include "hux_log.h"
#include "hux_type_probe.h"
#include "hux_wifi_retry.h"
#include "hux_ws_frag.h"

static const char *TAG = "hux_net";

/* sdkconfig (gitignored) keeps whatever it had when it was first made:
 * edits to sdkconfig.defaults never reach an existing one. These values
 * are what keeps a long reply from stalling (F-0015); an old sdkconfig
 * builds the bug back in, silently. Fail the build instead. */
#if CONFIG_LWIP_TCP_RECVMBOX_SIZE < 25 || CONFIG_LWIP_TCP_OOSEQ_MAX_PBUFS == 0 || \
    CONFIG_LWIP_TCP_WND_DEFAULT > CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM * 1024
#error "sdkconfig is older than sdkconfig.defaults: delete it and run `idf.py reconfigure` (F-0015)"
#endif

#define WIFI_CONNECTED_BIT BIT0
/* Network ring buffer sized for the largest single WS text frame we
 * reasonably expect in v0 (hello / state / status / audio deltas are
 * all under 4 KB). Revisit when inbound audio chunks grow. */
/* esp_websocket_client's per-message RX/TX buffer. Sets the ceiling
 * for a single WebSocket *message* size — any inbound message larger
 * than this arrives fragmented and (today) is dropped by
 * `forward_ws_text` (see triage F-0004 for the v0.3 reassembly plan).
 * This is independent of LWIP's TCP-level send buffer — that's
 * `CONFIG_LWIP_TCP_SND_BUF_DEFAULT` in sdkconfig.defaults, which
 * governs how many TCP bytes can be queued for transmission. */
#define WS_BUFFER_SIZE     32768
#define WS_RECONNECT_MS    2000
/* Bounded send timeout — if TCP stalls, the 50 Hz audio sender task
 * must fail fast, not wedge. Longer than any reasonable flush on a
 * healthy LAN (~1 ms); short enough that a stall becomes a warning
 * we can act on before the watchdog does. */
#define WS_SEND_TIMEOUT_MS 200

static EventGroupHandle_t s_wifi_events = NULL;
static esp_websocket_client_handle_t s_ws = NULL;
static uint32_t s_wifi_retries = 0;   /* attempts since the last IP */
static esp_timer_handle_t s_wifi_retry_timer = NULL;
/* The WS client starts on the first IP, not at boot: started earlier its
 * first connect fails ("Host is unreachable", logged as an error on every
 * boot) and the next try waits out the 2 s reconnect timer. Only the
 * system event task touches this. */
static bool s_ws_started = false;
/* The last CLOSE frame the server sent (WS task): an eviction ("Replaced
 * by new client", 1001) is told apart from a restart or a ping timeout. */
static bool s_close_was_eviction = false;
static char s_server_uri[128] = {0};

/* Audio data-plane seam — see firmware/docs/architecture.md §"Data
 * plane vs control plane". Inbound `audio` messages bypass
 * hux_app's queue entirely; at 50 Hz the control-plane queue cannot
 * keep up with per-frame JSON parses + heap copies. Sink pointer
 * reads cross CPU cores (set from app_main, called on the WS client
 * task) so it's atomic. */
static _Atomic(hux_net_audio_sink_fn) s_audio_sink = NULL;
/* `audio_clear` runs on the same task as `audio`, in arrival order: a
 * clear posted through hux_app's queue could land after the next reply's
 * first chunks and wipe them. */
static _Atomic(hux_net_audio_clear_fn) s_audio_clear_sink = NULL;
/* Decode scratch — single-writer (the WS client task), so no lock is
 * needed. Sized to fit the decoded PCM of the largest message we
 * accept: WS_FRAG_BUFFER_BYTES (384 KB JSON+base64) decodes to about
 * 287 KB PCM (base64 expansion is 4/3, plus JSON envelope overhead).
 * Round up to 288 KB.
 *
 * Why this big: OpenAI Realtime occasionally bursts a buffered audio
 * delta of ~3 sec of speech in a single message (137 KB PCM = 183 KB
 * envelope was observed in v0.3.1 testing). The previous 96 KB ceiling
 * caused every such burst to fail base64 decode and disappear, taking
 * 2-3 sec of speech with it (firmware/docs/triage.md F-0012).
 *
 * PSRAM-resident; access latency is a few hundred ns per byte,
 * negligible against the 20 ms audio frame period. */
#define AUDIO_SCRATCH_BYTES (288 * 1024)
EXT_RAM_BSS_ATTR static uint8_t s_audio_scratch[AUDIO_SCRATCH_BYTES];

/* WebSocket fragment reassembly scratch — see firmware/docs/triage.md
 * F-0003 for the original motivation, F-0012 for the v0.3.2 bump.
 * Server responses (OpenAI audio deltas in particular) can exceed
 * WS_BUFFER_SIZE, arriving as multiple fragments with increasing
 * payload_offset. We accumulate into this PSRAM buffer; the
 * reassembler owns the state.
 *
 * 384 KB sized for the largest realistic single OpenAI Realtime
 * audio burst: ~3 sec of speech ≈ 137 KB PCM ≈ 183 KB JSON envelope.
 * Doubled for headroom and rounded to 384 KB. Anything bigger than
 * this and the server is misbehaving (no single message should
 * encode more than a few seconds of audio — that's a server-side
 * pacing bug, not a firmware problem to absorb).
 *
 * Single-writer (WS client RX task), so the reassembler is
 * lock-free. Unit-tested in firmware/tests/test_hux_ws_frag.c. */
#define WS_FRAG_BUFFER_BYTES (384 * 1024)
EXT_RAM_BSS_ATTR static char s_frag_buf[WS_FRAG_BUFFER_BYTES];
static hux_ws_reassembler_t s_reassembler;

/* Inbound stats, WS task only. One `ws.rx.stats` line per second while
 * audio flows: a reply that never plays shows here first (did audio
 * arrive? how fast? how long did the socket go quiet?). */
static uint32_t s_rx_events = 0;
static uint32_t s_rx_bytes = 0;
static uint32_t s_rx_msgs = 0;
static uint32_t s_rx_audio_msgs = 0;
static uint32_t s_rx_audio_bytes = 0;
static uint32_t s_rx_max_gap_ms = 0;
static uint32_t s_rx_busy_us = 0;      /* time spent in our handler */
static uint32_t s_rx_max_busy_us = 0;
static uint32_t s_rx_window_start_ms = 0;
static _Atomic uint32_t s_rx_last_ms = 0;
#define RX_STATS_WINDOW_MS 1000

static uint32_t now_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void rx_stats_tick(uint32_t now) {
    if (now - s_rx_window_start_ms < RX_STATS_WINDOW_MS) {
        return;
    }
    if (s_rx_audio_msgs > 0) {
        ESP_LOGI(TAG, "ws.rx.stats window_ms=%u events=%u bytes=%u msgs=%u audio_msgs=%u "
                      "audio_ms=%u max_gap_ms=%u busy_ms=%u max_busy_ms=%u",
                 (unsigned)(now - s_rx_window_start_ms), (unsigned)s_rx_events,
                 (unsigned)s_rx_bytes, (unsigned)s_rx_msgs, (unsigned)s_rx_audio_msgs,
                 (unsigned)(s_rx_audio_bytes / 48), (unsigned)s_rx_max_gap_ms,
                 (unsigned)(s_rx_busy_us / 1000), (unsigned)(s_rx_max_busy_us / 1000));
    }
    s_rx_events = s_rx_bytes = s_rx_msgs = s_rx_audio_msgs = s_rx_audio_bytes = 0;
    s_rx_max_gap_ms = s_rx_busy_us = s_rx_max_busy_us = 0;
    s_rx_window_start_ms = now;
}

static void post_app(hux_app_event_kind_t kind) {
    hux_app_event_t ev = {.kind = kind};
    hux_app_post_event(&ev, false);
}

/* ------------------------------------------------------------------ */
/*  Wi-Fi                                                             */
/* ------------------------------------------------------------------ */

static void wifi_retry_timer_cb(void *arg) {
    (void)arg;
    esp_wifi_connect();
}

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_CONNECTED) {
        /* Which AP (a mesh has several with one SSID) and how well. */
        const wifi_event_sta_connected_t *c = (const wifi_event_sta_connected_t *)data;
        ESP_LOGI(TAG, "wifi.associated bssid=%02x:%02x:%02x:%02x:%02x:%02x channel=%d rssi=%d",
                 c->bssid[0], c->bssid[1], c->bssid[2], c->bssid[3], c->bssid[4], c->bssid[5],
                 c->channel, hux_net_rssi());
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        /* Retry forever (hux_wifi_retry): a few at once, then backing off
         * to every 30 s. The reason code says why (201 no AP found, 15 /
         * 204 wrong password, 8 we left). */
        const wifi_event_sta_disconnected_t *d = (const wifi_event_sta_disconnected_t *)data;
        s_wifi_retries++;
        uint32_t delay_ms = hux_wifi_retry_delay_ms(s_wifi_retries);
        ESP_LOGW(TAG, "wifi.disconnected reason=%d retry=%u next_in_ms=%u",
                 d != NULL ? (int)d->reason : -1, (unsigned)s_wifi_retries, (unsigned)delay_ms);
        if (delay_ms == 0) {
            esp_wifi_connect();
        } else {
            esp_timer_stop(s_wifi_retry_timer); /* not running is fine */
            esp_timer_start_once(s_wifi_retry_timer, (uint64_t)delay_ms * 1000);
        }
        post_app(HUX_APP_EV_NET_WIFI_DOWN);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "wifi.got_ip ip=" IPSTR " rssi=%d", IP2STR(&event->ip_info.ip),
                 hux_net_rssi());
        s_wifi_retries = 0;
        esp_timer_stop(s_wifi_retry_timer); /* a pending retry is moot now */
        xEventGroupSetBits(s_wifi_events, WIFI_CONNECTED_BIT);
        post_app(HUX_APP_EV_NET_WIFI_UP);
        if (!s_ws_started && s_ws != NULL) {
            /* From here on esp_websocket_client reconnects on its own. */
            esp_err_t err = esp_websocket_client_start(s_ws);
            s_ws_started = err == ESP_OK;
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "ws.start_failed err=%s (retried on the next IP)",
                         esp_err_to_name(err));
            } else {
                ESP_LOGI(TAG, "ws.start uri=%s", s_server_uri);
            }
        }
    }
}

static void wifi_init_sta(const char *ssid, const char *password) {
    s_wifi_events = xEventGroupCreate();
    configASSERT(s_wifi_events != NULL);
    const esp_timer_create_args_t retry_args = {
        .callback = wifi_retry_timer_cb,
        .name = "wifi_retry",
    };
    ESP_ERROR_CHECK(esp_timer_create(&retry_args, &s_wifi_retry_timer));

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
    /* Join the strongest AP with this SSID. The default fast scan joins the
     * first one it hears: on a mesh (several APs, one SSID) the bench board
     * landed at -26, -60 and -87 dBm on three boots a metre from the same
     * node. A full scan costs about a second at boot. */
    wifi_config.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    wifi_config.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    /* Mains-powered audio device — battery life is irrelevant.
     * Default `WIFI_PS_MIN_MODEM` parks the radio between AP beacons
     * (typically 100 ms intervals) and the first packet after an
     * idle period waits for the next wake, blowing through the
     * 200 ms WS write timeout when two beacons stack unfavourably.
     * Symptom (firmware/docs/triage.md F-0013): after ~10 sec of
     * conversational silence, the next outbound mic frame trips
     * `transport_poll_write(0)` and the WS lock starves.
     * `WIFI_PS_NONE` keeps the radio always-awake — first-packet
     * latency drops from ~50-150 ms to <5 ms. Power cost on
     * mains-powered hardware is irrelevant. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_LOGI(TAG, "wifi.init ssid=\"%s\" ps=NONE", ssid);
}

/* ------------------------------------------------------------------ */
/*  WebSocket                                                          */
/* ------------------------------------------------------------------ */

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
    s_rx_audio_msgs++;
    s_rx_audio_bytes += pcm_len;
    sink(s_audio_scratch, pcm_len);
}

/* Dispatch a complete (reassembled or single-frame) message. Audio
 * goes to the zero-copy data-plane sink; everything else gets copied
 * onto the heap and posted to hux_app's control queue. */
static void dispatch_complete_message(const char *msg, size_t msg_len) {
    s_rx_msgs++;
    if (hux_has_type(msg, msg_len, "\"audio\"")) {
        dispatch_audio(msg, msg_len);
        return;
    }
    if (hux_has_type(msg, msg_len, "\"audio_clear\"")) {
        hux_net_audio_clear_fn clear =
            atomic_load_explicit(&s_audio_clear_sink, memory_order_acquire);
        ESP_LOGI(TAG, "ws.rx.audio_clear");
        if (clear != NULL) {
            clear();
        }
        return;
    }

    char *copy = malloc(msg_len + 1);
    if (copy == NULL) {
        ESP_LOGE(TAG, "ws.rx.oom len=%u", (unsigned)msg_len);
        return;
    }
    memcpy(copy, msg, msg_len);
    copy[msg_len] = '\0';

    hux_app_event_t ev = {
        .kind = HUX_APP_EV_NET_WS_MESSAGE,
        .payload.ws_message = {.data = copy, .len = msg_len},
    };
    if (!hux_app_post_event(&ev, false)) {
        /* Post failed; own the memory we just allocated. */
        free(copy);
    }
}

/* Handle a single WS DATA event. Ignores control frames, runs text
 * fragments through the reassembler, and dispatches on the first
 * `HUX_FRAG_READY`. Binary frames are flagged loud — protocol is
 * JSON-only. */
/* A CLOSE frame from the server: code (2 bytes, big-endian) + reason. */
static void note_close_frame(const esp_websocket_event_data_t *data) {
    int code = -1;
    char reason[48] = {0};
    if (data->data_len >= 2 && data->data_ptr != NULL) {
        code = ((uint8_t)data->data_ptr[0] << 8) | (uint8_t)data->data_ptr[1];
        int n = data->data_len - 2 < (int)sizeof(reason) - 1 ? data->data_len - 2
                                                             : (int)sizeof(reason) - 1;
        memcpy(reason, data->data_ptr + 2, (size_t)n);
    }
    s_close_was_eviction = code == 1001 && strstr(reason, "Replaced") != NULL;
    ESP_LOGW(TAG, "ws.rx.close code=%d reason=\"%s\" eviction=%d", code, reason,
             s_close_was_eviction);
}

static void forward_ws_text(const esp_websocket_event_data_t *data) {
    if (data->op_code != 0x01 /* TEXT */) {
        if (data->op_code == 0x02) {
            ESP_LOGW(TAG, "ws.rx.binary len=%d (unexpected)", data->data_len);
        } else if (data->op_code == 0x08) {
            note_close_frame(data);
        }
        /* Control frames (PING/PONG/CLOSE) and stray binary are handled
         * by esp_websocket_client internally; the reassembler doesn't
         * see them (we skip the call entirely). */
        return;
    }

    int64_t t0 = esp_timer_get_time();
    uint32_t now = (uint32_t)(t0 / 1000);
    uint32_t last = atomic_load_explicit(&s_rx_last_ms, memory_order_relaxed);
    if (last != 0 && now - last > s_rx_max_gap_ms) {
        s_rx_max_gap_ms = now - last;
    }
    atomic_store_explicit(&s_rx_last_ms, now, memory_order_relaxed);
    s_rx_events++;
    s_rx_bytes += (uint32_t)data->data_len;

    const char *msg = NULL;
    size_t msg_len = 0;
    hux_frag_result_t rc = hux_ws_reassemble(
        &s_reassembler,
        data->op_code,
        data->payload_offset,
        data->data_len,
        data->payload_len,
        data->data_ptr,
        &msg, &msg_len);

    switch (rc) {
        case HUX_FRAG_READY:
            dispatch_complete_message(msg, msg_len);
            break;
        case HUX_FRAG_NEED_MORE:
            /* Silent common case on multi-fragment messages. */
            break;
        case HUX_FRAG_DROPPED:
            ESP_LOGW(TAG, "ws.rx.frag.dropped off=%d len=%d/%d",
                     data->payload_offset, data->data_len, data->payload_len);
            break;
    }
    uint32_t busy = (uint32_t)(esp_timer_get_time() - t0);
    s_rx_busy_us += busy;
    if (busy > s_rx_max_busy_us) {
        s_rx_max_busy_us = busy;
    }
    rx_stats_tick(now);
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
            /* Any half-received message is now unrecoverable; the
             * server will resend from scratch after reconnect. */
            hux_ws_reassembler_reset(&s_reassembler);
            post_app(HUX_APP_EV_NET_WS_DISCONNECTED);
            break;
        case WEBSOCKET_EVENT_DATA:
            forward_ws_text(ev);
            break;
        case WEBSOCKET_EVENT_ERROR:
            ESP_LOGE(TAG, "ws.error");
            break;
        case WEBSOCKET_EVENT_CLOSED:
            /* The server closed it with a CLOSE frame (eviction, shutdown, a
             * missed keepalive ping). The library reconnects by itself
             * (enable_close_reconnect) but emits no DISCONNECTED: post it,
             * or the app stays READY on a dead socket. */
            ESP_LOGW(TAG, "ws.closed by_server eviction=%d", s_close_was_eviction);
            hux_ws_reassembler_reset(&s_reassembler);
            post_app(HUX_APP_EV_NET_WS_DISCONNECTED);
            if (s_close_was_eviction) {
                post_app(HUX_APP_EV_NET_WS_EVICTED);
            }
            s_close_was_eviction = false;
            break;
        case WEBSOCKET_EVENT_FINISH:
            /* The client's task ended: nothing reads or reconnects now. Only
             * hux_net_pause() should get here; anything else is reported so
             * the app restarts it. Before enable_close_reconnect, every
             * server close ended here, silently (F-0015). */
            ESP_LOGW(TAG, "ws.finished: the client task exited");
            post_app(HUX_APP_EV_NET_WS_FINISHED);
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
        /* Used as `timeout_ms` for `esp_transport_poll_write` inside
         * the library. Default (10s) is too short under bidirectional
         * audio: a transient TCP-window stall trips the timeout and
         * (pre-v1.5) closes the socket. With separate TX/RX locks
         * (v1.5+, enabled via CONFIG_ESP_WS_CLIENT_SEPARATE_TX_LOCK)
         * 30s gives the library + lwIP comfortable headroom. Per
         * Espressif maintainer guidance in
         * espressif/esp-protocols issue #964. */
        .network_timeout_ms = 30000,

        /* TCP-level keep-alive — detect dead connections fast so the
         * library can fire `WEBSOCKET_EVENT_DISCONNECTED` and trigger
         * the auto-reconnect path. Without this, a NAT/router that
         * silently dropped the conntrack entry produces a zombie
         * socket where every write times out but no disconnect
         * fires (firmware/docs/triage.md F-0013).
         *
         * Tightened in v0.3.2 from 5/5/3 (~20s) to 3/2/3 (~9s) after
         * empirically observing the WS wedge recover 20-something
         * seconds after onset — long enough that user-facing audio
         * has clearly broken. 9s is right at the edge of "user
         * notices" without being so aggressive that a transient
         * AP/STA hiccup trips a false reconnect. */
        .keep_alive_enable = true,
        .keep_alive_idle = 3,
        .keep_alive_interval = 2,
        .keep_alive_count = 3,

        /* WS-level application PING. Cuts through application-layer
         * stalls (e.g. server hung but TCP healthy). 10s default is
         * fine; making it explicit so a future tweak doesn't have
         * to read the library source to know it. With
         * `disable_pingpong_discon=false` (default), missing PONG
         * for `pingpong_timeout_sec` (default 120s) auto-closes the
         * socket — combines with auto-reconnect to clear zombies. */
        .ping_interval_sec = 10,

        /* Reconnect after the server closes the socket too, not only after
         * a network error. Off by default: the client's task then exits on
         * any CLOSE frame and the device sits "connected" to nothing until
         * it is power-cycled (seen on the bench, F-0015). */
        .enable_close_reconnect = true,

        /* This task runs the whole inbound data plane (reassembly, cJSON,
         * base64, the speaker ring) and logs from it. The library default
         * (4 KB) had 1.2 KB left before the first reply arrived. */
        .task_stack = 6144,
    };
    s_ws = esp_websocket_client_init(&cfg);
    configASSERT(s_ws != NULL);
    ESP_ERROR_CHECK(esp_websocket_register_events(
        s_ws, WEBSOCKET_EVENT_ANY, ws_event_handler, NULL));
    ESP_LOGI(TAG, "ws.init uri=%s (starts on the first IP)", s_server_uri);
}

/* ------------------------------------------------------------------ */
/*  Public API                                                         */
/* ------------------------------------------------------------------ */

void hux_net_start(const hux_net_config_t *cfg) {
    configASSERT(cfg != NULL && cfg->wifi_ssid != NULL &&
                 cfg->wifi_password != NULL && cfg->server_uri != NULL);
    hux_ws_reassembler_init(&s_reassembler, s_frag_buf, sizeof(s_frag_buf));
    /* WS client created first; the GOT_IP handler starts it. */
    ws_start(cfg->server_uri);
    wifi_init_sta(cfg->wifi_ssid, cfg->wifi_password);
}

void hux_net_pause(void) {
    if (s_ws == NULL || !s_ws_started) {
        return;
    }
    /* From the app task (never the WS task: stop waits for it to exit). */
    esp_err_t err = esp_websocket_client_stop(s_ws);
    s_ws_started = false;
    ESP_LOGI(TAG, "ws.paused err=%s", esp_err_to_name(err));
}

void hux_net_resume(void) {
    if (s_ws == NULL) {
        return;
    }
    if (s_ws_started) {
        /* The task ended on its own (FINISH): stop() just cleans up. */
        esp_websocket_client_stop(s_ws);
    }
    esp_err_t err = esp_websocket_client_start(s_ws);
    s_ws_started = err == ESP_OK;
    ESP_LOGI(TAG, "ws.resumed err=%s", esp_err_to_name(err));
}

uint32_t hux_net_rx_age_ms(void) {
    uint32_t last = atomic_load_explicit(&s_rx_last_ms, memory_order_relaxed);
    return last == 0 ? UINT32_MAX : now_ms() - last;
}

int hux_net_rssi(void) {
    wifi_ap_record_t ap;
    return esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
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

void hux_net_set_audio_clear_sink(hux_net_audio_clear_fn sink) {
    atomic_store_explicit(&s_audio_clear_sink, sink, memory_order_release);
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

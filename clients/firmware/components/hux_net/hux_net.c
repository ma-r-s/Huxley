#include "hux_net.h"

#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_attr.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "nvs.h"

#include "hux_app.h"
#include "hux_link.h"
#include "hux_log.h"
#include "hux_type_probe.h"
#include "hux_wifi_retry.h"
#include "hux_opus.h"
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
/* How long a send may wait for room in the socket. esp_websocket_client
 * tears the connection down when a send can't start within its timeout
 * (esp_websocket_client.c, `wlen == 0` -> abort_connection), so this is
 * "how long without progress before the link counts as dead", not "how
 * slow is too slow". At 200 ms (until F-0022) any Wi-Fi slowdown during a
 * question killed the connection and the question with it. A slow link
 * still frees room every so often; 5 s with none is a dead one. Callers
 * (mic sender, log drain, app control messages) are their own tasks, never
 * the WebSocket task, so the receive side keeps playing meanwhile. */
#define WS_SEND_TIMEOUT_MS 5000
/* The library's PONG (and its own ping) wait this long for the tx lock a
 * stalled send holds; shorter, and a server ping during a stall aborts the
 * connection (F-0022 review). */
#if CONFIG_ESP_WS_CLIENT_TX_LOCK_TIMEOUT_MS <= WS_SEND_TIMEOUT_MS
#error "CONFIG_ESP_WS_CLIENT_TX_LOCK_TIMEOUT_MS must exceed WS_SEND_TIMEOUT_MS (sdkconfig older than sdkconfig.defaults?)"
#endif

static EventGroupHandle_t s_wifi_events = NULL;
static esp_websocket_client_handle_t s_ws = NULL;
static uint32_t s_wifi_retries = 0;   /* attempts since the last IP */
static esp_timer_handle_t s_wifi_retry_timer = NULL;
/* The WS client starts on the first IP, not at boot: started earlier its
 * first connect fails ("Host is unreachable", logged as an error on every
 * boot) and the next try waits out the 2 s reconnect timer. Only the
 * system event task touches s_ws_first_start; later IPs (Wi-Fi came back)
 * leave the client alone, or a Wi-Fi blip would undo an eviction pause.
 * Every later start or stop is the app task's (hux_net_pause / resume).
 *
 * s_ws_alive: the client's task exists. Set by a successful start, cleared
 * by its own FINISH (dispatched by the task just before it exits, so it is
 * clear before stop() returns). A FINISH reaching the app late can't
 * restart a client that is already running again. */
static _Atomic bool s_ws_first_start = false;
static _Atomic bool s_ws_alive = false;
/* The last CLOSE frame the server sent (WS task): an eviction ("Replaced
 * by new client", 1001) is told apart from a restart or a ping timeout. */
static bool s_close_was_eviction = false;
static char s_server_uri[128] = {0};
/* Where a switch reverts to: the last address a hello proved (the saved
 * one, else the compiled one). */
static char s_home_uri[128] = {0};
/* secrets.h's address, compiled into every image: the fallback when the
 * saved one stops answering (remote-operation W5, F-0027). */
static char s_compiled_uri[128] = {0};
/* NVS: the address `server keep` switched to, saved only once a hello
 * succeeded on it, so a typo can never strand the walkie. */
#define SERVER_NVS_NS  "hux_net"
#define SERVER_NVS_KEY "srv_uri"
/* No hello this long on the saved address after a boot: try the compiled
 * one, then back, until one answers. 5 min, off the ladder's 10 min Wi-Fi
 * step (both at once wastes the first one's attempt). */
#define SERVER_FALLBACK_S (5 * 60)
typedef enum {
    SRV_HOME,     /* on the proven address: nothing pending */
    SRV_TEMP,     /* `server <uri> [min]`: tests; reverts by time, never saved */
    SRV_KEEP,     /* `server keep <uri>`: saved at the first hello, else reverts */
    SRV_FALLBACK, /* watching the saved address after boot; no hello and it
                   * alternates with the compiled one ("watch" in the log) */
} srv_mode_t;
static _Atomic int s_srv_mode = SRV_HOME;
/* What the library connects to: the server URI plus `audio=opus` (F-0024,
 * the client's opt-in; the server's hello confirms it). */
static char s_ws_uri[160] = {0};
/* The server confirmed Opus in its hello: audio_clear then goes through
 * hux_opus's queue, in order with the binary packets before it. */
static _Atomic bool s_audio_opus = false;

static const char *ws_uri_for(const char *base) {
    if (!hux_opus_ready()) {
        return base; /* no codec: never ask for Opus (F-0024 review) */
    }
    snprintf(s_ws_uri, sizeof(s_ws_uri), "%s%saudio=opus", base, strchr(base, '?') ? "&" : "?");
    return s_ws_uri;
}
/* The link lock: one stop/start of the client at a time (a server move,
 * its revert, the ladder, a pause or resume, a hello proving an address).
 * The stop a switch does ends the client's task, whose FINISH would make
 * the app resume it on the old URI; resume waits for the lock instead. */
static _Atomic bool s_link_busy = false;
/* The first IP's start, asked for while the lock was held: the holder runs
 * it on its way out (the event task never waits on the lock). */
static _Atomic bool s_start_pending = false;
static esp_timer_handle_t s_server_revert;
static void server_revert_due(void *arg);
/* Generations, under the lock: a switch or a proving hello bumps
 * s_srv_gen, so a revert timer armed before it (s_revert_gen) is stale. A
 * hello proves an address only if its connection opened after the last
 * switch (s_conn_gen, bumped by the WS task at each CONNECTED). */
static uint32_t s_srv_gen = 0;
static uint32_t s_revert_gen = 0;
static _Atomic uint32_t s_conn_gen = 0;
static uint32_t s_switch_conn_gen = 0;
/* A hello arrived on s_home_uri this boot. A revert to an unproven home
 * watches it instead of trusting it. */
static bool s_home_proven = false;

/* The ladder (remote-operation W5, F-0027): nobody at the home can
 * restart the walkie, so without the server it restarts the WebSocket
 * (2 min), Wi-Fi (10), then itself (60). The server counts as reached
 * at a hello, lost at any disconnect; a deliberate pause (another client
 * took the server) stops the clock. */
static _Atomic bool s_server_up = false;
static _Atomic bool s_paused = false;
static _Atomic uint32_t s_lost_ms = 0;
static _Atomic int s_ladder_done = HUX_LADDER_NONE;
static _Atomic uint32_t s_ws_attempts = 0;
#define LADDER_TICK_MS 5000
static void ladder_task(void *arg);
/* The supervisor watches the ladder's ticks (it blocks in a stop if the
 * WS task is stuck). Any WS event (the client pings every 10 s, so pongs
 * keep coming) stamps s_ws_event_ms: 90 s without one while the server is
 * up means the WS task is stuck, which counts as lost (F-0027). */
static _Atomic uint32_t s_ladder_ticks = 0;
static _Atomic uint32_t s_ws_event_ms = 0;
#define WS_SILENT_MS 90000

/* device.health (F-0027): Wi-Fi disconnect reasons and TLS error codes
 * with their counts, and WebSocket connects, since the last take. */
#define HEALTH_SLOTS 8
static portMUX_TYPE s_health_lock = portMUX_INITIALIZER_UNLOCKED;
static hux_net_count_t s_h_wifi[HEALTH_SLOTS];
static hux_net_count_t s_h_tls[HEALTH_SLOTS];
static uint32_t s_h_connects = 0;

static void health_count(hux_net_count_t *slots, int code) {
    taskENTER_CRITICAL(&s_health_lock);
    for (int i = 0; i < HEALTH_SLOTS; i++) {
        if (slots[i].count == 0 || slots[i].code == code) {
            slots[i].code = code;
            slots[i].count++;
            break; /* full: an eighth distinct code a minute isn't counted */
        }
    }
    taskEXIT_CRITICAL(&s_health_lock);
}

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

/* Waits instead of skipping, so a revert can't be lost to a ladder step
 * that happened to run at the same moment. Never from the WS task (a stop
 * waits for it) or the event task (the ladder holds it around
 * esp_wifi_stop). */
static void link_lock(void) {
    while (atomic_exchange(&s_link_busy, true)) {
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

static bool link_lock_timed(uint32_t ms) {
    for (uint32_t waited = 0; atomic_exchange(&s_link_busy, true); waited += 50) {
        if (waited >= ms) {
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    return true;
}

/* Lock held. A running client is not paused, whoever started it. */
static void client_start(const char *why) {
    esp_err_t err = esp_websocket_client_start(s_ws);
    if (err == ESP_OK) {
        atomic_store(&s_ws_alive, true);
        atomic_store(&s_ws_first_start, true);
        atomic_store(&s_paused, false);
    }
    ESP_LOGI(TAG, "ws.start why=%s err=%s uri=%s", why, esp_err_to_name(err), s_server_uri);
}

/* Lock held. FINISH clears s_ws_alive on the task's way out; clear it
 * here too, so nothing reads a stale true after stop() returns. */
static void client_stop(void) {
    esp_websocket_client_stop(s_ws);
    atomic_store(&s_ws_alive, false);
}

static void link_unlock(void) {
    for (;;) {
        if (atomic_exchange(&s_start_pending, false) && !atomic_load(&s_ws_alive) &&
            !atomic_load(&s_paused)) {
            client_start("first_ip");
        }
        atomic_store(&s_link_busy, false);
        /* A start asked for while we released: take the lock back for it. */
        if (!atomic_load(&s_start_pending) || atomic_exchange(&s_link_busy, true)) {
            return;
        }
    }
}

/* An outage logs its first attempts and every tenth, not each one: the
 * lines wait in a 256-line queue until the server is back, and an hour of
 * them would push out the ladder's own. */
static bool link_loud(uint32_t attempt) {
    return attempt <= 3 || attempt % 10 == 0;
}

/* Any disconnect: start the ladder's clock (once per outage) and back
 * the next attempt off (2 s doubling to 60 s, jittered): a long outage
 * must not cost a TLS handshake every 2 s. From the WS task: the
 * library reads the delay live, without a lock. */
static void link_lost(const char *why) {
    if (atomic_exchange(&s_server_up, false)) {
        atomic_store(&s_lost_ms, now_ms());
        atomic_store(&s_ladder_done, HUX_LADDER_NONE);
        ESP_LOGW(TAG, "ladder.lost why=%s", why);
    }
    uint32_t attempt = atomic_fetch_add(&s_ws_attempts, 1) + 1;
    uint32_t ms = hux_link_backoff_ms(attempt, esp_random());
    if (s_ws != NULL) {
        esp_websocket_client_set_reconnect_timeout(s_ws, (int)ms);
    }
    if (link_loud(attempt)) {
        ESP_LOGW(TAG, "ws.backoff why=%s attempt=%u next_in_ms=%u", why, (unsigned)attempt,
                 (unsigned)ms);
    }
}

static void rx_stats_tick(uint32_t now) {
    if (now - s_rx_window_start_ms < RX_STATS_WINDOW_MS) {
        return;
    }
    if (s_rx_audio_msgs > 0) {
        ESP_LOGI(TAG, "ws.rx.stats window_ms=%u events=%u bytes=%u msgs=%u audio_msgs=%u "
                      "audio_ms=%u max_gap_ms=%u busy_ms=%u max_busy_ms=%u rssi=%d opus=%d dec_us=%u "
                      "dec_q=%u dec_err=%u dec_stack=%u",
                 (unsigned)(now - s_rx_window_start_ms), (unsigned)s_rx_events,
                 (unsigned)s_rx_bytes, (unsigned)s_rx_msgs, (unsigned)s_rx_audio_msgs,
                 (unsigned)(s_rx_audio_bytes / 48), (unsigned)s_rx_max_gap_ms,
                 (unsigned)(s_rx_busy_us / 1000), (unsigned)(s_rx_max_busy_us / 1000),
                 hux_net_rssi(), atomic_load(&s_audio_opus), (unsigned)hux_opus_take_dec_us(),
                 (unsigned)hux_opus_rx_waiting(), (unsigned)hux_opus_dec_errors(),
                 (unsigned)hux_opus_dec_stack_free());
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

/* An AP's channel width from its secondary channel: 20 MHz has none. */
static const char *second_chan_name(wifi_second_chan_t second) {
    switch (second) {
        case WIFI_SECOND_CHAN_ABOVE: return "above";
        case WIFI_SECOND_CHAN_BELOW: return "below";
        default:                     return "none";
    }
}

static int bandwidth_mhz(wifi_bandwidth_t bw) {
    return bw == WIFI_BW_HT40 ? 40 : 20;
}

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        /* 20 MHz, not 40: on 2.4 GHz a 40 MHz link spans most of the band
         * and shares its air with everything on both halves (here five of
         * our own mesh nodes on channel 9, 2026-10-12). The audio needs
         * ~0.5 Mbit/s. A/B on the board, radio streaming 3 x 2.5 min per
         * width: no difference while the network was calm (starved 31 vs
         * 25 s of ~478 s); standard advice for a crowded 2.4 GHz band, no
         * measured cost. `wifibw 40` tries the other until a restart. */
        esp_err_t bw = esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20);
        if (bw != ESP_OK) {
            ESP_LOGW(TAG, "wifi.bandwidth default 20 MHz failed err=%s", esp_err_to_name(bw));
        }
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_CONNECTED) {
        /* Which AP (a mesh has several with one SSID) and how well. */
        const wifi_event_sta_connected_t *c = (const wifi_event_sta_connected_t *)data;
        /* The AP's width (second: none = 20 MHz, above/below = 40) and
         * ours: on a crowded 2.4 GHz band a 40 MHz link shares its air
         * with the neighbours on both halves. */
        wifi_ap_record_t ap = {0};
        bool have_ap = esp_wifi_sta_get_ap_info(&ap) == ESP_OK;
        wifi_bandwidth_t bw = WIFI_BW_HT20;
        esp_wifi_get_bandwidth(WIFI_IF_STA, &bw);
        ESP_LOGI(TAG,
                 "wifi.associated bssid=%02x:%02x:%02x:%02x:%02x:%02x channel=%d second=%s "
                 "phy=%s bw_mhz=%d rssi=%d",
                 c->bssid[0], c->bssid[1], c->bssid[2], c->bssid[3], c->bssid[4], c->bssid[5],
                 c->channel, have_ap ? second_chan_name(ap.second) : "?",
                 !have_ap ? "?" : ap.phy_11ax ? "ax" : ap.phy_11n ? "n" : ap.phy_11g ? "g" : "b",
                 bandwidth_mhz(bw), hux_net_rssi());
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        /* Retry forever (hux_wifi_retry): a few at once, then backing off
         * to every 30 s. The reason code says why (201 no AP found, 15 /
         * 204 wrong password, 8 we left). */
        const wifi_event_sta_disconnected_t *d = (const wifi_event_sta_disconnected_t *)data;
        s_wifi_retries++;
        health_count(s_h_wifi, d != NULL ? (int)d->reason : -1);
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
        if (atomic_load(&s_ws_first_start) && !atomic_load(&s_server_up)) {
            /* A new network deserves a fast try, not the outage's minute. */
            atomic_store(&s_ws_attempts, 0);
            esp_websocket_client_set_reconnect_timeout(s_ws, HUX_LINK_BACKOFF_MIN_MS);
            ESP_LOGI(TAG, "ws.retry_fast why=new_ip");
        }
        if (!atomic_load(&s_ws_first_start) && s_ws != NULL) {
            /* From here on esp_websocket_client reconnects on its own. Never
             * wait for the lock here: ask, and whoever holds it (or we, if
             * nobody does) runs the start on the way out. */
            atomic_store(&s_start_pending, true);
            if (!atomic_exchange(&s_link_busy, true)) {
                link_unlock();
            } else {
                ESP_LOGI(TAG, "ws.start_deferred why=link_busy");
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
    wifi_bandwidth_t bw = WIFI_BW_HT20;
    esp_wifi_get_bandwidth(WIFI_IF_STA, &bw);
    ESP_LOGI(TAG, "wifi.init ssid=\"%s\" ps=NONE bw_mhz=%d", ssid, bandwidth_mhz(bw));
}

#define WIFI_SCAN_MAX_APS 24

void hux_net_wifi_scan(void) {
    /* Every AP the board hears, from where it sits: which neighbours
     * share its channel, and how loud they are. Blocking, ~2 s; the link
     * leaves its channel for a moment per channel scanned. */
    wifi_scan_config_t sc = {.show_hidden = true};
    esp_err_t err = esp_wifi_scan_start(&sc, true);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "wifi.scan failed err=%s", esp_err_to_name(err));
        return;
    }
    uint16_t found = 0;
    esp_wifi_scan_get_ap_num(&found);
    uint16_t n = found < WIFI_SCAN_MAX_APS ? found : WIFI_SCAN_MAX_APS;
    wifi_ap_record_t *aps = calloc(n > 0 ? n : 1, sizeof(*aps));
    if (aps == NULL) {
        esp_wifi_clear_ap_list();
        ESP_LOGW(TAG, "wifi.scan failed err=no_mem found=%u", (unsigned)found);
        return;
    }
    esp_wifi_scan_get_ap_records(&n, aps); /* frees the driver's whole list */
    ESP_LOGI(TAG, "wifi.scan found=%u shown=%u", (unsigned)found, (unsigned)n);
    for (uint16_t i = 0; i < n; i++) {
        const wifi_ap_record_t *a = &aps[i];
        ESP_LOGI(TAG,
                 "wifi.scan.ap bssid=%02x:%02x:%02x:%02x:%02x:%02x channel=%d second=%s "
                 "rssi=%d ssid=\"%.32s\"",
                 a->bssid[0], a->bssid[1], a->bssid[2], a->bssid[3], a->bssid[4], a->bssid[5],
                 a->primary, second_chan_name(a->second), a->rssi, (const char *)a->ssid);
    }
    free(aps);
}

esp_err_t hux_net_set_bandwidth(int mhz) {
    /* Takes effect on the next association, so leave the AP: the
     * disconnect handler rejoins at once (reason 8, retry 1). Not kept
     * across restarts. */
    wifi_bandwidth_t bw = mhz == 40 ? WIFI_BW_HT40 : WIFI_BW_HT20;
    esp_err_t err = esp_wifi_set_bandwidth(WIFI_IF_STA, bw);
    ESP_LOGI(TAG, "wifi.bandwidth set_mhz=%d err=%s", bandwidth_mhz(bw), esp_err_to_name(err));
    if (err == ESP_OK) {
        /* Let the developer door's "ok" out first: without it the reply
         * was lost, the tool retried, and every retry rejoined again
         * (2 minutes of reconnects per switch, 2026-10-12 A/B). */
        vTaskDelay(pdMS_TO_TICKS(300));
        esp_wifi_disconnect();
    }
    return err;
}

/* ------------------------------------------------------------------ */
/*  WebSocket                                                          */
/* ------------------------------------------------------------------ */

/* Full audio path. Parses the envelope, extracts `data`, decodes
 * base64 into `s_audio_scratch`, hands the view to the registered
 * sink. Runs on the WS client task; the sink must not block. */
static void dispatch_audio(const char *json, size_t len) {
    if (atomic_load(&s_audio_opus)) {
        /* Once Opus is agreed all audio is binary: the decoder task is the
         * ring's only writer (a second writer corrupts a stream buffer). */
        static uint32_t dropped;
        if ((dropped++ % 50) == 0) {
            ESP_LOGW(TAG, "ws.rx.audio_json_in_opus_mode dropped=%u", (unsigned)dropped);
        }
        return;
    }
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
        bool opus = atomic_load(&s_audio_opus);
        ESP_LOGI(TAG, "ws.rx.audio_clear%s", opus ? " (behind the queued opus)" : "");
        if (opus) {
            hux_opus_rx_clear(); /* after the packets before it, as sent */
            return;
        }
        hux_net_audio_clear_fn clear =
            atomic_load_explicit(&s_audio_clear_sink, memory_order_acquire);
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
            /* One Opus packet per binary message (F-0024). Small (~250 B),
             * never split across events in practice; a split one is
             * dropped rather than decoded in pieces. */
            uint32_t now = (uint32_t)(esp_timer_get_time() / 1000);
            uint32_t last = atomic_load_explicit(&s_rx_last_ms, memory_order_relaxed);
            if (last != 0 && now - last > s_rx_max_gap_ms) {
                s_rx_max_gap_ms = now - last;
            }
            atomic_store_explicit(&s_rx_last_ms, now, memory_order_relaxed);
            s_rx_events++;
            s_rx_bytes += (uint32_t)data->data_len;
            if (data->payload_offset == 0 && data->data_len == data->payload_len) {
                s_rx_msgs++;
                s_rx_audio_msgs++;
                s_rx_audio_bytes += HUX_OPUS_FRAME_SAMPLES * 2; /* as PCM: audio_ms stays true */
                int64_t t0 = esp_timer_get_time();
                hux_opus_rx_packet((const uint8_t *)data->data_ptr, (size_t)data->data_len);
                uint32_t busy = (uint32_t)(esp_timer_get_time() - t0); /* a full queue waits */
                s_rx_busy_us += busy;
                if (busy > s_rx_max_busy_us) {
                    s_rx_max_busy_us = busy;
                }
            } else {
                ESP_LOGW(TAG, "ws.rx.binary_split off=%d len=%d/%d (dropped)",
                         data->payload_offset, data->data_len, data->payload_len);
            }
            rx_stats_tick(now);
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
    atomic_store_explicit(&s_ws_event_ms, now_ms(), memory_order_relaxed);
    switch (id) {
        case WEBSOCKET_EVENT_CONNECTED:
            /* A new connection: its hello decides the format again, and
             * nothing queued from the last one may play (the library can
             * close without DISCONNECTED: CLOSED, FINISH). */
            atomic_store(&s_audio_opus, false);
            hux_opus_rx_reset();
            atomic_fetch_add(&s_conn_gen, 1);
            taskENTER_CRITICAL(&s_health_lock);
            s_h_connects++;
            taskEXIT_CRITICAL(&s_health_lock);
            ESP_LOGI(TAG, "ws.connected uri=%s", s_server_uri);
            post_app(HUX_APP_EV_NET_WS_CONNECTED);
            break;
        case WEBSOCKET_EVENT_DISCONNECTED:
            link_lost("disconnected"); /* logs ws.backoff why=disconnected */
            atomic_store(&s_audio_opus, false); /* the next hello decides again */
            hux_opus_rx_reset();
            /* Any half-received message is now unrecoverable; the
             * server will resend from scratch after reconnect. */
            hux_ws_reassembler_reset(&s_reassembler);
            post_app(HUX_APP_EV_NET_WS_DISCONNECTED);
            break;
        case WEBSOCKET_EVENT_DATA:
            forward_ws_text(ev);
            break;
        case WEBSOCKET_EVENT_ERROR:
            if (ev != NULL && ev->error_handle.esp_tls_last_esp_err != 0) {
                health_count(s_h_tls, (int)ev->error_handle.esp_tls_last_esp_err);
            }
            if (link_loud(atomic_load(&s_ws_attempts) + 1)) {
                ESP_LOGE(TAG, "ws.error attempt=%u", (unsigned)(atomic_load(&s_ws_attempts) + 1));
            }
            break;
        case WEBSOCKET_EVENT_CLOSED:
            /* The server closed it with a CLOSE frame (eviction, shutdown, a
             * missed keepalive ping). The library reconnects by itself
             * (enable_close_reconnect) but emits no DISCONNECTED: post it,
             * or the app stays READY on a dead socket. */
            ESP_LOGW(TAG, "ws.closed by_server eviction=%d", s_close_was_eviction);
            link_lost("closed");
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
            ESP_LOGW(TAG, "ws.finished why=client_task_exited");
            link_lost("finished");
            atomic_store(&s_ws_alive, false);
            post_app(HUX_APP_EV_NET_WS_FINISHED);
            break;
        default:
            break;
    }
}

static bool ws_start(const char *uri) {
    strlcpy(s_server_uri, uri, sizeof(s_server_uri));
    esp_websocket_client_config_t cfg = {
        .uri = ws_uri_for(s_server_uri),
        .buffer_size = WS_BUFFER_SIZE,
        .reconnect_timeout_ms = WS_RECONNECT_MS,
        /* The library's own waits: the TCP connect, ping/pong/close
         * writes, and reading the rest of a frame it started (idle waits
         * for the next frame are a separate 1 s poll). 30 s (per
         * espressif/esp-protocols #964, from before our sends had their
         * own timeout) left the walkie deaf for 18 s when Wi-Fi dropped
         * mid-reply: the task sat in a half-read frame on a dead socket
         * (bench.py --wifi offline, F-0022). 5 s, the same rule as
         * WS_SEND_TIMEOUT_MS: no progress for 5 s on a LAN is a dead link;
         * a 16 KB audio frame takes 0.5 s even at 40% of real time. */
        .network_timeout_ms = 5000,

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
    if (s_ws == NULL) {
        ESP_LOGE(TAG, "ws.init_failed uri=%s", s_server_uri);
        return false;
    }
    ESP_ERROR_CHECK(esp_websocket_register_events(
        s_ws, WEBSOCKET_EVENT_ANY, ws_event_handler, NULL));
    ESP_LOGI(TAG, "ws.init uri=%s (starts on the first IP)", s_server_uri);
    return true;
}

/* ------------------------------------------------------------------ */
/*  Public API                                                         */
/* ------------------------------------------------------------------ */

/* NVS's saved address, or "" (none, unreadable, or not one we may use:
 * it is erased then, so it can't fail every boot). */
static void server_load_saved(char *out, size_t cap) {
    out[0] = '\0';
    nvs_handle_t h;
    if (nvs_open(SERVER_NVS_NS, NVS_READONLY, &h) != ESP_OK) {
        return;
    }
    size_t len = cap;
    esp_err_t err = nvs_get_str(h, SERVER_NVS_KEY, out, &len);
    nvs_close(h);
    if (err != ESP_OK) {
        if (err != ESP_ERR_NVS_NOT_FOUND) {
            ESP_LOGW(TAG, "ws.server_nvs_read err=%s", esp_err_to_name(err));
        }
        out[0] = '\0';
    }
}

static void server_save(const char *uri);

void hux_net_start(const hux_net_config_t *cfg) {
    configASSERT(cfg != NULL && cfg->wifi_ssid != NULL &&
                 cfg->wifi_password != NULL && cfg->server_uri != NULL);
    hux_ws_reassembler_init(&s_reassembler, s_frag_buf, sizeof(s_frag_buf));
    /* WS client created first; the GOT_IP handler starts it. */
    strlcpy(s_compiled_uri, cfg->server_uri, sizeof(s_compiled_uri));
    strlcpy(s_home_uri, cfg->server_uri, sizeof(s_home_uri));
    char saved[sizeof(s_home_uri)];
    server_load_saved(saved, sizeof(saved));
    if (saved[0] != '\0' && !hux_link_uri_ok(saved, sizeof(s_home_uri))) {
        ESP_LOGE(TAG, "ws.server_saved_invalid uri=%s: erased", saved);
        server_save(NULL);
        saved[0] = '\0';
    }
    if (saved[0] != '\0' && strcmp(saved, s_compiled_uri) != 0) {
        strlcpy(s_home_uri, saved, sizeof(s_home_uri));
    }
    bool from_nvs = strcmp(s_home_uri, s_compiled_uri) != 0;
    ESP_LOGI(TAG, "ws.server boot=%s source=%s", s_home_uri, from_nvs ? "nvs" : "compiled");
    if (!ws_start(s_home_uri)) {
        /* Never let saved data stop the boot: back to the compiled one. */
        configASSERT(from_nvs);
        ESP_LOGE(TAG, "ws.server_saved_unusable uri=%s: erased, booting on %s", s_home_uri,
                 s_compiled_uri);
        server_save(NULL);
        strlcpy(s_home_uri, s_compiled_uri, sizeof(s_home_uri));
        from_nvs = false;
        configASSERT(ws_start(s_home_uri));
    }
    const esp_timer_create_args_t args = {.callback = server_revert_due, .name = "ws_revert"};
    ESP_ERROR_CHECK(esp_timer_create(&args, &s_server_revert));
    if (from_nvs) {
        /* The saved address must prove itself again: if it never answers,
         * the compiled one gets its turn. */
        atomic_store(&s_srv_mode, SRV_FALLBACK);
        s_revert_gen = s_srv_gen;
        esp_timer_start_once(s_server_revert, (uint64_t)SERVER_FALLBACK_S * 1000000);
    }
    atomic_store(&s_lost_ms, now_ms()); /* no server yet: the ladder's clock runs */
    if (xTaskCreate(ladder_task, "hux_ladder", 4096, NULL, 3, NULL) != pdPASS) {
        ESP_LOGE(TAG, "ladder.task_create_failed: no escalation this boot");
    }
    wifi_init_sta(cfg->wifi_ssid, cfg->wifi_password);
}

void hux_net_pause(void) {
    if (s_ws == NULL) {
        return;
    }
    /* From the app task. Deliberate: the ladder waits until the next press
     * resumes. */
    link_lock();
    if (atomic_load(&s_ws_alive)) {
        atomic_store(&s_paused, true);
        client_stop();
        ESP_LOGI(TAG, "ws.paused");
    }
    link_unlock();
}

void hux_net_resume(void) {
    if (s_ws == NULL) {
        return;
    }
    link_lock();
    if (!atomic_load(&s_ws_first_start)) {
        ESP_LOGI(TAG, "ws.resume skipped why=no_ip_yet"); /* the first IP starts it */
    } else if (atomic_load(&s_ws_alive)) {
        ESP_LOGI(TAG, "ws.resume skipped why=running");
    } else {
        if (atomic_load(&s_paused) && !atomic_load(&s_server_up)) {
            atomic_store(&s_lost_ms, now_ms()); /* the clock starts again */
            atomic_store(&s_ladder_done, HUX_LADDER_NONE);
        }
        client_start("resume");
    }
    link_unlock();
}

static void server_save(const char *uri) {
    nvs_handle_t h;
    esp_err_t err = nvs_open(SERVER_NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = uri != NULL ? nvs_set_str(h, SERVER_NVS_KEY, uri) : nvs_erase_key(h, SERVER_NVS_KEY);
        if (err == ESP_ERR_NVS_NOT_FOUND) {
            err = ESP_OK; /* erasing what isn't there */
        }
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    ESP_LOGW(TAG, "ws.server_saved uri=%s err=%s", uri != NULL ? uri : "(compiled)",
             esp_err_to_name(err));
}

/* Lock held. Points the client at `uri` (restarting it if it ran) and arms
 * the revert. False, with nothing changed, if the library refuses it. */
static bool server_switch_locked(const char *uri, uint32_t revert_s, srv_mode_t mode) {
    bool was_running = atomic_load(&s_ws_alive);
    if (was_running) {
        client_stop();
    }
    char old[sizeof(s_server_uri)];
    strlcpy(old, s_server_uri, sizeof(old));
    strlcpy(s_server_uri, uri, sizeof(s_server_uri));
    esp_err_t err = esp_websocket_client_set_uri(s_ws, ws_uri_for(s_server_uri));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ws.server_refused uri=%s why=set_uri err=%s", uri, esp_err_to_name(err));
        strlcpy(s_server_uri, old, sizeof(s_server_uri));
        esp_websocket_client_set_uri(s_ws, ws_uri_for(s_server_uri));
        if (was_running) {
            client_start("switch_undone");
        }
        return false;
    }
    atomic_store(&s_srv_mode, mode);
    s_srv_gen++;
    s_switch_conn_gen = atomic_load(&s_conn_gen); /* a hello must come after this */
    ESP_LOGW(TAG, "ws.server uri=%s was=%s mode=%d revert_in_s=%u", uri, old, (int)mode,
             (unsigned)revert_s);
    if (was_running) {
        client_start("switch");
    }
    esp_timer_stop(s_server_revert); /* not running is fine */
    /* A watch runs its timer whichever address it's on. */
    if (revert_s > 0 && (mode == SRV_FALLBACK || strcmp(s_server_uri, s_home_uri) != 0)) {
        s_revert_gen = s_srv_gen;
        esp_timer_start_once(s_server_revert, (uint64_t)revert_s * 1000000);
    }
    return true;
}

static void server_switch(const char *uri, uint32_t revert_s, srv_mode_t mode) {
    if (s_ws == NULL) {
        return;
    }
    if (!hux_link_uri_ok(uri, sizeof(s_server_uri))) {
        ESP_LOGE(TAG, "ws.server_refused uri=%s why=invalid (ws[s]://host:port/path)",
                 uri != NULL ? uri : "(null)");
        return;
    }
    link_lock();
    server_switch_locked(uri, revert_s, mode);
    link_unlock();
}

static void server_revert_task(void *arg) {
    (void)arg;
    link_lock();
    int mode = atomic_load(&s_srv_mode);
    if (s_revert_gen != s_srv_gen || mode == SRV_HOME) {
        /* A hello or another switch got here first. */
        ESP_LOGI(TAG, "ws.server_revert skipped why=stale mode=%d", mode);
    } else if (mode == SRV_FALLBACK) {
        /* No hello on either address yet: try the other one. */
        const char *next = strcmp(s_server_uri, s_compiled_uri) == 0 ? s_home_uri : s_compiled_uri;
        char to[sizeof(s_server_uri)];
        strlcpy(to, next, sizeof(to));
        ESP_LOGW(TAG, "ws.server fallback to=%s no_hello_s=%u", to, (unsigned)SERVER_FALLBACK_S);
        server_switch_locked(to, SERVER_FALLBACK_S, SRV_FALLBACK);
    } else {
        /* A temporary or kept switch timed out: home, watched if no hello
         * proved it this boot. */
        char to[sizeof(s_server_uri)];
        strlcpy(to, s_home_uri, sizeof(to));
        ESP_LOGW(TAG, "ws.server_revert to=%s why=timeout home_proven=%d", to, s_home_proven);
        server_switch_locked(to, s_home_proven ? 0 : SERVER_FALLBACK_S,
                             s_home_proven ? SRV_HOME : SRV_FALLBACK);
    }
    link_unlock();
    vTaskDelete(NULL);
}

static void server_revert_due(void *arg) {
    (void)arg;
    /* Not on the esp_timer task: the stop a switch does waits for the WS
     * task. If the task can't be made, try again in 10 s. */
    if (xTaskCreate(server_revert_task, "ws_revert", 4096, NULL, 3, NULL) != pdPASS) {
        ESP_LOGE(TAG, "ws.server_revert task_create_failed: retry in 10 s");
        esp_timer_start_once(s_server_revert, 10 * 1000000);
    }
}

void hux_net_set_server(const char *uri, uint32_t revert_s) {
    server_switch(uri, revert_s, revert_s > 0 ? SRV_TEMP : SRV_HOME);
}

void hux_net_server_keep(const char *uri, uint32_t revert_s) {
    server_switch(uri, revert_s > 0 ? revert_s : 15 * 60, SRV_KEEP);
}

void hux_net_server_reset(void) {
    if (s_ws == NULL) {
        return;
    }
    link_lock();
    server_save(NULL);
    strlcpy(s_home_uri, s_compiled_uri, sizeof(s_home_uri));
    s_home_proven = false;
    char to[sizeof(s_server_uri)];
    strlcpy(to, s_compiled_uri, sizeof(to));
    server_switch_locked(to, 0, SRV_HOME);
    link_unlock();
}

void hux_net_hello_ok(void) {
    if (s_ws == NULL) {
        return;
    }
    link_lock();
    if (!esp_websocket_client_is_connected(s_ws) ||
        atomic_load(&s_conn_gen) == s_switch_conn_gen) {
        /* Queued from a connection a switch has since closed: it proves
         * nothing about the address we're on now. */
        ESP_LOGW(TAG, "ws.hello_stale uri=%s", s_server_uri);
        link_unlock();
        return;
    }
    atomic_store(&s_ws_attempts, 0);
    if (!atomic_exchange(&s_server_up, true)) {
        ESP_LOGI(TAG, "ladder.found lost_s=%u steps=%s",
                 (unsigned)((now_ms() - atomic_load(&s_lost_ms)) / 1000),
                 hux_ladder_name((hux_ladder_step_t)atomic_load(&s_ladder_done)));
    }
    int mode = atomic_load(&s_srv_mode);
    if (mode == SRV_KEEP) {
        server_save(s_server_uri); /* proven: home now, across reboots */
    }
    if (mode == SRV_KEEP || mode == SRV_FALLBACK) {
        /* A watch that landed on the compiled address makes it home until
         * the next boot, which tries the saved one again. */
        strlcpy(s_home_uri, s_server_uri, sizeof(s_home_uri));
        esp_timer_stop(s_server_revert);
        s_srv_gen++; /* a revert already on its way is stale */
        atomic_store(&s_srv_mode, SRV_HOME);
        ESP_LOGI(TAG, "ws.server_proven uri=%s mode=%s source=%s", s_server_uri,
                 mode == SRV_KEEP ? "keep" : "watch",
                 strcmp(s_server_uri, s_compiled_uri) == 0 ? "compiled" : "nvs");
    }
    if (strcmp(s_server_uri, s_home_uri) == 0) {
        s_home_proven = true;
    }
    link_unlock();
}

void hux_net_server_status(void) {
    static const char *const MODES[] = {"home", "temp", "keep", "watch"};
    link_lock();
    ESP_LOGI(TAG, "ws.server now=%s home=%s compiled=%s mode=%s home_proven=%d", s_server_uri,
             s_home_uri, s_compiled_uri, MODES[atomic_load(&s_srv_mode) & 3], s_home_proven);
    link_unlock();
}

static void ladder_task(void *arg) {
    (void)arg;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(LADDER_TICK_MS));
        atomic_fetch_add(&s_ladder_ticks, 1);
        if (atomic_load(&s_server_up) &&
            now_ms() - atomic_load_explicit(&s_ws_event_ms, memory_order_relaxed) > WS_SILENT_MS) {
            ESP_LOGE(TAG, "ws.silent ms=%u: the WS task is stuck or the link dead",
                     (unsigned)(now_ms() - atomic_load(&s_ws_event_ms)));
            link_lost("silent");
        }
        if (atomic_load(&s_server_up) || atomic_load(&s_paused) || s_ws == NULL) {
            continue;
        }
        uint32_t lost_s = (now_ms() - atomic_load(&s_lost_ms)) / 1000;
        hux_ladder_step_t step =
            hux_ladder_due(lost_s, (hux_ladder_step_t)atomic_load(&s_ladder_done));
        if (step == HUX_LADDER_NONE) {
            continue;
        }
        if (step == HUX_LADDER_REBOOT) {
            /* An hour without the server: whatever is stuck, a restart
             * clears it. No lock: a hung holder must not stop the last
             * resort. (An image still pending its OTA confirm goes back to
             * the other slot.) */
            ESP_LOGE(TAG, "ladder.step step=reboot lost_s=%u uri=%s", (unsigned)lost_s,
                     s_server_uri);
            vTaskDelay(pdMS_TO_TICKS(500)); /* the line reaches serial and the black box */
            esp_restart();
        }
        if (!link_lock_timed(10000)) {
            ESP_LOGW(TAG, "ladder.lock_busy step=%s lost_s=%u", hux_ladder_name(step),
                     (unsigned)lost_s);
            continue; /* the next tick tries again; the reboot needs no lock */
        }
        atomic_store(&s_ladder_done, step);
        ESP_LOGW(TAG, "ladder.step step=%s lost_s=%u uri=%s", hux_ladder_name(step),
                 (unsigned)lost_s, s_server_uri);
        if (step == HUX_LADDER_WS) {
            if (!atomic_load(&s_ws_first_start)) {
                ESP_LOGI(TAG, "ladder.ws_skipped why=no_ip_yet"); /* Wi-Fi's step is next */
            } else {
                if (atomic_load(&s_ws_alive)) {
                    client_stop();
                }
                client_start("ladder");
            }
        } else {
            /* A full stop and start: a fresh association and DHCP lease
             * (a stuck AP, a login page that handed out an IP). STA_START
             * reconnects; the WebSocket follows the new IP. A pending
             * retry would call connect in the middle of it. */
            esp_timer_stop(s_wifi_retry_timer);
            s_wifi_retries = 0;
            esp_err_t stop = esp_wifi_stop();
            esp_err_t start = esp_wifi_start();
            ESP_LOGI(TAG, "ladder.wifi_restarted stop=%s start=%s", esp_err_to_name(stop),
                     esp_err_to_name(start));
        }
        link_unlock();
    }
}

void hux_net_take_health(hux_net_health_t *out) {
    taskENTER_CRITICAL(&s_health_lock);
    memcpy(out->wifi_disconnects, s_h_wifi, sizeof(s_h_wifi));
    memcpy(out->tls_errors, s_h_tls, sizeof(s_h_tls));
    out->connects = s_h_connects;
    memset(s_h_wifi, 0, sizeof(s_h_wifi));
    memset(s_h_tls, 0, sizeof(s_h_tls));
    s_h_connects = 0;
    taskEXIT_CRITICAL(&s_health_lock);
    wifi_ap_record_t ap = {0};
    out->have_ap = esp_wifi_sta_get_ap_info(&ap) == ESP_OK;
    out->rssi = out->have_ap ? ap.rssi : 0;
    out->channel = out->have_ap ? ap.primary : 0;
    memcpy(out->bssid, ap.bssid, sizeof(out->bssid));
    out->ws_rx_age_ms = hux_net_rx_age_ms();
}

uint32_t hux_net_ladder_ticks(void) {
    return atomic_load(&s_ladder_ticks);
}

bool hux_net_is_running(void) {
    return atomic_load(&s_ws_alive);
}

uint32_t hux_net_rx_age_ms(void) {
    uint32_t last = atomic_load_explicit(&s_rx_last_ms, memory_order_relaxed);
    return last == 0 ? UINT32_MAX : now_ms() - last;
}

int hux_net_rssi(void) {
    wifi_ap_record_t ap;
    return esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}

void hux_net_set_audio_opus(bool on) {
    atomic_store(&s_audio_opus, on);
}

bool hux_net_audio_opus(void) {
    return atomic_load(&s_audio_opus);
}

bool hux_net_send_binary(const uint8_t *data, size_t len) {
    if (s_ws == NULL || !esp_websocket_client_is_connected(s_ws)) {
        return false;
    }
    int sent = esp_websocket_client_send_bin(s_ws, (const char *)data, (int)len,
                                             pdMS_TO_TICKS(WS_SEND_TIMEOUT_MS));
    if (sent != (int)len) {
        ESP_LOGW(TAG, "ws.send.incomplete binary requested=%u sent=%d", (unsigned)len, sent);
        return false;
    }
    return true;
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

bool hux_net_send_log(const hux_log_entry_t *entry) {
    if (entry == NULL) {
        return true;
    }
    if (s_ws == NULL || !esp_websocket_client_is_connected(s_ws)) {
        return false; /* offline: hux_log keeps it and retries */
    }

    /* cJSON handles quoting / escaping of anything weird in the log
     * line (quotes, backslashes, control chars). Hand-rolled JSON
     * formatting here would silently corrupt messages that contain
     * those. The alloc cost is tolerable on the drain task — it runs
     * at low priority and only fires for WARN+ lines. */
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        return true; /* out of memory: retrying won't help */
    }
    cJSON *data = cJSON_CreateObject();
    if (data == NULL) {
        cJSON_Delete(root);
        return true;
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
        return true;
    }

    bool sent = hux_net_send_text(json, strlen(json));
    cJSON_free(json);
    return sent;
}

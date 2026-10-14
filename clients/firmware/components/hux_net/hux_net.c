#include "hux_net.h"

#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_attr.h"
#include "esp_crt_bundle.h"
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
#include "hux_wifi_list.h"
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
/* The server closed the connection (a CLOSE frame): an update's accept
 * wants one followed by a reconnect (F-0028). */
static _Atomic uint32_t s_server_closes = 0;
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
static TaskHandle_t s_ladder_task = NULL;
/* A new IP while the server is lost: restart the client now. The library
 * sleeps half its retry delay between checks (up to 30 s after a long
 * outage), so lowering the delay alone came 25 s late (2026-10-12). */
static _Atomic bool s_kick = false;
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
/* Server audio received, ever, in ms (the hello's received_ms_last_conn). */
static _Atomic uint32_t s_rx_audio_total_ms = 0;
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

/* ------------------------------------------------------------------ */
/*  The Wi-Fi list (F-0027): compiled networks + up to 4 added on site */
/* ------------------------------------------------------------------ */

#define WIFI_NVS_NS        "hux_wifi"
#define WIFI_NVS_GOOD      "good"
#define WIFI_RESCAN_MS     (2 * 60 * 1000)  /* no IP this long: look for a known network heard */
#define WIFI_ROTATE_MS     (10 * 60 * 1000) /* an IP but no server this long: the next one heard */
#define WIFI_ADD_IP_MS     30000            /* a network being added gets this long for an IP */
#define WIFI_ADD_SERVER_MS 60000            /* then this long to reach the server (reported only) */
#define WIFI_AUTH_FAILS    3                /* in a row: a wrong password (one can be weak signal) */
#define WIFI_HEARD_MAX     40

typedef struct {
    char ssid[33];
    char pass[65];
} wifi_saved_t;

static hux_wifi_net_t s_nets[HUX_WIFI_MAX];
static int s_nnets = 0;
static int s_cur = -1;      /* the network joined (index into s_nets) */
static char s_good[33];     /* the one that last reached the server (NVS) */
/* Since when there is no IP: the last join, or the moment an IP was lost. */
static _Atomic uint32_t s_noip_ms = 0;
static uint32_t s_rotate_ms = 0;
/* The last hello (an add's "server reached" must come after its IP). */
static _Atomic uint32_t s_hello_ms = 0;
/* F-0032: the last time the server was steadily up (2 min on one connection), or boot. */
static _Atomic uint32_t s_stable_ms = 0;
/* Saved entries an update has since compiled in: kept in NVS untouched (a
 * rollback to an older image still finds them), not joined twice. */
static wifi_saved_t s_shadow[HUX_WIFI_SAVED_MAX];
static int s_nshadow = 0;
static _Atomic bool s_have_ip = false;
/* A scan or a join under way: the disconnect handler doesn't retry. */
static _Atomic bool s_wifi_hold = false;
/* An add under way (`wifi addhex`, USB only): the network on trial, the one
 * to go back to, and what the driver said meanwhile. */
static _Atomic bool s_add_on = false;
static struct {
    int idx;
    int prev;
    uint32_t since_ms;
    bool saved;
} s_add;
static _Atomic uint32_t s_add_ip_ms = 0;
static _Atomic int s_add_auth_fails = 0;
static _Atomic int s_add_last_reason = 0;

static int wifi_find(const char *ssid) {
    for (int i = 0; i < s_nnets; i++) {
        if (strcmp(s_nets[i].ssid, ssid) == 0) {
            return i;
        }
    }
    return -1;
}

static const char *net_source(int i) {
    return i >= 0 && i < s_nnets && s_nets[i].compiled ? "compiled" : "saved";
}

static void wifi_add_compiled(const char *ssid, const char *pass) {
    if (ssid == NULL || ssid[0] == '\0' || pass == NULL || s_nnets >= HUX_WIFI_COMPILED_MAX ||
        wifi_find(ssid) >= 0) {
        return;
    }
    hux_wifi_net_t *n = &s_nets[s_nnets++];
    strlcpy(n->ssid, ssid, sizeof(n->ssid));
    strlcpy(n->pass, pass, sizeof(n->pass));
    n->compiled = true;
}

static void wifi_list_load(const hux_net_config_t *cfg) {
    s_nnets = 0;
    wifi_add_compiled(cfg->wifi_ssid, cfg->wifi_password);
    wifi_add_compiled(cfg->wifi2_ssid, cfg->wifi2_password);
    wifi_add_compiled(cfg->wifi3_ssid, cfg->wifi3_password);
    int compiled = s_nnets;
    s_good[0] = '\0';
    nvs_handle_t h;
    if (nvs_open(WIFI_NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        for (int k = 0; k < HUX_WIFI_SAVED_MAX && s_nnets < HUX_WIFI_MAX; k++) {
            char key[4];
            snprintf(key, sizeof(key), "w%d", k);
            wifi_saved_t w;
            size_t len = sizeof(w);
            if (nvs_get_blob(h, key, &w, &len) == ESP_OK && len == sizeof(w) &&
                memchr(w.ssid, 0, sizeof(w.ssid)) != NULL &&
                memchr(w.pass, 0, sizeof(w.pass)) != NULL && w.ssid[0] != '\0') {
                if (wifi_find(w.ssid) >= 0) {
                    s_shadow[s_nshadow++] = w; /* compiled in since: keep, don't join twice */
                } else {
                    hux_wifi_net_t *n = &s_nets[s_nnets++];
                    memcpy(n->ssid, w.ssid, sizeof(n->ssid));
                    memcpy(n->pass, w.pass, sizeof(n->pass));
                    n->compiled = false;
                }
            }
            memset(&w, 0, sizeof(w));
        }
        size_t len = sizeof(s_good);
        if (nvs_get_str(h, WIFI_NVS_GOOD, s_good, &len) != ESP_OK) {
            s_good[0] = '\0';
        }
        nvs_close(h);
    }
    ESP_LOGI(TAG, "wifi.list compiled=%d saved=%d shadowed=%d good=\"%s\"", compiled,
             s_nnets - compiled, s_nshadow, s_good);
}

/* The saved entries, rewritten as w0..w3 (a few blobs, only on a change). */
static bool wifi_list_save(void) {
    nvs_handle_t h;
    esp_err_t err = nvs_open(WIFI_NVS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "wifi.save_failed err=%s", esp_err_to_name(err));
        return false;
    }
    int k = 0;
    for (int i = 0; i < s_nnets && err == ESP_OK; i++) {
        if (s_nets[i].compiled || k >= HUX_WIFI_SAVED_MAX) {
            continue;
        }
        char key[4];
        snprintf(key, sizeof(key), "w%d", k++);
        wifi_saved_t w = {0};
        memcpy(w.ssid, s_nets[i].ssid, sizeof(w.ssid));
        memcpy(w.pass, s_nets[i].pass, sizeof(w.pass));
        err = nvs_set_blob(h, key, &w, sizeof(w));
        memset(&w, 0, sizeof(w));
    }
    for (int i = 0; i < s_nshadow && k < HUX_WIFI_SAVED_MAX && err == ESP_OK; i++) {
        char key[4];
        snprintf(key, sizeof(key), "w%d", k++);
        err = nvs_set_blob(h, key, &s_shadow[i], sizeof(s_shadow[i]));
    }
    for (; k < HUX_WIFI_SAVED_MAX && err == ESP_OK; k++) {
        char key[4];
        snprintf(key, sizeof(key), "w%d", k);
        esp_err_t e = nvs_erase_key(h, key);
        if (e != ESP_OK && e != ESP_ERR_NVS_NOT_FOUND) {
            err = e;
        }
    }
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "wifi.save_failed err=%s", esp_err_to_name(err));
    }
    return err == ESP_OK;
}

static void wifi_set_good(const char *ssid) {
    if (strcmp(s_good, ssid) == 0) {
        return; /* written only when it changes */
    }
    strlcpy(s_good, ssid, sizeof(s_good));
    nvs_handle_t h;
    esp_err_t err = nvs_open(WIFI_NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = ssid[0] != '\0' ? nvs_set_str(h, WIFI_NVS_GOOD, ssid) : nvs_erase_key(h, WIFI_NVS_GOOD);
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    ESP_LOGW(TAG, "wifi.good ssid=\"%s\" saved=%s", ssid, esp_err_to_name(err));
}

static bool wifi_apply_config(int idx) {
    wifi_config_t wc = {0};
    memcpy(wc.sta.ssid, s_nets[idx].ssid, strnlen(s_nets[idx].ssid, sizeof(wc.sta.ssid)));
    memcpy(wc.sta.password, s_nets[idx].pass,
           strnlen(s_nets[idx].pass, sizeof(wc.sta.password)));
    wc.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK; /* WPA2 or better */
    wc.sta.pmf_cfg.capable = true;
    wc.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH; /* WPA3-only networks too */
    /* Join the strongest AP with this SSID. The default fast scan joins the
     * first one it hears: on a mesh (several APs, one SSID) the bench board
     * landed at -26, -60 and -87 dBm on three boots a metre from the same
     * node. A full scan costs about a second. */
    wc.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    wc.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, &wc);
    memset(&wc, 0, sizeof(wc));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "wifi.config_failed ssid=\"%s\" err=%s", s_nets[idx].ssid,
                 esp_err_to_name(err));
    }
    return err == ESP_OK;
}

/* Leave whatever network, join `idx`: a full stop and start (a fresh
 * association and DHCP lease, as the ladder's Wi-Fi step always did).
 * STA_START connects. The caller holds the link lock. */
static bool wifi_join_locked(int idx, const char *why) {
    atomic_store(&s_wifi_hold, true);
    esp_timer_stop(s_wifi_retry_timer);
    s_wifi_retries = 0;
    esp_err_t stop = esp_wifi_stop();
    /* Refused: the driver keeps the previous network's config, and so does
     * s_cur (an add then fails with why=config). */
    bool ok = wifi_apply_config(idx);
    if (ok) {
        s_cur = idx;
    }
    atomic_store(&s_noip_ms, now_ms());
    atomic_store(&s_have_ip, false);
    atomic_store(&s_wifi_hold, false);
    esp_err_t start = esp_wifi_start();
    ESP_LOGW(TAG, "wifi.join ssid=\"%s\" source=%s why=%s config=%d stop=%s start=%s",
             s_nets[idx].ssid, net_source(idx), why, ok, esp_err_to_name(stop),
             esp_err_to_name(start));
    return ok;
}

/* A blocking scan (~2 s). The driver refuses one while a connect is under
 * way (ESP_ERR_WIFI_STATE), and a disconnect only asks it to stop: wait
 * and ask again, up to 3 s (F-0027: the field test, 2026-10-14). */
static esp_err_t scan_start_waiting(bool stop_connecting) {
    wifi_scan_config_t sc = {.show_hidden = true};
    esp_err_t err = ESP_FAIL;
    for (int attempt = 1; attempt <= 10; attempt++) {
        err = esp_wifi_scan_start(&sc, true);
        if (err != ESP_ERR_WIFI_STATE) {
            break;
        }
        ESP_LOGW(TAG, "wifi.scan busy attempt=%d: a connect is winding down", attempt);
        if (stop_connecting) {
            esp_wifi_disconnect();
        }
        vTaskDelay(pdMS_TO_TICKS(300));
    }
    return err;
}

/* What is heard from here (blocking, ~2 s). A connect in progress makes a
 * scan fail, so without an IP the attempt is stopped first; the caller
 * then joins or connects again. The caller holds the link lock. */
static int wifi_scan_locked(hux_wifi_heard_t *heard, int cap) {
    atomic_store(&s_wifi_hold, true);
    esp_timer_stop(s_wifi_retry_timer);
    bool no_ip = !atomic_load(&s_have_ip);
    if (no_ip) {
        esp_wifi_disconnect();
    }
    esp_err_t err = scan_start_waiting(no_ip);
    int nh = 0;
    if (err == ESP_OK) {
        uint16_t found = 0;
        esp_wifi_scan_get_ap_num(&found);
        uint16_t n = found < WIFI_HEARD_MAX ? found : WIFI_HEARD_MAX;
        wifi_ap_record_t *aps = calloc(n > 0 ? n : 1, sizeof(*aps));
        if (aps != NULL) {
            esp_wifi_scan_get_ap_records(&n, aps);
            for (uint16_t i = 0; i < n && nh < cap; i++) {
                memcpy(heard[nh].ssid, aps[i].ssid, 32);
                heard[nh].ssid[32] = '\0';
                heard[nh].rssi = aps[i].rssi;
                nh++;
            }
            free(aps);
        } else {
            esp_wifi_clear_ap_list();
        }
    } else {
        ESP_LOGW(TAG, "wifi.scan failed err=%s", esp_err_to_name(err));
    }
    atomic_store(&s_wifi_hold, false);
    return err == ESP_OK ? nh : -1;
}

/* Scan, rank, and either move to `pick(order)` or stay. Caller holds the
 * link lock. `next`: the candidate after the current one (rotation);
 * otherwise the best. Returns whether it joined. */
static bool wifi_choose_locked(bool next, const char *why) {
    static hux_wifi_heard_t heard[WIFI_HEARD_MAX]; /* link lock: one user */
    int nh = wifi_scan_locked(heard, WIFI_HEARD_MAX);
    int order[HUX_WIFI_MAX];
    int n = hux_wifi_rank(s_nets, s_nnets, s_good, heard, nh > 0 ? nh : 0, order, HUX_WIFI_MAX);
    int pick = n > 0 ? order[0] : s_cur;
    if (pick < 0) {
        /* None heard and none current (the active one was forgotten). */
        pick = s_good[0] != '\0' && wifi_find(s_good) >= 0 ? wifi_find(s_good) : 0;
    }
    if (next && n > 0) {
        for (int i = 0; i < n; i++) {
            if (order[i] == s_cur) {
                pick = order[(i + 1) % n];
                break;
            }
        }
    }
    ESP_LOGW(TAG, "wifi.choose why=%s heard=%d candidates=%d current=\"%s\" pick=\"%s\"", why, nh, n,
             s_cur >= 0 ? s_nets[s_cur].ssid : "", pick >= 0 ? s_nets[pick].ssid : "");
    if (pick != s_cur || (next && n > 0)) {
        /* `next` with one heard: the same one afresh. None heard: stay. */
        return wifi_join_locked(pick, why);
    }
    if (!atomic_load(&s_have_ip)) {
        atomic_store(&s_noip_ms, now_ms()); /* staying: the next look in 2 min */
        esp_wifi_connect();
    }
    return false;
}

/* The add under way, once per ladder tick: kept once it has an IP (the
 * server is reported, not required: the Pi or the tunnel may be down that
 * minute), dropped with the reason otherwise. */
static void wifi_add_tick(uint32_t now) {
    uint32_t ip_ms = atomic_load(&s_add_ip_ms);
    if (ip_ms != 0 && !s_add.saved) {
        if (!link_lock_timed(10000)) {
            return; /* the next tick */
        }
        bool ok = wifi_list_save();
        if (!ok) {
            ESP_LOGW(TAG, "wifi.add failed ssid=\"%s\" why=nvs: back to \"%s\"",
                     s_nets[s_add.idx].ssid, s_nets[s_add.prev].ssid);
            int prev = s_add.prev;
            memset(&s_nets[s_add.idx], 0, sizeof(s_nets[0]));
            s_nnets--;
            atomic_store(&s_add_on, false);
            wifi_join_locked(prev, "add_failed");
            link_unlock();
            return;
        }
        s_add.saved = true;
        if (!atomic_load(&s_server_up)) {
            atomic_store(&s_lost_ms, now); /* a fresh start for the ladder */
        }
        atomic_store(&s_ladder_done, HUX_LADDER_NONE);
        s_rotate_ms = now;
        ESP_LOGW(TAG, "wifi.add saved ssid=\"%s\" ip_after_ms=%u nvs=%d", s_nets[s_add.idx].ssid,
                 (unsigned)(ip_ms - s_add.since_ms), ok);
        link_unlock();
        return;
    }
    if (s_add.saved) {
        if (atomic_load(&s_server_up) && (int32_t)(atomic_load(&s_hello_ms) - ip_ms) > 0) {
            ESP_LOGW(TAG, "wifi.add server_reached ssid=\"%s\"", s_nets[s_add.idx].ssid);
            atomic_store(&s_add_on, false);
        } else if (now - ip_ms >= WIFI_ADD_SERVER_MS) {
            ESP_LOGW(TAG, "wifi.add no_server ssid=\"%s\": kept (it gives an IP); the server "
                          "didn't answer in %d s",
                     s_nets[s_add.idx].ssid, WIFI_ADD_SERVER_MS / 1000);
            atomic_store(&s_add_on, false);
        }
        return;
    }
    int fails = atomic_load(&s_add_auth_fails);
    int reason = atomic_load(&s_add_last_reason);
    const char *why = NULL;
    if (fails >= WIFI_AUTH_FAILS) {
        why = "wrong_password";
    } else if (reason == WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY ||
               reason == WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD) {
        why = "security_too_weak"; /* open or WPA-TKIP only */
    } else if (reason == WIFI_REASON_802_1X_AUTH_FAILED) {
        why = "enterprise_login";
    } else if (now - s_add.since_ms >= WIFI_ADD_IP_MS) {
        why = reason == WIFI_REASON_NO_AP_FOUND ? "not_heard" : fails > 0 ? "auth_failed" : "no_ip";
    }
    if (why == NULL || !link_lock_timed(10000)) {
        return;
    }
    ESP_LOGW(TAG, "wifi.add failed ssid=\"%s\" why=%s reason=%d auth_fails=%d: back to \"%s\"",
             s_nets[s_add.idx].ssid, why, reason, fails, s_nets[s_add.prev].ssid);
    int prev = s_add.prev;
    memset(&s_nets[s_add.idx], 0, sizeof(s_nets[0])); /* the trial entry is the last one */
    s_nnets--;
    atomic_store(&s_add_on, false);
    wifi_join_locked(prev, "add_failed");
    link_unlock();
}

/* Once per ladder tick (5 s): an add under way, no IP for 2 min (move to a
 * known network heard), an IP but no server since the ladder's Wi-Fi step
 * (the next one heard every 10 min). */
static void wifi_tick(void) {
    uint32_t now = now_ms();
    if (atomic_load(&s_add_on)) {
        wifi_add_tick(now);
        return;
    }
    if (!atomic_load(&s_have_ip) && s_nnets > 1 && now - atomic_load(&s_noip_ms) >= WIFI_RESCAN_MS &&
        link_lock_timed(10000)) {
        if (!atomic_load(&s_have_ip)) {
            wifi_choose_locked(true, "no_ip");
        }
        link_unlock();
    }
    if (atomic_load(&s_have_ip) && !atomic_load(&s_server_up) && !atomic_load(&s_paused) &&
        atomic_load(&s_ladder_done) >= HUX_LADDER_WIFI &&
        atomic_load(&s_ladder_done) != HUX_LADDER_REBOOT && now - s_rotate_ms >= WIFI_ROTATE_MS &&
        link_lock_timed(10000)) {
        s_rotate_ms = now;
        wifi_choose_locked(true, "no_server");
        link_unlock();
    }
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
        int reason = d != NULL ? (int)d->reason : -1;
        if (atomic_exchange(&s_have_ip, false)) {
            atomic_store(&s_noip_ms, now_ms()); /* the 2 min count from here */
        }
        if (atomic_load(&s_add_on)) {
            atomic_store(&s_add_last_reason, reason);
            if (reason == WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT || reason == WIFI_REASON_AUTH_FAIL ||
                reason == WIFI_REASON_HANDSHAKE_TIMEOUT) {
                atomic_fetch_add(&s_add_auth_fails, 1);
            } else if (reason != WIFI_REASON_ASSOC_LEAVE) {
                atomic_store(&s_add_auth_fails, 0); /* "three in a row" */
            }
        }
        if (atomic_load(&s_wifi_hold)) {
            /* A scan or a join under way: it connects when it's done. */
            ESP_LOGI(TAG, "wifi.disconnected reason=%d held", reason);
            post_app(HUX_APP_EV_NET_WIFI_DOWN);
            return;
        }
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
        atomic_store(&s_have_ip, true);
        if (atomic_load(&s_add_on) && s_cur == s_add.idx && atomic_load(&s_add_ip_ms) == 0) {
            atomic_store(&s_add_ip_ms, now_ms());
        }
        xEventGroupSetBits(s_wifi_events, WIFI_CONNECTED_BIT);
        post_app(HUX_APP_EV_NET_WIFI_UP);
        if (atomic_load(&s_ws_first_start) && !atomic_load(&s_server_up)) {
            /* A new network deserves a fast try, not the outage's minute. */
            atomic_store(&s_ws_attempts, 0);
            esp_websocket_client_set_reconnect_timeout(s_ws, HUX_LINK_BACKOFF_MIN_MS);
            ESP_LOGI(TAG, "ws.retry_fast why=new_ip");
            atomic_store(&s_kick, true);
            if (s_ladder_task != NULL) {
                xTaskNotifyGive(s_ladder_task); /* the ladder restarts it, not this task */
            }
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

static void wifi_init_sta(int idx) {
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

    /* The driver keeps no copy of any password in flash (`nvs.net80211` in
     * plain text otherwise): the list is the only store. */
    wifi_config_t stored = {0};
    if (esp_wifi_get_config(WIFI_IF_STA, &stored) == ESP_OK && stored.sta.ssid[0] != 0) {
        /* Left by an image before this one: erase it once. */
        esp_err_t r = esp_wifi_restore();
        ESP_LOGW(TAG, "wifi.flash_copy_erased err=%s", esp_err_to_name(r));
    }
    memset(&stored, 0, sizeof(stored));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    wifi_apply_config(idx);
    s_cur = idx;
    atomic_store(&s_noip_ms, now_ms());
    ESP_ERROR_CHECK(esp_wifi_start());

    /* Power save off: the radio stays awake (the walkie's biggest steady
     * draw; it runs on its cells now, so F-0029 measures a day of drain
     * and re-tests power save: the send timeout is 5 s today, not the
     * 200 ms that tripped below). History: the default `WIFI_PS_MIN_MODEM`
     * parks the radio between AP beacons
     * (typically 100 ms intervals) and the first packet after an
     * idle period waits for the next wake, blowing through the
     * 200 ms WS write timeout when two beacons stack unfavourably.
     * Symptom (firmware/docs/triage.md F-0013): after ~10 sec of
     * conversational silence, the next outbound mic frame trips
     * `transport_poll_write(0)` and the WS lock starves.
     * `WIFI_PS_NONE` keeps the radio always-awake — first-packet
     * latency drops from ~50-150 ms to <5 ms. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    wifi_bandwidth_t bw = WIFI_BW_HT20;
    esp_wifi_get_bandwidth(WIFI_IF_STA, &bw);
    ESP_LOGI(TAG, "wifi.init ssid=\"%s\" source=%s ps=NONE bw_mhz=%d", s_nets[idx].ssid,
             net_source(idx), bandwidth_mhz(bw));
}

#define WIFI_SCAN_MAX_APS 40

static const char *auth_name(wifi_auth_mode_t m) {
    switch (m) {
        case WIFI_AUTH_OPEN:            return "open";
        case WIFI_AUTH_WEP:             return "wep";
        case WIFI_AUTH_WPA_PSK:         return "wpa";
        case WIFI_AUTH_WPA2_PSK:        return "wpa2";
        case WIFI_AUTH_WPA_WPA2_PSK:    return "wpa_wpa2";
        case WIFI_AUTH_WPA2_ENTERPRISE: return "enterprise";
        case WIFI_AUTH_WPA3_PSK:        return "wpa3";
        case WIFI_AUTH_WPA2_WPA3_PSK:   return "wpa2_wpa3";
        default:                        return "other";
    }
}

void hux_net_wifi_scan(void) {
    /* Every AP the board hears, from where it sits: which neighbours
     * share its channel, and how loud they are. Blocking, ~2 s; the link
     * leaves its channel for a moment per channel scanned. */
    link_lock();
    atomic_store(&s_wifi_hold, true);
    esp_timer_stop(s_wifi_retry_timer);
    bool had_ip = atomic_load(&s_have_ip);
    if (!had_ip) {
        esp_wifi_disconnect(); /* a connect in progress makes a scan fail */
    }
    esp_err_t err = scan_start_waiting(!had_ip);
    atomic_store(&s_wifi_hold, false);
    if (!had_ip) {
        esp_wifi_connect();
    }
    link_unlock();
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
        char name[33];
        memcpy(name, a->ssid, 32);
        name[32] = '\0';
        ESP_LOGI(TAG,
                 "wifi.scan.ap bssid=%02x:%02x:%02x:%02x:%02x:%02x channel=%d second=%s "
                 "rssi=%d auth=%s known=%d ssid=\"%s\"",
                 a->bssid[0], a->bssid[1], a->bssid[2], a->bssid[3], a->bssid[4], a->bssid[5],
                 a->primary, second_chan_name(a->second), a->rssi, auth_name(a->authmode),
                 name[0] != '\0' && wifi_find(name) >= 0, name);
    }
    free(aps);
}

bool hux_net_wifi_add(const char *ssid, const char *pass) {
    size_t sl = strlen(ssid), pl = strlen(pass);
    ESP_LOGW(TAG, "wifi.add ssid=\"%s\" ssid_len=%u pass_len=%u", ssid, (unsigned)sl,
             (unsigned)pl); /* never the password */
    const char *refused = NULL;
    bool psk_hex = pl == 64;
    for (size_t i = 0; psk_hex && i < pl; i++) {
        psk_hex = (pass[i] >= '0' && pass[i] <= '9') || (pass[i] >= 'a' && pass[i] <= 'f') ||
                  (pass[i] >= 'A' && pass[i] <= 'F');
    }
    if (sl < 1 || sl > 32 || pl < 8 || pl > 64 || (pl == 64 && !psk_hex)) {
        refused = "lengths"; /* a name of 1-32 bytes, a passphrase of 8-63, or a 64-hex key */
    } else if (atomic_load(&s_add_on)) {
        refused = "busy";
    }
    if (refused == NULL) {
        link_lock();
        int saved = 0;
        for (int i = 0; i < s_nnets; i++) {
            saved += !s_nets[i].compiled;
        }
        if (wifi_find(ssid) >= 0) {
            refused = "known"; /* forget it first to change its password */
        } else if (saved >= HUX_WIFI_SAVED_MAX || s_nnets >= HUX_WIFI_MAX) {
            refused = "full";
        } else {
            hux_wifi_net_t *n = &s_nets[s_nnets];
            strlcpy(n->ssid, ssid, sizeof(n->ssid));
            strlcpy(n->pass, pass, sizeof(n->pass));
            n->compiled = false;
            s_add.idx = s_nnets++;
            s_add.prev = s_cur >= 0 ? s_cur : 0;
            s_add.since_ms = now_ms();
            s_add.saved = false;
            atomic_store(&s_add_ip_ms, 0);
            atomic_store(&s_add_auth_fails, 0);
            atomic_store(&s_add_last_reason, 0);
            atomic_store(&s_add_on, true);
            /* The ladder starts over: an hour of outage before the visit must
             * not reboot it in the middle of the add. */
            atomic_store(&s_ladder_done, HUX_LADDER_NONE);
            if (!atomic_load(&s_server_up)) {
                atomic_store(&s_lost_ms, now_ms());
            }
            s_rotate_ms = now_ms();
            if (atomic_load(&s_srv_mode) == SRV_FALLBACK) {
                /* The server watch starts over too: no address switch mid-add. */
                s_revert_gen = ++s_srv_gen;
                esp_timer_stop(s_server_revert);
                esp_timer_start_once(s_server_revert, (uint64_t)SERVER_FALLBACK_S * 1000000);
            }
            if (!wifi_join_locked(s_add.idx, "add")) {
                memset(&s_nets[s_add.idx], 0, sizeof(s_nets[0]));
                s_nnets--;
                atomic_store(&s_add_on, false);
                refused = "config";
            }
        }
        link_unlock();
    }
    if (refused != NULL) {
        ESP_LOGW(TAG, "wifi.add refused why=%s", refused);
        return false;
    }
    return true;
}

bool hux_net_wifi_forget(const char *ssid) {
    link_lock();
    int i = wifi_find(ssid);
    const char *refused = i < 0                       ? "not_known"
                          : s_nets[i].compiled        ? "compiled"
                          : atomic_load(&s_add_on)    ? "busy"
                                                      : NULL;
    if (refused != NULL) {
        link_unlock();
        ESP_LOGW(TAG, "wifi.forget refused ssid=\"%s\" why=%s", ssid, refused);
        return false;
    }
    bool active = i == s_cur;
    memmove(&s_nets[i], &s_nets[i + 1], (size_t)(s_nnets - i - 1) * sizeof(s_nets[0]));
    s_nnets--;
    memset(&s_nets[s_nnets], 0, sizeof(s_nets[0]));
    if (s_cur > i) {
        s_cur--;
    }
    if (strcmp(s_good, ssid) == 0) {
        wifi_set_good("");
    }
    bool ok = wifi_list_save();
    ESP_LOGW(TAG, "wifi.forget ssid=\"%s\" active=%d nvs=%d", ssid, active, ok);
    if (active) {
        s_cur = -1; /* whatever slid into its slot isn't what the driver runs */
        wifi_choose_locked(false, "forgot_active");
    }
    link_unlock();
    return ok;
}

bool hux_net_wifi_join(const char *ssid) {
    link_lock();
    int i = wifi_find(ssid);
    bool ok = i >= 0 && !atomic_load(&s_add_on) && wifi_join_locked(i, "bench");
    link_unlock();
    if (!ok) {
        ESP_LOGW(TAG, "wifi.join refused ssid=\"%s\" why=%s", ssid,
                 i < 0 ? "not_known" : "busy_or_config");
    }
    return ok;
}

void hux_net_wifi_list(void) {
    uint8_t mac[6] = {0};
    esp_wifi_get_mac(WIFI_IF_STA, mac);
    ESP_LOGI(TAG, "wifi.list mac=%02x:%02x:%02x:%02x:%02x:%02x networks=%d good=\"%s\" ip=%d",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], s_nnets, s_good,
             atomic_load(&s_have_ip));
    for (int i = 0; i < s_nnets; i++) {
        ESP_LOGI(TAG, "wifi.list.net ssid=\"%s\" source=%s good=%d active=%d", s_nets[i].ssid,
                 net_source(i), strcmp(s_nets[i].ssid, s_good) == 0, i == s_cur);
    }
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
                atomic_fetch_add(&s_rx_audio_total_ms, HUX_OPUS_FRAME_MS);
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
        case WEBSOCKET_EVENT_ERROR: {
            /* The library fills the TLS and socket fields only for a
             * transport error; for any other type they are its stack's
             * leftovers (the event struct isn't zeroed), so read them only
             * then. */
            bool transport =
                ev != NULL && ev->error_handle.error_type == WEBSOCKET_ERROR_TYPE_TCP_TRANSPORT;
            unsigned tls_err = transport ? (unsigned)ev->error_handle.esp_tls_last_esp_err : 0;
            if (tls_err != 0) {
                health_count(s_h_tls, (int)tls_err);
            }
            if (link_loud(atomic_load(&s_ws_attempts) + 1)) {
                /* Why, not only that: a home's filter, a tunnel down (an
                 * HTTP 530) and a DNS failure must read differently. */
                ESP_LOGE(TAG, "ws.error attempt=%u type=%d tls_err=0x%x tls_stack=0x%x sock_errno=%d "
                              "http_status=%d",
                         (unsigned)(atomic_load(&s_ws_attempts) + 1),
                         ev != NULL ? (int)ev->error_handle.error_type : -1, tls_err,
                         transport ? (unsigned)ev->error_handle.esp_tls_stack_err : 0,
                         transport ? ev->error_handle.esp_transport_sock_errno : 0,
                         ev != NULL ? ev->error_handle.esp_ws_handshake_status_code : 0);
            }
            break;
        }
        case WEBSOCKET_EVENT_CLOSED:
            /* The server closed it with a CLOSE frame (eviction, shutdown, a
             * missed keepalive ping). The library reconnects by itself
             * (enable_close_reconnect) but emits no DISCONNECTED: post it,
             * or the app stays READY on a dead socket. */
            atomic_fetch_add(&s_server_closes, 1);
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

/* "Authorization: Bearer <token>\r\n" for the upgrade (F-0028). */
static char s_auth_header[96] = {0};

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
         * stalls (e.g. server hung but TCP healthy). Every 20 s, not 10
         * (Mario, 2026-10-12: fewer wakeups; the server pings every 20 s
         * too, and a WS silent 90 s still counts as stuck). With
         * `disable_pingpong_discon=false` (default), missing PONG
         * for `pingpong_timeout_sec` (default 120s) auto-closes the
         * socket — combines with auto-reconnect to clear zombies. */
        .ping_interval_sec = 20,

        /* Reconnect after the server closes the socket too, not only after
         * a network error. Off by default: the client's task then exits on
         * any CLOSE frame and the device sits "connected" to nothing until
         * it is power-cycled (seen on the bench, F-0015). */
        .enable_close_reconnect = true,

        /* This task runs the whole inbound data plane (reassembly, cJSON,
         * base64, the speaker ring) and logs from it. The library default
         * (4 KB) had 1.2 KB left before the first reply arrived. */
        .task_stack = 6144,
        .headers = s_auth_header[0] != '\0' ? s_auth_header : NULL,
        /* wss:// (the Cloudflare tunnel, T1.43): the Mozilla CA bundle
         * checks the server's certificate. Unused on ws://. */
        .crt_bundle_attach = esp_crt_bundle_attach,
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
    if (cfg->device_token != NULL && cfg->device_token[0] != '\0') {
        int n = snprintf(s_auth_header, sizeof(s_auth_header), "Authorization: Bearer %s\r\n",
                         cfg->device_token);
        if (n <= 0 || n >= (int)sizeof(s_auth_header)) {
            s_auth_header[0] = '\0';
            ESP_LOGE(TAG, "ws.token_too_long: none sent");
        }
    }
    ESP_LOGI(TAG, "ws.token present=%d", s_auth_header[0] != '\0'); /* never the value */
    /* WS client created first; the GOT_IP handler starts it. */
    if (!hux_link_uri_complete(cfg->server_uri, s_compiled_uri, sizeof(s_compiled_uri))) {
        strlcpy(s_compiled_uri, cfg->server_uri, sizeof(s_compiled_uri));
    }
    strlcpy(s_home_uri, s_compiled_uri, sizeof(s_home_uri));
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
    atomic_store(&s_stable_ms, now_ms()); /* F-0032's clock, from boot */
    if (xTaskCreate(ladder_task, "hux_ladder", 4096, NULL, 3, &s_ladder_task) != pdPASS) {
        ESP_LOGE(TAG, "ladder.task_create_failed: no escalation this boot");
    }
    wifi_list_load(cfg);
    /* `good` directly (its own all-channel scan picks the mesh's strongest
     * AP); elsewhere, no IP in 2 min looks for another known network. */
    int first = s_good[0] != '\0' ? wifi_find(s_good) : -1;
    wifi_init_sta(first >= 0 ? first : 0);
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
    char full[sizeof(s_server_uri)];
    if (!hux_link_uri_complete(uri, full, sizeof(full)) || !hux_link_uri_ok(full, sizeof(full))) {
        ESP_LOGE(TAG, "ws.server_refused uri=%s why=invalid (ws[s]://host[:port]/path)",
                 uri != NULL ? uri : "(null)");
        return;
    }
    link_lock();
    server_switch_locked(full, revert_s, mode);
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
    atomic_store(&s_hello_ms, now_ms());
    if (s_cur >= 0) {
        wifi_set_good(s_nets[s_cur].ssid); /* reached the server through it */
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
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(LADDER_TICK_MS)); /* or a new IP */
        atomic_fetch_add(&s_ladder_ticks, 1);
        wifi_tick();
        if (atomic_exchange(&s_kick, false) && !atomic_load(&s_server_up) &&
            atomic_load(&s_ws_first_start) && !atomic_load(&s_paused) &&
            link_lock_timed(10000)) {
            if (atomic_load(&s_ws_alive)) {
                client_stop();
            }
            client_start("new_ip");
            link_unlock();
        }
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
        } else if (!atomic_load(&s_add_on)) {
            /* The next known network heard, or the same one afresh: a full
             * stop and start, a fresh association and DHCP lease (a stuck AP,
             * a login page that handed out an IP). Again every 10 min while
             * the server stays lost (wifi_tick). */
            s_rotate_ms = now_ms();
            wifi_choose_locked(true, "ladder");
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

bool hux_net_link_info(hux_net_link_t *out) {
    wifi_ap_record_t ap = {0};
    if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) {
        return false;
    }
    strlcpy(out->ssid, (const char *)ap.ssid, sizeof(out->ssid));
    out->channel = ap.primary;
    out->rssi = ap.rssi;
    memcpy(out->bssid, ap.bssid, sizeof(out->bssid));
    return true;
}

uint32_t hux_net_server_closes(void) {
    return atomic_load(&s_server_closes);
}

uint32_t hux_net_connects(void) {
    return atomic_load(&s_conn_gen);
}

void hux_net_server_uri(char *out, size_t cap) {
    link_lock();
    strlcpy(out, s_server_uri, cap);
    link_unlock();
}

const char *hux_net_device_token(void) {
    /* "Authorization: Bearer <token>\r\n" -> the token. */
    static char token[72];
    const char *p = strstr(s_auth_header, "Bearer ");
    if (p == NULL) {
        return NULL;
    }
    p += 7;
    size_t n = strcspn(p, "\r\n");
    if (n == 0 || n >= sizeof(token)) {
        return NULL;
    }
    memcpy(token, p, n);
    token[n] = '\0';
    return token;
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

bool hux_net_have_ip(void) {
    return atomic_load(&s_have_ip);
}

/* F-0032: the server counts as reachable only after 2 min on one connection
 * (a server that connects and crashes over and over still counts as lost),
 * counted from the last time it was. Paused on purpose (an eviction) or
 * switched for a test: not lost. */
#define STABLE_UP_MS (2u * 60u * 1000u)

uint32_t hux_net_server_lost_ms(void) {
    uint32_t now = now_ms();
    if (atomic_load(&s_paused) || atomic_load(&s_srv_mode) == SRV_TEMP) {
        atomic_store(&s_stable_ms, now);
        return 0;
    }
    uint32_t hello = atomic_load(&s_hello_ms);
    if (atomic_load(&s_server_up) && hello != 0 && now - hello >= STABLE_UP_MS) {
        atomic_store(&s_stable_ms, now);
        return 0;
    }
    uint32_t ms = now - atomic_load(&s_stable_ms);
    return ms > 0 ? ms : 1;
}

uint32_t hux_net_rx_audio_ms(void) {
    return atomic_load(&s_rx_audio_total_ms);
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

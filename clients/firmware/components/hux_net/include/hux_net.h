/**
 * hux_net — Wi-Fi STA + WebSocket client.
 *
 * Owns everything network: brings up Wi-Fi, maintains the single
 * outbound WebSocket connection to the Huxley server, and translates
 * both into events posted on `hux_app`'s queue. All state lives inside
 * this component; the rest of the firmware talks to it only through
 * this header.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#include "hux_log.h"

typedef struct {
    /* Up to 3 compiled networks (F-0027's Wi-Fi list): the first is the
     * default; the others NULL when not built in. */
    const char *wifi_ssid;
    const char *wifi_password;
    const char *wifi2_ssid;
    const char *wifi2_password;
    const char *wifi3_ssid;
    const char *wifi3_password;
    /** Full URI, e.g. "ws://192.168.68.83:8765/". */
    const char *server_uri;
    /** The device token (F-0028): `Authorization: Bearer` on the upgrade.
     * NULL or empty: none sent. Never logged. */
    const char *device_token;
} hux_net_config_t;

/**
 * Kick off Wi-Fi association and (once associated) WebSocket connection.
 * Non-blocking: events arrive on `hux_app`'s queue as they happen.
 * Call exactly once at boot, after `hux_app_start`.
 */
void hux_net_start(const hux_net_config_t *cfg);

/** Signal strength of the current AP in dBm, 0 when not associated. */
int hux_net_rssi(void);

/** Scan every channel and log each AP heard (`wifi.scan.ap` lines):
 *  channel, width, signal. Blocking, about 2 s. */
void hux_net_wifi_scan(void);

/** The Wi-Fi list (F-0027), from the USB console only. `add`: join it now,
 * keep it once it gives an IP (`wifi.add saved`, then `server_reached` or
 * `no_server`), else back to the previous network (`wifi.add failed
 * why=`). False if refused at once (`wifi.add refused why=`). The password
 * is never logged. */
bool hux_net_wifi_add(const char *ssid, const char *pass);
/** Forget a network added on site (never a compiled one). */
bool hux_net_wifi_forget(const char *ssid);
/** Log the list (names, which is good and active, never passwords) and
 * the MAC address. */
void hux_net_wifi_list(void);

/** Bench (`wifi join <ssid hex>`): join a known network now, heard or not.
 * On one it can't hear, no IP for 2 min makes it look for another (the
 * field case, F-0027). */
bool hux_net_wifi_join(const char *ssid);

/** Set the station's channel width (20 or 40 MHz) and rejoin the AP so
 *  it applies. Not kept across restarts. */
esp_err_t hux_net_set_bandwidth(int mhz);

/**
 * Stop the WebSocket client (no reconnects) / start it again. For an
 * eviction: another client took the server, and the walkie waits for its
 * talk button before taking it back. Call from the app task, never from
 * the WebSocket task. `resume` also restarts a client whose task ended;
 * it does nothing while the client is running.
 */
void hux_net_pause(void);
void hux_net_resume(void);

/** The server's hello confirmed Opus (F-0024): binary audio both ways,
 * audio_clear queued behind the packets. Set by the app on each hello;
 * cleared on disconnect. */
void hux_net_set_audio_opus(bool on);
bool hux_net_audio_opus(void);

/** One binary WebSocket message (an Opus packet). Same timeout rule as
 * hux_net_send_text. */
bool hux_net_send_binary(const uint8_t *data, size_t len);

/** Point the WebSocket at another server (tests: through bench.py's
 * proxy), until the next boot or `revert_s` seconds (0: no revert), so a
 * test that dies can't leave the walkie pointed at nothing. Reconnects
 * now. Not from the WebSocket task. */
void hux_net_set_server(const char *uri, uint32_t revert_s);

/** Switch to `uri` and keep it, across reboots, once a hello succeeds on
 * it (saved in NVS); no hello within `revert_s` (0: 15 min) and it goes
 * back to the last proven address. For moving the walkie to another
 * server remotely (remote-operation W5, F-0027). Not from the WS task. */
void hux_net_server_keep(const char *uri, uint32_t revert_s);

/** Forget the saved address and go back to the compiled one. */
void hux_net_server_reset(void);

/** How long ago the server closed the WebSocket for a restart (1012, or 1001
 * with no reason, a deploy's shutdown), or UINT32_MAX if not since the last
 * hello (F-0037: a press then says the walkie is updating). */
uint32_t hux_net_restart_close_age_ms(void);

/** Where the path to the server broke last (hux_link_fail_t: NONE, INTERNET
 * or SERVER), reset by each hello (F-0039). */
int hux_net_link_fail(void);

/** The app's hello arrived: a pending `server_keep` is saved; a fallback
 * stops alternating. */
void hux_net_hello_ok(void);

/** Log the current, proven and compiled addresses and the mode. */
void hux_net_server_status(void);

/** The ladder task's ticks (one per 5 s): the supervisor's view of it
 * (F-0027). */
uint32_t hux_net_ladder_ticks(void);

/** A code and how often it came (Wi-Fi disconnect reasons, TLS errors). */
typedef struct {
    int code;
    uint32_t count; /* 0: an empty slot */
} hux_net_count_t;

typedef struct {
    hux_net_count_t wifi_disconnects[8];
    hux_net_count_t tls_errors[8];
    uint32_t connects; /* WebSocket connections opened */
    bool have_ap;
    int rssi;
    int channel;
    uint8_t bssid[6];
    uint32_t ws_rx_age_ms;
} hux_net_health_t;

/** device.health (F-0027): the counts since the last call (then zeroed),
 * plus the link as it is now. */
void hux_net_take_health(hux_net_health_t *out);

typedef struct {
    char ssid[33];
    int channel;
    int rssi;
    uint8_t bssid[6];
} hux_net_link_t;

/** The access point now (for device.hello); false when not associated. */
bool hux_net_link_info(hux_net_link_t *out);

/** Times the server closed the connection, and connections opened, this
 * boot: an update's accept wants a close then a reconnect (F-0028). */
uint32_t hux_net_server_closes(void);
uint32_t hux_net_connects(void);

/** The server address in use (`ws[s]://host:port/...`), for resolving a
 * download path the server sent (F-0028). Not from the WS task. */
void hux_net_server_uri(char *out, size_t cap);

/** The device token sent on every upgrade, for the firmware downloads'
 * `Authorization: Bearer` (F-0028); NULL when none. Never log it. */
const char *hux_net_device_token(void);

/** The WebSocket client's task is running (it connects or reconnects on
 * its own). False after a pause, or if it ended unexpectedly. */
bool hux_net_is_running(void);

/** Milliseconds since the last inbound WebSocket data, UINT32_MAX if none. */
uint32_t hux_net_rx_age_ms(void);

/**
 * Send a text frame over the WebSocket. Returns true on success, false
 * if the WS isn't currently connected (caller's responsibility to back
 * off). Safe to call from any task.
 */
bool hux_net_send_text(const char *data, size_t len);

/**
 * Sink callback compatible with `hux_log_set_sink`. Builds a
 * `client_event` JSON envelope per docs/protocol.md:
 *
 *   {"type":"client_event","event":"huxley.firmware_log",
 *    "data":{"level":"W","tag":"hux_net","line":"...","ts":12345}}
 *
 * Drops the entry silently if the WS isn't connected — remote logging
 * is best-effort; serial already has the line.
 */
bool hux_net_send_log(const hux_log_entry_t *entry);

/**
 * Callback invoked for every inbound `audio` message, with the message's
 * base64-decoded PCM16 bytes. The `pcm` pointer is only valid for the
 * duration of the call — the sink MUST copy into its own buffer (a
 * speaker ring, a file writer, etc.) or drop; retaining the pointer is
 * a bug.
 *
 * Runs on the WebSocket client task. Keep it tight: no blocking I/O,
 * no heap allocation, no logging above DEBUG. A slow sink stalls every
 * subsequent WS message.
 */
typedef void (*hux_net_audio_sink_fn)(const uint8_t *pcm, size_t len);

/**
 * Register the audio sink. Pass NULL to unregister (inbound audio will
 * then drop silently). The pointer update is a release-store —
 * whoever observes the new sink also sees the sink's fully-constructed
 * internal state (ring buffers, ES8311 handle, etc.).
 *
 * This is the seam that keeps the audio hot path OUT of
 * `hux_app`'s event queue — at 50 Hz the app task couldn't keep up
 * with per-frame JSON parses and heap copies. See
 * firmware/docs/architecture.md §"Data plane vs control plane".
 */
void hux_net_set_audio_sink(hux_net_audio_sink_fn sink);

/** Milliseconds of server audio received since boot (Opus packets, 60 ms
 * each): the hello's received_ms_last_conn (T1.44). */
uint32_t hux_net_rx_audio_ms(void);

/** The walkie has an IP now (F-0032: it can reach the internet at all). */
bool hux_net_have_ip(void);
/** How long the server has been unreachable, ms; 0 while it's up or the
 * walkie is paused on purpose (an eviction). */
uint32_t hux_net_server_lost_ms(void);

/**
 * Called for every inbound `audio_clear` ("drop any queued audio": the
 * server is interrupting, seeking, or starting something new). Runs on
 * the WebSocket client task, in order with the `audio` sink, so a clear
 * never lands after the audio that follows it. May block briefly (the
 * speaker ring is reset by its own reader).
 */
typedef void (*hux_net_audio_clear_fn)(void);

/** Register the `audio_clear` handler. Pass NULL to unregister. */
void hux_net_set_audio_clear_sink(hux_net_audio_clear_fn sink);

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
    const char *wifi_ssid;
    const char *wifi_password;
    /** Full URI, e.g. "ws://192.168.68.83:8765/". */
    const char *server_uri;
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

/** The app's hello arrived: a pending `server_keep` is saved; a fallback
 * stops alternating. */
void hux_net_hello_ok(void);

/** Log the current, proven and compiled addresses and the mode. */
void hux_net_server_status(void);

/** The ladder task's ticks (one per 5 s): the supervisor's view of it
 * (F-0027). */
uint32_t hux_net_ladder_ticks(void);

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

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
void hux_net_send_log(const hux_log_entry_t *entry);

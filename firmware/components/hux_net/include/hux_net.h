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

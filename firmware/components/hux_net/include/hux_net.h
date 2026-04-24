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

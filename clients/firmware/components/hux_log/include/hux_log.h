/**
 * hux_log — firmware log hijack + remote streaming plumbing.
 *
 * Hooks ESP-IDF's `vprintf` so every `ESP_LOGE/W/I/D/V` call still
 * reaches serial AND — for lines at or above the remote threshold — is
 * parsed, queued, and handed to a registered sink. The sink is owned
 * by `hux_net`, which builds the `client_event` JSON envelope and
 * sends it over the WebSocket.
 *
 * Recursion guard: the dedicated drain task's own log lines are NEVER
 * queued. Tags belonging to the underlying send stack
 * (`websocket_client`, `transport_*`, `esp-tls`, `mbedtls`) are
 * filtered before queueing so a send-path log can't feed itself back
 * into the queue.
 *
 * See firmware/docs/debugging.md for the log convention and how the
 * server side sees the stream.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * One parsed log line. The struct is small enough to pass by value
 * through a FreeRTOS queue (~232 B). Fields are NUL-terminated
 * strings; `line` is pre-sanitised (no trailing newline, no ANSI
 * escapes).
 */
typedef struct {
    char level;        /* 'E', 'W', 'I', 'D', 'V' */
    uint32_t ts_ms;    /* Milliseconds since boot (from the ESP log prefix). */
    char tag[28];      /* Component tag, e.g. "hux_net". */
    char line[192];    /* Message body, truncated if longer. */
} hux_log_entry_t;

typedef void (*hux_log_sink_fn)(const hux_log_entry_t *entry);

/**
 * Install the vprintf hook, allocate the ring, spawn the drain task.
 * Idempotent — second call is a no-op. Safe to call very early in
 * `app_main` (before Wi-Fi, before any other component).
 */
void hux_log_init(void);

/**
 * Register the callback that receives log lines cleared for remote
 * delivery. Pass NULL to unregister. The sink runs on the drain task.
 *
 * The sink MUST NOT block on network I/O from taking tens of seconds;
 * if a send fails, drop the line and return. Drain throughput dictates
 * how fast the ring can recover from a burst.
 */
void hux_log_set_sink(hux_log_sink_fn sink);

/**
 * Set the minimum level that gets forwarded to the sink. Character
 * form: 'E' (errors only), 'W' (warnings+), 'I' (info+), 'D' (debug+),
 * 'V' (verbose+). Default is 'W'. Anything below this threshold stays
 * serial-only.
 */
void hux_log_set_remote_level(char level_char);

#ifdef __cplusplus
}
#endif

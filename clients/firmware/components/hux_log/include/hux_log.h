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

#include <stdbool.h>
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

/* Returns true when the line is dealt with (sent, or unsendable for good),
 * false when it couldn't go out now (offline): the drain task keeps it
 * and tries again, so a drop's own story reaches the server once the
 * link is back (F-0022). */
typedef bool (*hux_log_sink_fn)(const hux_log_entry_t *entry);

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
 * The sink MUST NOT block on network I/O for tens of seconds; return
 * false when offline and the line is retried. Lines logged before the
 * sink exists (boot) wait in the queue for it.
 */
void hux_log_set_sink(hux_log_sink_fn sink);

/**
 * Set the minimum level that gets forwarded to the sink. Character
 * form: 'E' (errors only), 'W' (warnings+), 'I' (info+), 'D' (debug+),
 * 'V' (verbose+). Default is 'W'. Anything below this threshold stays
 * serial-only.
 */
void hux_log_set_remote_level(char level_char);

/**
 * The previous boot's last warnings and errors (`device.blackbox`, about
 * 4 KB, oldest first, one per line), or NULL: none (a power-on, or
 * nothing logged). `*dropped` gets the lines it had to overwrite. Kept
 * until hux_log_blackbox_sent(). App task only.
 */
const char *hux_log_blackbox(uint32_t *dropped);

/** The black box reached the server: free it. */
void hux_log_blackbox_sent(void);

/** Capture what the calling task logs into `buf` ("L tag: text" lines,
 * NUL-terminated) until hux_log_capture_end(): a console command's output
 * for dev.cmd (F-0028). One capture at a time. */
void hux_log_capture_begin(char *buf, size_t size);

/** Stop capturing; the length captured, `*truncated` if lines didn't fit. */
size_t hux_log_capture_end(bool *truncated);

/** Put one line straight into this boot's black box, from an interrupt
 * (the task watchdog's, just before it panics). */
void hux_log_blackbox_note_isr(const char *line);

#ifdef __cplusplus
}
#endif

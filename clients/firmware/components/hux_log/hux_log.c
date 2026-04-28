#include "hux_log.h"

#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#define LOG_QUEUE_DEPTH      32
#define DRAIN_TASK_STACK     4096
#define DRAIN_TASK_PRIO      2
/* vprintf hook runs on the caller's stack — Wi-Fi, LWIP, and ISR
 * paths have tight stacks (~2–4 KB). 256 B covers typical IDF log
 * lines (~80–150 B); anything longer is truncated rather than stack-
 * smashed. */
#define FORMAT_BUF_BYTES     256
/* How often the drain task wakes to report dropped-log counts, even
 * with an empty queue. 5 seconds keeps the counter visible without
 * spamming. */
#define DROP_HEARTBEAT_TICKS (5000 / portTICK_PERIOD_MS)

/* Tags whose log lines must NOT stream — otherwise a failing send
 * produces more logs, which get streamed, which may fail again… These
 * are exactly the components that live under `hux_net`'s send path.
 * Missing a new one would cause the queue to grow unboundedly on
 * disconnect. */
static const char *const DENY_TAGS[] = {
    "websocket_client",
    "transport_ws",
    "transport_base",
    "transport_raw_tcp",
    "esp-tls",
    "esp-tls-mbedtls",
    "mbedtls",
    NULL,
};

static vprintf_like_t s_prev_vprintf = NULL;
static QueueHandle_t s_queue = NULL;
static TaskHandle_t s_drain_task = NULL;
/* Sink + level are read by log-producing tasks on either CPU core and
 * written by `app_main` (single writer). `_Atomic` gives aligned
 * word-sized atomic loads/stores with explicit ordering — prevents a
 * producer on CPU1 from observing a half-initialised sink pointer. */
static _Atomic(hux_log_sink_fn) s_sink = NULL;
static _Atomic char s_remote_level = 'W';
/* Monotonic drop counter. Bumped from the vprintf hook (any task, any
 * context) when the ring is full; reported out by the drain task. */
static _Atomic uint32_t s_dropped = 0;

static int level_rank(char c) {
    switch (c) {
        case 'E': return 1;
        case 'W': return 2;
        case 'I': return 3;
        case 'D': return 4;
        case 'V': return 5;
        default:  return 0;
    }
}

static bool tag_denied(const char *tag) {
    for (size_t i = 0; DENY_TAGS[i] != NULL; i++) {
        if (strcmp(tag, DENY_TAGS[i]) == 0) {
            return true;
        }
    }
    return false;
}

/* Parse one IDF-formatted log line into the struct. Handles both
 * color-prefixed (`\033[...m...`) and bare output. Returns true on
 * recognisable shape. Truncates tag and message to the struct's
 * bounded sizes — oversized messages stream as far as they fit.
 *
 * Expected shape after stripping the optional ANSI prefix:
 *   <LEVEL> (<ts>) <tag>: <message>\n
 * Examples:
 *   "I (6101) hux_net: ws.connected uri=...\n"
 *   "\x1b[0;32mI (6101) hux_net: ws.connected uri=...\x1b[0m\n"
 */
static bool parse_log_line(const char *s, hux_log_entry_t *out) {
    if (s == NULL || out == NULL) {
        return false;
    }

    /* Strip ANSI color prefix if present. */
    if (*s == '\x1b') {
        while (*s && *s != 'm') s++;
        if (*s == 'm') s++;
    }

    char level = *s;
    if (level_rank(level) == 0) {
        return false;
    }
    s++;
    if (s[0] != ' ' || s[1] != '(') {
        return false;
    }
    s += 2;

    uint32_t ts = 0;
    while (*s >= '0' && *s <= '9') {
        ts = ts * 10 + (uint32_t)(*s - '0');
        s++;
    }
    if (*s != ')' || s[1] != ' ') {
        return false;
    }
    s += 2;

    size_t ti = 0;
    while (*s && *s != ':' && ti < sizeof(out->tag) - 1) {
        out->tag[ti++] = *s++;
    }
    /* If the tag was longer than our bound, consume the remainder. */
    while (*s && *s != ':') {
        s++;
    }
    out->tag[ti] = '\0';

    if (*s != ':') {
        return false;
    }
    s++;
    if (*s == ' ') {
        s++;
    }

    /* Message runs until newline, ANSI-reset escape, or NUL. */
    const char *end = s;
    while (*end && *end != '\n' && *end != '\x1b') {
        end++;
    }
    size_t line_len = (size_t)(end - s);
    if (line_len >= sizeof(out->line)) {
        line_len = sizeof(out->line) - 1;
    }
    memcpy(out->line, s, line_len);
    out->line[line_len] = '\0';

    out->level = level;
    out->ts_ms = ts;
    return true;
}

static int hux_vprintf(const char *fmt, va_list args) {
    /* Hand serial output to the previous handler first — this path
     * has no allocation and no blocking, so it survives early boot,
     * ISRs, and panics just as the IDF default would. `va_list` is a
     * one-shot per spec: we copy before the delegate call, `va_end`
     * the original after it returns, and consume the copy ourselves. */
    va_list copy;
    va_copy(copy, args);
    int n = s_prev_vprintf != NULL ? s_prev_vprintf(fmt, args) : vprintf(fmt, args);
    va_end(args);

    /* Snapshot atomic globals with acquire ordering so the values we
     * inspect are consistent with the corresponding stores on other
     * cores (notably `hux_log_set_sink` from app_main). */
    hux_log_sink_fn sink = atomic_load_explicit(&s_sink, memory_order_acquire);
    char threshold = atomic_load_explicit(&s_remote_level, memory_order_relaxed);

    /* Bail out before any queueing work if we don't have a sink yet,
     * the queue isn't initialised, or we're running on the drain task
     * itself (recursion guard — send-path logs from the drain task
     * would otherwise feed themselves back into the queue). */
    if (sink == NULL || s_queue == NULL ||
        xTaskGetCurrentTaskHandle() == s_drain_task) {
        va_end(copy);
        return n;
    }

    /* Bounded stack buffer — see FORMAT_BUF_BYTES rationale. Lines
     * longer than this are truncated in the remote stream; serial
     * sees the full line via the delegate above. */
    char buf[FORMAT_BUF_BYTES];
    int written = vsnprintf(buf, sizeof(buf), fmt, copy);
    va_end(copy);
    if (written <= 0) {
        return n;
    }

    hux_log_entry_t entry = {0};
    if (!parse_log_line(buf, &entry)) {
        return n;
    }
    if (level_rank(entry.level) > level_rank(threshold)) {
        return n; /* Below threshold — serial only. */
    }
    if (tag_denied(entry.tag)) {
        return n; /* Send-path recursion blocker. */
    }

    /* Non-blocking send — if the queue is full we drop the line and
     * bump the counter; the drain task publishes the drop count on
     * its periodic heartbeat. ISR callers (panic path, Wi-Fi driver
     * ISRs logging errors) need the FromISR variant; `xQueueSend`
     * from ISR is undefined behaviour on FreeRTOS. */
    BaseType_t ok;
    if (xPortInIsrContext()) {
        BaseType_t hpw = pdFALSE;
        ok = xQueueSendFromISR(s_queue, &entry, &hpw);
        if (hpw == pdTRUE) {
            portYIELD_FROM_ISR();
        }
    } else {
        ok = xQueueSend(s_queue, &entry, 0);
    }
    if (ok != pdTRUE) {
        atomic_fetch_add_explicit(&s_dropped, 1, memory_order_relaxed);
    }
    return n;
}

/* Build a synthetic "dropped N logs" entry and hand it directly to
 * the sink, bypassing the ring. We can't ESP_LOGW about drops —
 * those would feed through the hook, which might itself drop. */
static void emit_drop_heartbeat(hux_log_sink_fn sink, uint32_t *last_reported) {
    uint32_t now = atomic_load_explicit(&s_dropped, memory_order_relaxed);
    if (now == *last_reported) {
        return;
    }
    uint32_t since = now - *last_reported;
    *last_reported = now;

    hux_log_entry_t entry = {.level = 'W', .ts_ms = 0};
    strcpy(entry.tag, "hux_log");
    snprintf(entry.line, sizeof(entry.line),
             "log.dropped since_last=%u total=%u",
             (unsigned)since, (unsigned)now);
    entry.ts_ms = (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS);
    sink(&entry);
}

static void drain_task(void *arg) {
    (void)arg;
    hux_log_entry_t entry;
    uint32_t dropped_last_reported = 0;
    for (;;) {
        /* Block with a heartbeat timeout so we still surface drop
         * counts when the queue is quiet. A noisy queue naturally
         * visits the heartbeat branch often enough via the second
         * arm below. */
        if (xQueueReceive(s_queue, &entry, DROP_HEARTBEAT_TICKS) == pdTRUE) {
            hux_log_sink_fn sink = atomic_load_explicit(&s_sink, memory_order_acquire);
            if (sink != NULL) {
                sink(&entry);
                emit_drop_heartbeat(sink, &dropped_last_reported);
            }
        } else {
            hux_log_sink_fn sink = atomic_load_explicit(&s_sink, memory_order_acquire);
            if (sink != NULL) {
                emit_drop_heartbeat(sink, &dropped_last_reported);
            }
        }
    }
}

void hux_log_init(void) {
    if (s_queue != NULL) {
        return; /* Idempotent — already initialised. */
    }
    s_queue = xQueueCreate(LOG_QUEUE_DEPTH, sizeof(hux_log_entry_t));
    configASSERT(s_queue != NULL);

    BaseType_t ok = xTaskCreate(drain_task, "hux_log_drain", DRAIN_TASK_STACK,
                                NULL, DRAIN_TASK_PRIO, &s_drain_task);
    configASSERT(ok == pdPASS);

    /* Install the hook last so no log line flows through it before
     * the drain task and queue exist. */
    s_prev_vprintf = esp_log_set_vprintf(hux_vprintf);
}

void hux_log_set_sink(hux_log_sink_fn sink) {
    /* Release-store pairs with the acquire-load in `hux_vprintf`:
     * by the time any producer observes `sink != NULL`, all writes
     * that set up the sink's internal state are visible too. */
    atomic_store_explicit(&s_sink, sink, memory_order_release);
}

void hux_log_set_remote_level(char level_char) {
    if (level_rank(level_char) == 0) {
        return;
    }
    atomic_store_explicit(&s_remote_level, level_char, memory_order_relaxed);
}

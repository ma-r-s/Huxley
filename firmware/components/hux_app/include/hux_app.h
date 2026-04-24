/**
 * hux_app — the single state owner.
 *
 * Every other task (net, mic, spk, button…) emits events into `hux_app`'s
 * queue via `hux_app_post_event`. `app_task` is the only code that
 * mutates session state. No mutexes on business state — ever. Adding a
 * new task means adding an event kind + a case in the dispatch switch.
 *
 * The queue takes events by value; payloads larger than a few bytes
 * (JSON text, PCM buffers) are heap-allocated by the producer, handed
 * off via a pointer in the payload union, and freed by `app_task` after
 * dispatch. Producers do not retain references.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

typedef enum {
    HUX_APP_EV_NET_WIFI_UP = 1,
    HUX_APP_EV_NET_WIFI_DOWN,
    HUX_APP_EV_NET_WS_CONNECTED,
    HUX_APP_EV_NET_WS_DISCONNECTED,
    HUX_APP_EV_NET_WS_MESSAGE,
} hux_app_event_kind_t;

typedef struct {
    /* Malloc'd UTF-8 JSON text. `app_task` frees after dispatch. */
    char *data;
    size_t len;
} hux_app_ws_message_t;

typedef struct {
    hux_app_event_kind_t kind;
    union {
        hux_app_ws_message_t ws_message;
    } payload;
} hux_app_event_t;

/**
 * Spawn the app task + create the event queue. Safe to call once during
 * boot, before any producer posts events. Idempotent calls are not
 * supported — this is a boot-time init, not a runtime hook.
 */
void hux_app_start(void);

/**
 * Post an event to the app task. Returns true on success, false if the
 * queue is full (event dropped; producer should log and continue).
 * Safe to call from any task, including ISRs when `from_isr` is true.
 *
 * The event struct is copied by value into the queue; any heap pointer
 * inside `event->payload` is handed over — the caller must not free it
 * or access it after a successful post.
 */
bool hux_app_post_event(const hux_app_event_t *event, bool from_isr);

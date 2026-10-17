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
#include <stdint.h>

typedef enum {
    HUX_APP_EV_NET_WIFI_UP = 1,
    HUX_APP_EV_NET_WIFI_DOWN,
    HUX_APP_EV_NET_WS_CONNECTED,
    HUX_APP_EV_NET_WS_DISCONNECTED,
    HUX_APP_EV_NET_WS_MESSAGE,
    /* The server replaced us with another client (close 1001). */
    HUX_APP_EV_NET_WS_EVICTED,
    /* The WebSocket client's task ended (it no longer reconnects). */
    HUX_APP_EV_NET_WS_FINISHED,
    /* Supervisor test (console `hang app`): the app task blocks for good. */
    HUX_APP_EV_DEBUG_HANG,
    /* Button events. K1/K3 will land as their own kinds when a
     * consumer appears — see hux_button.c. */
    HUX_APP_EV_BUTTON_K2_PRESSED,
    HUX_APP_EV_BUTTON_K2_RELEASED,
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
        /* Button events: when hux_button saw the edge (esp_timer ms), so a
         * held-up app task still sees the real gap between edges. */
        uint32_t button_at_ms;
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

/** Identity reported to the server on every connect (`client_event:
 * device`). Strings must outlive the firmware (static or app desc). */
typedef struct {
    const char *version;
    const char *reset;
    char mac[18];
    uint32_t boot_counter;
    bool power_on; /* a real power on, or a charge after the battery park (F-0033) */
} hux_app_device_info_t;

/** Set once at boot, before Wi-Fi starts. */
void hux_app_set_device_info(const hux_app_device_info_t *info);

/** The supervisor's view (F-0027): app loop wakes (one a second at least),
 * and the uplink's items taken / waiting. */
uint32_t hux_app_beats(void);

/** Runs one console line for server_event dev.cmd (main wires
 * hux_console_run; the line may be modified). */
typedef void (*hux_app_dev_cmd_fn)(char *line);
void hux_app_set_dev_cmd_runner(hux_app_dev_cmd_fn runner);

/** client_event firmware.status (F-0028): hux_ota's status sink. Any task. */
void hux_app_firmware_status(const char *version, const char *phase, const char *error);

/** Send a battery reading to the server (`client_event system.battery
 * {mv, pct}`, F-0025): hux_battery's sink. Any task. */
void hux_app_report_battery(int cell_mv, int pct);

/** Bench (console `status poweron`): the connection sounds start over as
 * after a power on, which can't be done from afar (F-0033). Any task. */
void hux_app_status_power_on(void);
void hux_app_uplink_progress(uint32_t *done, uint32_t *pending);

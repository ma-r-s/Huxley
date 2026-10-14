/**
 * hux_alert_logic: when the walkie pushes "needs you" / "fixed" to Mario's
 * phone itself (F-0032). Pure, host-tested (tests/test_hux_alert_logic.c);
 * hux_alert does the I/O.
 *
 * Down: the server unreachable for 11 min while the walkie has an IP (its
 * own internet may still work when the Pi, its power, the house internet or
 * the tunnel is down; 11, not 10: the ladder's Wi-Fi step is at 10).
 * "Unreachable" counts from the last steady connection (hux_net: up 2 min),
 * so a server that connects and crashes over and over still counts. Fixed:
 * once it's back, only after a down went out, at once (quiet hours make it
 * a silent push, never a held one: a stale "needs you" overnight). Never
 * repeated while the state is the same (RTC memory, and NVS for a power
 * cut). Quiet hours 22:00-07:00 (UTC-5) hold a down until 07:00. A failed
 * send is retried after 2 min, doubling to 30.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_ALERT_DOWN_AFTER_MS (11u * 60u * 1000u)
#define HUX_ALERT_RETRY_MS      (2u * 60u * 1000u)
#define HUX_ALERT_RETRY_MAX_MS  (30u * 60u * 1000u)
#define HUX_ALERT_UTC_OFFSET_S  (-5 * 3600) /* Bogota, no DST */
#define HUX_ALERT_QUIET_FROM_H  22
#define HUX_ALERT_QUIET_TO_H    7

typedef struct {
    uint32_t down_sent;     /* a "needs you" went out and no "fixed" yet */
    int64_t down_since;     /* epoch s the server was lost (0: unknown) */
    int64_t fixed_at;       /* epoch s it came back (0: not yet) */
    uint32_t failed_at_ms;  /* uptime of the last failed send */
    uint32_t retry_ms;      /* how long after it to try again (0: no failure pending) */
} hux_alert_state_t;

typedef enum {
    HUX_ALERT_NONE = 0,
    HUX_ALERT_SEND_DOWN,
    HUX_ALERT_SEND_FIXED,
} hux_alert_action_t;

/** One tick. `epoch_s`: wall-clock seconds (0 when not synced yet: then
 * there are no quiet hours). `lost_ms`: how long the server has been
 * unreachable (0 when it's up). Returns what to send now. */
hux_alert_action_t hux_alert_step(hux_alert_state_t *s, int64_t epoch_s, uint32_t uptime_ms,
                                  bool have_ip, bool server_up, uint32_t lost_ms);

/** After a send: `ok` (an HTTP 2xx) moves the state on; a failure waits
 * HUX_ALERT_RETRY_MS, doubling to HUX_ALERT_RETRY_MAX_MS. */
void hux_alert_sent(hux_alert_state_t *s, hux_alert_action_t what, bool ok, uint32_t uptime_ms);

/** Quiet hours at `epoch_s` (false when the clock isn't synced). */
bool hux_alert_quiet(int64_t epoch_s);

/** "HH:MM" local time of `epoch_s` into `out` (6 bytes), "?" if unknown. */
void hux_alert_hhmm(int64_t epoch_s, char *out);

#ifdef __cplusplus
}
#endif

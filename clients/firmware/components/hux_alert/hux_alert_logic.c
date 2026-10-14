#include "hux_alert_logic.h"

#include <stdio.h>
#include <string.h>

#define SYNCED_AFTER 1767225600 /* 2026-01-01: an unsynced clock reads 1970 */

static int local_hour(int64_t epoch_s) {
    int64_t local = epoch_s + HUX_ALERT_UTC_OFFSET_S;
    return (int)((local % 86400 + 86400) % 86400 / 3600);
}

bool hux_alert_quiet(int64_t epoch_s) {
    if (epoch_s < SYNCED_AFTER) {
        return false; /* no clock: don't hold an alert for hours on a guess */
    }
    int h = local_hour(epoch_s);
    return h >= HUX_ALERT_QUIET_FROM_H || h < HUX_ALERT_QUIET_TO_H;
}

void hux_alert_hhmm(int64_t epoch_s, char *out) {
    if (epoch_s < SYNCED_AFTER) {
        strcpy(out, "?");
        return;
    }
    int64_t local = epoch_s + HUX_ALERT_UTC_OFFSET_S;
    int64_t day = (local % 86400 + 86400) % 86400;
    snprintf(out, 6, "%02d:%02d", (int)(day / 3600), (int)(day % 3600 / 60));
}

/* A failed send is waiting out its retry (unsigned: uptime wraps after 49
 * days, the difference doesn't). */
static bool retry_wait(const hux_alert_state_t *s, uint32_t uptime_ms) {
    return s->retry_ms != 0 && uptime_ms - s->failed_at_ms < s->retry_ms;
}

hux_alert_action_t hux_alert_step(hux_alert_state_t *s, int64_t epoch_s, uint32_t uptime_ms,
                                  bool have_ip, bool server_up, uint32_t lost_ms) {
    bool synced = epoch_s >= SYNCED_AFTER;
    if (!s->down_sent) {
        if (server_up) {
            s->down_since = 0; /* back before anything went out: nothing to say */
            s->retry_ms = 0;
            return HUX_ALERT_NONE;
        }
        if (s->down_since == 0 && synced) {
            s->down_since = epoch_s - (int64_t)(lost_ms / 1000);
        }
        if (!have_ip || lost_ms < HUX_ALERT_DOWN_AFTER_MS || hux_alert_quiet(epoch_s) ||
            retry_wait(s, uptime_ms)) {
            return HUX_ALERT_NONE;
        }
        return HUX_ALERT_SEND_DOWN;
    }
    if (!server_up) {
        s->fixed_at = 0; /* down again before "fixed" went out: still one outage */
        return HUX_ALERT_NONE;
    }
    if (s->fixed_at == 0 && synced) {
        s->fixed_at = epoch_s;
    }
    if (retry_wait(s, uptime_ms)) {
        return HUX_ALERT_NONE;
    }
    return HUX_ALERT_SEND_FIXED; /* never held: quiet hours make it silent instead */
}

void hux_alert_sent(hux_alert_state_t *s, hux_alert_action_t what, bool ok, uint32_t uptime_ms) {
    if (!ok) {
        s->failed_at_ms = uptime_ms;
        s->retry_ms = s->retry_ms == 0 ? HUX_ALERT_RETRY_MS
                      : s->retry_ms * 2 > HUX_ALERT_RETRY_MAX_MS ? HUX_ALERT_RETRY_MAX_MS
                                                                 : s->retry_ms * 2;
        return;
    }
    s->retry_ms = 0;
    if (what == HUX_ALERT_SEND_DOWN) {
        s->down_sent = 1;
        s->fixed_at = 0;
    } else if (what == HUX_ALERT_SEND_FIXED) {
        s->down_sent = 0;
        s->down_since = 0;
        s->fixed_at = 0;
    }
}

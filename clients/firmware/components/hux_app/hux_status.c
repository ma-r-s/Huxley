#include "hux_status.h"

#include <string.h>

void hux_status_init(hux_status_t *s, bool power_on, bool connected_owed) {
    memset(s, 0, sizeof(*s));
    s->phase = power_on ? HUX_PHASE_BOOT : HUX_PHASE_SEARCHING;
    s->announce = power_on || connected_owed;
    s->ticks_off = !power_on;
}

/* A due sound: played, or held by the night (the state moves on either way). */
static hux_status_sound_t due(hux_status_t *s, hux_status_sound_t sound, bool night,
                              hux_status_sound_t *held) {
    if (night) {
        *held = sound;
        s->held_busy = false;
        return HUX_STATUS_NONE;
    }
    return sound;
}

static void start_searching(hux_status_t *s, uint32_t now_ms) {
    s->phase = HUX_PHASE_SEARCHING;
    s->search_since = now_ms;
    s->last_tick = now_ms; /* the first tick 3 s from now */
    s->ticks_off = false;
    s->ticks = 0;
    s->schedule_over = false;
}

hux_status_sound_t hux_status_step(hux_status_t *s, uint32_t now_ms, bool connected, bool busy,
                                   bool night, hux_status_sound_t *held) {
    *held = HUX_STATUS_NONE;
    if (s->phase == HUX_PHASE_BOOT) {
        if (busy) {
            return HUX_STATUS_NONE;
        }
        start_searching(s, now_ms);
        return HUX_STATUS_STARTUP; /* power on sounds, even at night */
    }
    if (connected) {
        if (s->phase == HUX_PHASE_CONNECTED) {
            return HUX_STATUS_NONE;
        }
        if (s->phase == HUX_PHASE_DROPPED) {
            s->phase = HUX_PHASE_CONNECTED; /* a blip: silent, the same connection */
            return HUX_STATUS_NONE;
        }
        if (s->announce && busy) {
            if (!s->owed) {
                s->owed = true;
                s->owed_at = now_ms;
            }
            if (now_ms - s->owed_at < HUX_STATUS_OWED_FOR_MS) {
                return HUX_STATUS_NONE; /* waits for a gap */
            }
            /* Still busy: the server or a press already says it. */
            s->phase = HUX_PHASE_CONNECTED;
            s->connected_at = s->owed_at;
            s->announce = false;
            s->owed = false;
            *held = HUX_STATUS_CONNECTED;
            s->held_busy = true;
            return HUX_STATUS_NONE;
        }
        s->phase = HUX_PHASE_CONNECTED;
        s->connected_at = s->owed ? s->owed_at : now_ms;
        s->owed = false;
        if (!s->announce) {
            return HUX_STATUS_NONE;
        }
        s->announce = false;
        return due(s, HUX_STATUS_CONNECTED, night, held);
    }
    s->owed = false; /* gone again before it could play: owed from the next one */
    if (s->phase == HUX_PHASE_CONNECTED) {
        s->phase = HUX_PHASE_DROPPED;
        s->dropped_at = now_ms;
        return HUX_STATUS_NONE;
    }
    if (s->phase == HUX_PHASE_DROPPED) {
        if (now_ms - s->dropped_at < HUX_STATUS_LOST_AFTER_MS || busy) {
            return HUX_STATUS_NONE;
        }
        if (s->in_outage && s->dropped_at - s->connected_at < HUX_STATUS_STABLE_MS) {
            /* Up for less than 2 min: the same outage goes on, quietly,
             * on its first schedule (and owes no second "connected"). */
            s->phase = HUX_PHASE_SEARCHING;
            return HUX_STATUS_NONE;
        }
        s->in_outage = true;
        s->announce = true;
        start_searching(s, now_ms);
        return due(s, HUX_STATUS_LOST, night, held);
    }
    if (s->ticks_off) {
        return HUX_STATUS_NONE;
    }
    uint32_t elapsed = now_ms - s->search_since;
    if (elapsed >= HUX_STATUS_SEARCH_FOR_MS) {
        s->schedule_over = true; /* stopped: battery, and no beeping all night */
        return HUX_STATUS_NONE;
    }
    uint32_t every = elapsed < HUX_STATUS_FAST_FOR_MS ? HUX_STATUS_FAST_MS : HUX_STATUS_SLOW_MS;
    if (now_ms - s->last_tick < every || busy) {
        return HUX_STATUS_NONE;
    }
    s->last_tick = now_ms;
    s->ticks++;
    return due(s, HUX_STATUS_SEARCHING, night, held);
}

const char *hux_status_name(hux_status_sound_t sound) {
    switch (sound) {
        case HUX_STATUS_STARTUP:   return "startup";
        case HUX_STATUS_SEARCHING: return "searching";
        case HUX_STATUS_CONNECTED: return "connected";
        case HUX_STATUS_LOST:      return "lost";
        default:                   return "none";
    }
}

const char *hux_status_phase_name(hux_status_phase_t phase) {
    switch (phase) {
        case HUX_PHASE_BOOT:      return "boot";
        case HUX_PHASE_SEARCHING: return "searching";
        case HUX_PHASE_CONNECTED: return "connected";
        case HUX_PHASE_DROPPED:   return "dropped";
        default:                  return "?";
    }
}

hux_status_updating_t hux_status_updating(bool ota_busy, bool image_pending, uint32_t uptime_ms,
                                          uint32_t restart_close_age_ms) {
    if (ota_busy) {
        return HUX_UPDATING_OTA;
    }
    if (image_pending && uptime_ms < HUX_STATUS_PENDING_SAY_MS) {
        return HUX_UPDATING_PENDING;
    }
    if (restart_close_age_ms < HUX_STATUS_RESTART_SAY_MS) {
        return HUX_UPDATING_SERVER;
    }
    return HUX_UPDATING_NO;
}

const char *hux_status_updating_name(hux_status_updating_t why) {
    switch (why) {
        case HUX_UPDATING_OTA:     return "ota";
        case HUX_UPDATING_PENDING: return "ota_pending";
        case HUX_UPDATING_SERVER:  return "server_restart";
        default:                   return "none";
    }
}

hux_status_offline_t hux_status_offline_kind(bool have_ip, bool internet_failed) {
    if (!have_ip) {
        return HUX_OFFLINE_NO_WIFI;
    }
    return internet_failed ? HUX_OFFLINE_NO_INTERNET : HUX_OFFLINE_SERVER;
}

const char *hux_status_offline_name(hux_status_offline_t kind) {
    switch (kind) {
        case HUX_OFFLINE_NO_WIFI:     return "no_wifi";
        case HUX_OFFLINE_NO_INTERNET: return "no_internet";
        default:                      return "offline";
    }
}

hux_lever_action_t hux_lever_press(hux_lever_t *l, uint32_t now_ms) {
    if (l->release_pending) {
        l->release_pending = false;
        /* Inside the window: a bounce, never let go. Past it: a real
         * release the app only hadn't committed yet. */
        return now_ms - l->release_at < HUX_LEVER_MERGE_MS ? HUX_LEVER_REBOUND : HUX_LEVER_RESTART;
    }
    if (l->down) {
        return HUX_LEVER_RESTART; /* the release between them was dropped */
    }
    l->down = true;
    return HUX_LEVER_START;
}

void hux_lever_release(hux_lever_t *l, uint32_t now_ms) {
    if (!l->down || l->release_pending) {
        return;
    }
    l->release_pending = true;
    l->release_at = now_ms;
}

hux_lever_action_t hux_lever_tick(hux_lever_t *l, uint32_t now_ms) {
    if (!l->release_pending || now_ms - l->release_at < HUX_LEVER_MERGE_MS) {
        return HUX_LEVER_NONE;
    }
    l->release_pending = false;
    l->down = false;
    return HUX_LEVER_STOP;
}

void hux_ota_wait_hold(hux_ota_wait_t *w, uint32_t now_ms) {
    if (!w->held) {
        w->held = true;
        w->idle_since = now_ms;
    }
}

bool hux_ota_wait_due(hux_ota_wait_t *w, uint32_t now_ms, bool busy, bool connected) {
    if (!w->held) {
        return false;
    }
    if (busy || !connected) {
        w->idle_since = now_ms;
        return false;
    }
    if (now_ms - w->idle_since < HUX_OTA_IDLE_MS) {
        return false;
    }
    w->held = false;
    return true;
}

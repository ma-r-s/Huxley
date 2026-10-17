#include "hux_wifi_list.h"

#include <stdio.h>
#include <string.h>

/* The best signal heard for `ssid`, or INT8_MIN if not heard. */
static int heard_rssi(const char *ssid, const hux_wifi_heard_t *heard, int nh) {
    int best = INT8_MIN;
    for (int i = 0; i < nh; i++) {
        if (heard[i].ssid[0] != '\0' && strcmp(heard[i].ssid, ssid) == 0 && heard[i].rssi > best) {
            best = heard[i].rssi;
        }
    }
    return best;
}

static bool is_good(const hux_wifi_net_t *net, const char *good) {
    return good != NULL && good[0] != '\0' && strcmp(net->ssid, good) == 0;
}

int hux_wifi_rank(const hux_wifi_net_t *known, int n, const char *good,
                  const hux_wifi_heard_t *heard, int nh, int *order, int cap) {
    int count = 0;
    int rssi[HUX_WIFI_MAX];
    for (int i = 0; i < n && i < HUX_WIFI_MAX; i++) {
        rssi[i] = heard_rssi(known[i].ssid, heard, nh);
    }
    /* Heard: good first, then by signal (insertion, stable by index). */
    for (int i = 0; i < n && i < HUX_WIFI_MAX && count < cap; i++) {
        if (rssi[i] == INT8_MIN) {
            continue;
        }
        int j = count++;
        for (; j > 0; j--) {
            int k = order[j - 1];
            bool before = is_good(&known[i], good) ||
                          (!is_good(&known[k], good) && rssi[i] > rssi[k]);
            if (!before) {
                break;
            }
            order[j] = k;
        }
        order[j] = i;
    }
    return count;
}

static int find(const hux_wifi_net_t *known, int n, const char *ssid) {
    for (int i = 0; i < n; i++) {
        if (strcmp(known[i].ssid, ssid) == 0) {
            return i;
        }
    }
    return -1;
}

hux_wifi_add_t hux_wifi_add_plan(const hux_wifi_net_t *known, int n, const char *ssid,
                                 const char *pass, hux_wifi_src_t src) {
    if (src == HUX_WIFI_SRC_DOOR) {
        return HUX_WIFI_ADD_REFUSED;
    }
    int i = find(known, n, ssid);
    if (i >= 0) {
        if (strcmp(known[i].pass, pass) == 0 || strcmp(known[i].pass2, pass) == 0) {
            return HUX_WIFI_ADD_SAME;
        }
        if (known[i].staged) {
            return HUX_WIFI_ADD_RESTAGE; /* a typo corrected: never reached the server anyway */
        }
        /* A compiled name keeps one stored password; once that one reached
         * the server it is the good one and is never replaced. */
        return known[i].compiled && known[i].alt_proven ? HUX_WIFI_ADD_IN_USE
                                                        : HUX_WIFI_ADD_SECOND;
    }
    int saved = 0;
    for (int k = 0; k < n; k++) {
        saved += !known[k].compiled;
    }
    if (saved >= HUX_WIFI_SAVED_MAX || n >= HUX_WIFI_MAX) {
        return HUX_WIFI_ADD_FULL;
    }
    return src == HUX_WIFI_SRC_USB ? HUX_WIFI_ADD_JOIN : HUX_WIFI_ADD_STAGE;
}

const char *hux_wifi_add_name(hux_wifi_add_t plan) {
    switch (plan) {
        case HUX_WIFI_ADD_JOIN:    return "join";
        case HUX_WIFI_ADD_STAGE:   return "staged";
        case HUX_WIFI_ADD_RESTAGE: return "restaged";
        case HUX_WIFI_ADD_SECOND:  return "second";
        case HUX_WIFI_ADD_SAME:    return "same";
        case HUX_WIFI_ADD_FULL:    return "full";
        case HUX_WIFI_ADD_IN_USE:  return "second_in_use";
        default:                   return "source";
    }
}

hux_wifi_forget_t hux_wifi_forget_plan(const hux_wifi_net_t *known, int n, const char *ssid,
                                       int active, hux_wifi_src_t src) {
    if (src == HUX_WIFI_SRC_DOOR) {
        return HUX_WIFI_FORGET_REFUSED;
    }
    int i = find(known, n, ssid);
    if (i < 0) {
        return HUX_WIFI_FORGET_NOT_KNOWN;
    }
    if (known[i].compiled) {
        return HUX_WIFI_FORGET_COMPILED;
    }
    if (src == HUX_WIFI_SRC_SERVER && i == active) {
        return HUX_WIFI_FORGET_ACTIVE; /* the only way back would be a visit */
    }
    return HUX_WIFI_FORGET_OK;
}

const char *hux_wifi_forget_name(hux_wifi_forget_t plan) {
    switch (plan) {
        case HUX_WIFI_FORGET_OK:        return "ok";
        case HUX_WIFI_FORGET_NOT_KNOWN: return "not_known";
        case HUX_WIFI_FORGET_COMPILED:  return "compiled";
        case HUX_WIFI_FORGET_ACTIVE:    return "active";
        default:                        return "source";
    }
}

void hux_wifi_second(hux_wifi_net_t *e, const char *pass) {
    snprintf(e->pass2, sizeof(e->pass2), "%s", pass);
    e->alt = false;
}

const char *hux_wifi_confirm(hux_wifi_net_t *e) {
    bool staged = e->staged;
    e->staged = false;
    bool second = false;
    if (!e->compiled && e->alt && e->pass2[0] != '\0') {
        memcpy(e->pass, e->pass2, sizeof(e->pass)); /* never an empty first: an older image reads it */
        memset(e->pass2, 0, sizeof(e->pass2));
        second = true;
    }
    if (!e->compiled) {
        e->alt = false;
    }
    bool compiled = e->compiled && e->alt != e->alt_proven;
    if (compiled) {
        e->alt_proven = e->alt;
    }
    if (staged && second) {
        return "staged+second";
    }
    if (staged) {
        return "staged";
    }
    if (second) {
        return "second";
    }
    if (compiled) {
        return e->alt ? "stored" : "compiled";
    }
    return NULL;
}

bool hux_wifi_auth_step(hux_wifi_auth_t *a, uint32_t now_ms, bool auth_fail, bool has_second) {
    if (!auth_fail) {
        a->fails = 0;
        return false;
    }
    a->fails++;
    if (a->fails < HUX_WIFI_AUTH_FAILS || !has_second) {
        return false;
    }
    if (a->flips > 0 && now_ms - a->window_at >= HUX_WIFI_FLIP_EVERY_MS) {
        a->flips = 0; /* a new window */
    }
    if (a->flips >= HUX_WIFI_FLIPS_MAX) {
        return false;
    }
    if (a->flips == 0) {
        a->window_at = now_ms;
    }
    a->flips++;
    a->fails = 0;
    return true;
}

static bool str_ok(const char *s, size_t cap) {
    return memchr(s, 0, cap) != NULL; /* a stored string always ends inside its field */
}

void hux_wifi_merge(hux_wifi_net_t *nets, int *n, const hux_wifi_store_t *st,
                    hux_wifi_blob_t *shadow, int *nshadow) {
    int added = 0;
    *nshadow = 0;
    for (int k = 0; k < st->nsaved && k < HUX_WIFI_SAVED_MAX; k++) {
        const hux_wifi_blob_t *w = &st->saved[k];
        if (!str_ok(w->ssid, sizeof(w->ssid)) || !str_ok(w->pass, sizeof(w->pass)) ||
            w->ssid[0] == '\0') {
            continue;
        }
        if (find(nets, *n, w->ssid) >= 0) {
            if (*nshadow < HUX_WIFI_SAVED_MAX) {
                shadow[(*nshadow)++] = *w; /* compiled in since: kept, not joined twice */
            }
            continue;
        }
        if (*n >= HUX_WIFI_MAX || added >= HUX_WIFI_SAVED_MAX) {
            continue;
        }
        hux_wifi_net_t *e = &nets[(*n)++];
        memset(e, 0, sizeof(*e));
        memcpy(e->ssid, w->ssid, sizeof(e->ssid));
        memcpy(e->pass, w->pass, sizeof(e->pass));
        added++;
    }
    for (int k = 0; k < st->nstaged && k < HUX_WIFI_SAVED_MAX; k++) {
        const hux_wifi_blob_t *w = &st->staged[k];
        if (!str_ok(w->ssid, sizeof(w->ssid)) || !str_ok(w->pass, sizeof(w->pass)) ||
            w->ssid[0] == '\0' || find(nets, *n, w->ssid) >= 0 || *n >= HUX_WIFI_MAX ||
            added >= HUX_WIFI_SAVED_MAX) {
            continue; /* a known name covers it (an older image added it meanwhile) */
        }
        hux_wifi_net_t *e = &nets[(*n)++];
        memset(e, 0, sizeof(*e));
        memcpy(e->ssid, w->ssid, sizeof(e->ssid));
        memcpy(e->pass, w->pass, sizeof(e->pass));
        e->staged = true;
        added++;
    }
    for (int k = 0; k < st->nalts && k < HUX_WIFI_MAX; k++) {
        const hux_wifi_alt_t *p = &st->alts[k];
        if (!str_ok(p->ssid, sizeof(p->ssid)) || !str_ok(p->pass, sizeof(p->pass)) ||
            p->pass[0] == '\0') {
            continue;
        }
        int i = find(nets, *n, p->ssid);
        if (i < 0 || strcmp(nets[i].pass, p->pass) == 0) {
            continue;
        }
        memcpy(nets[i].pass2, p->pass, sizeof(nets[i].pass2));
        nets[i].alt = nets[i].alt_proven = nets[i].compiled && p->prefer != 0;
    }
}

void hux_wifi_split(const hux_wifi_net_t *nets, int n, const hux_wifi_blob_t *shadow, int nshadow,
                    hux_wifi_store_t *st) {
    memset(st, 0, sizeof(*st));
    for (int i = 0; i < n; i++) {
        const hux_wifi_net_t *e = &nets[i];
        if (!e->compiled) {
            hux_wifi_blob_t *w = NULL;
            if (e->staged && st->nstaged < HUX_WIFI_SAVED_MAX) {
                w = &st->staged[st->nstaged++];
            } else if (!e->staged && st->nsaved < HUX_WIFI_SAVED_MAX) {
                w = &st->saved[st->nsaved++];
            }
            if (w != NULL) {
                memcpy(w->ssid, e->ssid, sizeof(w->ssid));
                memcpy(w->pass, e->pass, sizeof(w->pass));
            }
        }
        if (e->pass2[0] != '\0' && st->nalts < HUX_WIFI_MAX) {
            hux_wifi_alt_t *p = &st->alts[st->nalts++];
            memcpy(p->ssid, e->ssid, sizeof(p->ssid));
            memcpy(p->pass, e->pass2, sizeof(p->pass));
            p->prefer = e->compiled && e->alt_proven; /* only what reached the server */
        }
    }
    for (int k = 0; k < nshadow && st->nsaved < HUX_WIFI_SAVED_MAX; k++) {
        st->saved[st->nsaved++] = shadow[k];
    }
}

static int nibble(char c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

int hux_hex_decode(const char *hex, char *out, size_t cap) {
    size_t len = strlen(hex);
    if (len % 2 != 0 || len / 2 + 1 > cap) {
        return -1;
    }
    for (size_t i = 0; i < len / 2; i++) {
        int hi = nibble(hex[2 * i]), lo = nibble(hex[2 * i + 1]);
        if (hi < 0 || lo < 0) {
            return -1;
        }
        out[i] = (char)(hi << 4 | lo);
        if (out[i] == '\0') {
            return -1; /* a NUL inside a name or password */
        }
    }
    out[len / 2] = '\0';
    return (int)(len / 2);
}

#include "hux_link.h"

#include <stdio.h>
#include <string.h>

uint32_t hux_link_backoff_ms(uint32_t attempt, uint32_t r) {
    uint32_t doublings = attempt > 1 ? attempt - 1 : 0;
    uint32_t base = HUX_LINK_BACKOFF_MAX_MS;
    if (doublings < 5) { /* 2 s << 5 is past the cap; no overflow */
        base = (uint32_t)HUX_LINK_BACKOFF_MIN_MS << doublings;
    }
    if (base > HUX_LINK_BACKOFF_MAX_MS) {
        base = HUX_LINK_BACKOFF_MAX_MS;
    }
    if (base == HUX_LINK_BACKOFF_MAX_MS) {
        return base / 100 * (75 + r % 26); /* 45 to 60 s, not mostly 60 */
    }
    uint32_t ms = base / 100 * (75 + r % 51);
    return ms > HUX_LINK_BACKOFF_MAX_MS ? HUX_LINK_BACKOFF_MAX_MS : ms;
}

bool hux_link_uri_ok(const char *uri, size_t max) {
    if (uri == NULL) {
        return false;
    }
    size_t len = strlen(uri);
    if (len == 0 || len >= max) {
        return false;
    }
    const char *s = uri;
    if (strncmp(s, "ws://", 5) == 0) {
        s += 5;
    } else if (strncmp(s, "wss://", 6) == 0) {
        s += 6;
    } else {
        return false;
    }
    const char *host = s;
    while (*s != '\0' && *s != ':' && *s != '/') {
        if (*s == '@' || *s == ' ' || *s == '?' || *s == '#') {
            return false;
        }
        s++;
    }
    if (s == host || *s != ':') {
        return false; /* no host, or no explicit port */
    }
    s++;
    uint32_t port = 0;
    const char *digits = s;
    while (*s >= '0' && *s <= '9') {
        port = port * 10 + (uint32_t)(*s - '0');
        if (port > 65535) {
            return false;
        }
        s++;
    }
    if (s == digits || port == 0 || *s != '/') {
        return false;
    }
    for (; *s != '\0'; s++) {
        if (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') {
            return false;
        }
    }
    return true;
}

hux_ladder_step_t hux_ladder_due(uint32_t lost_s, hux_ladder_step_t done) {
    hux_ladder_step_t due = HUX_LADDER_NONE;
    if (lost_s >= HUX_LADDER_REBOOT_S) {
        due = HUX_LADDER_REBOOT;
    } else if (lost_s >= HUX_LADDER_WIFI_S) {
        due = HUX_LADDER_WIFI;
    } else if (lost_s >= HUX_LADDER_WS_S) {
        due = HUX_LADDER_WS;
    }
    return due > done ? due : HUX_LADDER_NONE;
}

const char *hux_ladder_name(hux_ladder_step_t step) {
    switch (step) {
        case HUX_LADDER_WS:
            return "ws";
        case HUX_LADDER_WIFI:
            return "wifi";
        case HUX_LADDER_REBOOT:
            return "reboot";
        default:
            return "none";
    }
}

bool hux_link_uri_complete(const char *uri, char *out, size_t cap) {
    if (uri == NULL || cap == 0) {
        return false;
    }
    const char *port = NULL;
    size_t skip = 0;
    if (strncmp(uri, "wss://", 6) == 0) {
        port = "443";
        skip = 6;
    } else if (strncmp(uri, "ws://", 5) == 0) {
        port = "80";
        skip = 5;
    } else {
        return false;
    }
    const char *host = uri + skip;
    size_t host_len = strcspn(host, ":/");
    const char *rest = host + host_len; /* ":port/path", "/path" or "" */
    int n;
    if (*rest == ':') {
        n = snprintf(out, cap, "%s%s", uri, strchr(rest, '/') ? "" : "/");
    } else {
        n = snprintf(out, cap, "%.*s:%s%s", (int)(skip + host_len), uri, port,
                     *rest == '/' ? rest : "/");
    }
    return n > 0 && (size_t)n < cap;
}

bool hux_link_http_url(const char *ws_uri, const char *path, char *out, size_t cap) {
    if (ws_uri == NULL || path == NULL || cap == 0) {
        return false;
    }
    int n;
    if (strncmp(path, "http://", 7) == 0 || strncmp(path, "https://", 8) == 0) {
        n = snprintf(out, cap, "%s", path);
        return n > 0 && (size_t)n < cap;
    }
    if (path[0] != '/') {
        return false;
    }
    const char *scheme;
    size_t skip;
    if (strncmp(ws_uri, "wss://", 6) == 0) {
        scheme = "https://";
        skip = 6;
    } else if (strncmp(ws_uri, "ws://", 5) == 0) {
        scheme = "http://";
        skip = 5;
    } else {
        return false;
    }
    const char *authority = ws_uri + skip;
    size_t len = strcspn(authority, "/?#");
    if (len == 0) {
        return false;
    }
    n = snprintf(out, cap, "%s%.*s%s", scheme, (int)len, authority, path);
    return n > 0 && (size_t)n < cap;
}

hux_link_fail_t hux_link_fail_kind(int error_type, int http_status) {
    /* esp_websocket_error_type_t: 1 TCP_TRANSPORT, 2 PONG_TIMEOUT,
     * 3 HANDSHAKE, 4 SERVER_CLOSE. */
    if (http_status != 0 || error_type == 3 || error_type == 4) {
        return HUX_LINK_FAIL_SERVER;
    }
    if (error_type == 1) {
        return HUX_LINK_FAIL_INTERNET;
    }
    return HUX_LINK_FAIL_NONE;
}

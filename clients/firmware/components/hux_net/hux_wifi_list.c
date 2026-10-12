#include "hux_wifi_list.h"

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

/**
 * hux_wifi_list: which known Wi-Fi network to join (F-0027's Wi-Fi list).
 *
 * The walkie knows a few networks: up to 3 compiled in (Mario's home, his
 * phone's hotspot, the home's network once an update carries it) and up
 * to 4 added on site over USB (NVS). This orders them for a join from what
 * a scan heard. Pure, host-tested (tests/test_hux_wifi_list.c).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_WIFI_COMPILED_MAX 3
#define HUX_WIFI_SAVED_MAX    4
#define HUX_WIFI_MAX          (HUX_WIFI_COMPILED_MAX + HUX_WIFI_SAVED_MAX)

typedef struct {
    char ssid[33];
    char pass[65];
    bool compiled;
} hux_wifi_net_t;

typedef struct {
    char ssid[33]; /* empty: a hidden network */
    int8_t rssi;
} hux_wifi_heard_t;

/**
 * The known networks heard by name, for a join, into `order` (indices into
 * `known`): `good` (the one that last reached the server) first, then by
 * signal. Returns how many; 0 when none is heard (the caller stays where it
 * is: never a move to a network not heard). A hidden network is never
 * "heard": it is joined only as `good` at boot.
 */
int hux_wifi_rank(const hux_wifi_net_t *known, int n, const char *good,
                  const hux_wifi_heard_t *heard, int nh, int *order, int cap);

/** Hex to bytes (names and passwords cross the console as hex: spaces).
 * NUL-terminated in `out`; the length, or -1 if not hex, odd, or longer
 * than `cap - 1`. */
int hux_hex_decode(const char *hex, char *out, size_t cap);

#ifdef __cplusplus
}
#endif

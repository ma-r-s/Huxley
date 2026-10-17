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
    char pass[65];     /* the first password: the one believed good */
    bool compiled;
    bool staged;       /* added from afar, not yet reached the server (F-0036) */
    bool alt;          /* joining with pass2 now */
    char pass2[65];    /* a second password for the same name ("" none), F-0036 */
    bool alt_proven;   /* a compiled name: pass2 is the one that last reached the server */
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

/* -- Adding and forgetting from afar (F-0036) ------------------------- */

typedef enum {
    HUX_WIFI_SRC_USB = 0, /* the USB console: the operator is there */
    HUX_WIFI_SRC_DOOR,    /* the LAN developer door: plain HTTP, never a password */
    HUX_WIFI_SRC_SERVER,  /* the server's dev.cmd: wss with the device token */
} hux_wifi_src_t;

typedef enum {
    HUX_WIFI_ADD_JOIN = 0, /* USB, a new name: join now on trial (kept with an IP) */
    HUX_WIFI_ADD_STAGE,    /* the server, a new name: saved staged, tried when heard */
    HUX_WIFI_ADD_RESTAGE,  /* a staged name again: its password replaced, tried again */
    HUX_WIFI_ADD_SECOND,   /* a known name: the password becomes its second one */
    HUX_WIFI_ADD_SAME,     /* a password it already has: nothing to change */
    HUX_WIFI_ADD_FULL,     /* no free slot for a new name */
    HUX_WIFI_ADD_IN_USE,   /* a compiled name already on its stored password: no room */
    HUX_WIFI_ADD_REFUSED,  /* not from this source */
} hux_wifi_add_t;

/** What `wifi addhex <ssid> <pass>` does from `src`. A second password
 * never replaces the first (the one that reached the server): it waits as
 * the second, tried only after HUX_WIFI_AUTH_FAILS wrong-password failures
 * in a row on the first, and swaps in once it reaches the server. */
hux_wifi_add_t hux_wifi_add_plan(const hux_wifi_net_t *known, int n, const char *ssid,
                                 const char *pass, hux_wifi_src_t src);
const char *hux_wifi_add_name(hux_wifi_add_t plan);

/** A known name's new second password (HUX_WIFI_ADD_SECOND): it replaces
 * an untried second, and the first goes back to being tried first (the
 * second only after HUX_WIFI_AUTH_FAILS on it). */
void hux_wifi_second(hux_wifi_net_t *e, const char *pass);

/** The server reached through `e`: a staged entry becomes saved; a saved
 * name's second password that got here becomes its first, and the old
 * first (now known dead) is dropped, so a later switch can't land on it;
 * a compiled name remembers which of its two worked. Returns what changed
 * (for the log; the caller saves), NULL when nothing did. */
const char *hux_wifi_confirm(hux_wifi_net_t *e);

typedef enum {
    HUX_WIFI_FORGET_OK = 0,
    HUX_WIFI_FORGET_NOT_KNOWN,
    HUX_WIFI_FORGET_COMPILED,
    HUX_WIFI_FORGET_ACTIVE,   /* from afar: never the network it is on */
    HUX_WIFI_FORGET_REFUSED,  /* not from this source */
} hux_wifi_forget_t;

hux_wifi_forget_t hux_wifi_forget_plan(const hux_wifi_net_t *known, int n, const char *ssid,
                                       int active, hux_wifi_src_t src);
const char *hux_wifi_forget_name(hux_wifi_forget_t plan);

/* -- The second password: when to try it (F-0036) ---------------------- */

#define HUX_WIFI_AUTH_FAILS    3                       /* in a row: a wrong password */
#define HUX_WIFI_FLIP_EVERY_MS (10u * 60u * 1000u)    /* the switch budget's window */
#define HUX_WIFI_FLIPS_MAX     2                       /* switches per window: there and back */

typedef struct {
    int fails;
    int flips;          /* switches in the window that began at window_at */
    uint32_t window_at;
} hux_wifi_auth_t;

/** One disconnect of the network in use: `auth_fail` for a wrong-password
 * reason (a handshake timeout, an auth failure), else any other reason
 * but a deliberate leave. True when it is time to join with the other
 * password: HUX_WIFI_AUTH_FAILS in a row and a second password known. At
 * most HUX_WIFI_FLIPS_MAX switches per HUX_WIFI_FLIP_EVERY_MS: a weak
 * signal can fail three handshakes on the right password, so the switch
 * back comes at once; past that, two wrong passwords must not hammer an
 * access point that blocks devices. */
bool hux_wifi_auth_step(hux_wifi_auth_t *a, uint32_t now_ms, bool auth_fail, bool has_second);

/* -- Storage the previous image still reads (F-0036) ------------------- */

/* w0..w3: exactly the old saved entry (an image from before F-0036 reads
 * these and only these): reached the server, its first password. */
typedef struct {
    char ssid[33];
    char pass[65];
} hux_wifi_blob_t;

/* s0..s3: staged entries; p0..p6: second passwords, by name. Ignored by an
 * older image, so a rollback never joins a staged network. */
typedef struct {
    char ssid[33];
    char pass[65];
    uint8_t prefer; /* a compiled name: this one reached the server */
    uint8_t reserved[3];
} hux_wifi_alt_t;

typedef struct {
    hux_wifi_blob_t saved[HUX_WIFI_SAVED_MAX];
    int nsaved;
    hux_wifi_blob_t staged[HUX_WIFI_SAVED_MAX];
    int nstaged;
    hux_wifi_alt_t alts[HUX_WIFI_MAX];
    int nalts;
} hux_wifi_store_t;

/** The stored lists onto `nets` (the compiled ones already there, `*n` of
 * them): saved entries a compiled name shadows go to `shadow` (kept for a
 * rollback, not joined); staged ones a known name already covers are
 * dropped; at most HUX_WIFI_SAVED_MAX saved + staged; second passwords
 * attach by name (a name not known: dropped). */
void hux_wifi_merge(hux_wifi_net_t *nets, int *n, const hux_wifi_store_t *st,
                    hux_wifi_blob_t *shadow, int *nshadow);

/** `nets` (and the shadowed entries) as stored. */
void hux_wifi_split(const hux_wifi_net_t *nets, int n, const hux_wifi_blob_t *shadow, int nshadow,
                    hux_wifi_store_t *st);

/** Hex to bytes (names and passwords cross the console as hex: spaces).
 * NUL-terminated in `out`; the length, or -1 if not hex, odd, or longer
 * than `cap - 1`. */
int hux_hex_decode(const char *hex, char *out, size_t cap);

#ifdef __cplusplus
}
#endif

/**
 * hux_link: what the walkie does while it can't reach the server
 * (remote-operation W5, F-0027).
 *
 * Pure (no I/O), host-tested. At the retirement home nobody can restart
 * the walkie, so the firmware climbs a ladder by itself; and through a
 * long outage it must not hammer the network with a TLS handshake every
 * 2 s (about 200 MB a day on a 4G SIM).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define HUX_LINK_BACKOFF_MIN_MS 2000  /* the first retry: a server restart */
#define HUX_LINK_BACKOFF_MAX_MS 60000 /* a long outage: once a minute */

/** Delay before WebSocket attempt `attempt` (1 = the first after the
 * server was lost): 2 s doubling, each times 75 to 125 % from `r` (any
 * random number) so a home's devices don't retry in step; at the cap,
 * 45 to 60 s. */
uint32_t hux_link_backoff_ms(uint32_t attempt, uint32_t r);

/* Where the path to the server breaks (F-0039), from a WebSocket error:
 * a transport error with no HTTP answer (DNS, TCP or TLS to Cloudflare,
 * which always answers when there is internet) is the internet; any HTTP
 * answer (a handshake status: the tunnel's 530, a 401) or the server
 * closing is the server; a pong timeout tells nothing new (NONE: keep the
 * last verdict). The error types are esp_websocket_client's. */
typedef enum {
    HUX_LINK_FAIL_NONE = 0,
    HUX_LINK_FAIL_INTERNET,
    HUX_LINK_FAIL_SERVER,
} hux_link_fail_t;

hux_link_fail_t hux_link_fail_kind(int error_type, int http_status);

typedef enum {
    HUX_LADDER_NONE = 0,
    HUX_LADDER_WS,     /* restart the WebSocket client */
    HUX_LADDER_WIFI,   /* restart Wi-Fi (rejoin, new DHCP) */
    HUX_LADDER_REBOOT, /* restart the whole board */
} hux_ladder_step_t;

#define HUX_LADDER_WS_S     (2 * 60)
#define HUX_LADDER_WIFI_S   (10 * 60)
#define HUX_LADDER_REBOOT_S (60 * 60)

/** The step to take after `lost_s` seconds without the server, when
 * `done` is the last one already taken in this outage (NONE at its
 * start). Each step once per outage, in order; NONE when nothing new is
 * due. A late check takes only the highest step due (a Wi-Fi restart
 * also restarts the WebSocket). */
hux_ladder_step_t hux_ladder_due(uint32_t lost_s, hux_ladder_step_t done);

/** The step's name, for the log. */
const char *hux_ladder_name(hux_ladder_step_t step);

/** True for an address the walkie may switch to and keep: `ws://` or
 * `wss://`, a host, an explicit port (1-65535) and a path starting with
 * `/`, no spaces, shorter than `max` bytes. The WebSocket library keeps
 * the old port or path when a new address leaves one out, and a saved
 * address it can't parse would fail every boot (F-0027 review). */
bool hux_link_uri_ok(const char *uri, size_t max);

/** `uri` with its scheme's port filled in when it has none (443 for
 * `wss://`, 80 for `ws://`) and `/` when it has no path, into `out`;
 * then hux_link_uri_ok decides. False if it doesn't fit or isn't ws/wss.
 * "wss://walkie.example.com" -> "wss://walkie.example.com:443/". */
bool hux_link_uri_complete(const char *uri, char *out, size_t cap);

/** A download path the server sent (`/firmware/<v>.bin`, relative to the
 * server the walkie is using, so a move from the LAN address to the
 * tunnel never breaks it) as a full URL into `out`: `ws://host:port/...`
 * gives `http://host:port<path>`, `wss://` gives `https://`. An absolute
 * http(s) URL is copied as is. False on anything else or if it doesn't
 * fit (F-0028). */
bool hux_link_http_url(const char *ws_uri, const char *path, char *out, size_t cap);

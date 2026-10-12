/**
 * Copy to `secrets.h` (gitignored) and fill in local values.
 *
 * Secrets live in a header, not `sdkconfig`, because sdkconfig is
 * tracked and developer-shared; per-user credentials belong out of
 * source control entirely.
 */
#pragma once

#define HUX_WIFI_SSID     "your-ssid"
#define HUX_WIFI_PASSWORD "your-password"

/* Up to two more networks built in (F-0027's Wi-Fi list), tried when the
 * first isn't heard: a phone hotspot to rescue the walkie on a visit, the
 * place it lives once known (networks added on site over USB, tools/wifi.py,
 * live in NVS until an update compiles them in). Leave them out if unused. */
/* #define HUX_WIFI2_SSID     "a-phone-hotspot" */
/* #define HUX_WIFI2_PASSWORD "its-password" */
/* #define HUX_WIFI3_SSID     "where-it-lives" */
/* #define HUX_WIFI3_PASSWORD "its-password" */

/* Full WebSocket URI: the public one (wss through a tunnel) works from any
 * network; a LAN address only at home. Example for LAN development: */
#define HUX_SERVER_URI    "ws://192.168.1.10:8765/"

/* Developer access over Wi-Fi (components/hux_dev, tools/dev.py): flash,
 * health, console commands, crash dumps. Every request must carry this in
 * the X-Huxley-Token header; anyone on the LAN who has it can flash the
 * device. Make one: openssl rand -hex 16 (16-64 chars; the firmware
 * refuses "change-me" and short ones, and won't build without one). */
#define HUX_DEV_TOKEN     "change-me"

/* The device token: Authorization: Bearer on the WebSocket upgrade and on
 * the /firmware/ downloads (F-0028). The same value as HUXLEY_DEVICE_TOKEN in
 * server/runtime/.env; mint one with
 * python3 -c "import secrets; print(secrets.token_urlsafe(32))" */
#define HUX_DEVICE_TOKEN  "change-me"

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

/* Full WebSocket URI. Example for LAN development: */
#define HUX_SERVER_URI    "ws://192.168.1.10:8765/"

/* Developer access over Wi-Fi (components/hux_dev, tools/dev.py): flash,
 * health, console commands, crash dumps. Every request must carry this in
 * the X-Huxley-Token header; anyone on the LAN who has it can flash the
 * device. Make one: openssl rand -hex 16 (16-64 chars; the firmware
 * refuses "change-me" and short ones, and won't build without one). */
#define HUX_DEV_TOKEN     "change-me"

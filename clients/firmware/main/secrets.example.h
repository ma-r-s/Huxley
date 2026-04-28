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

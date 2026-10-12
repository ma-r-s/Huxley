/**
 * hux_dev: developer access over Wi-Fi, so the walkie never has to come
 * out of its case or onto a USB cable to be updated or looked at.
 *
 * ESP-IDF's HTTP server on port 80; every request needs the X-Huxley-Token
 * header (HUX_DEV_TOKEN in main/secrets.h; without it the server doesn't
 * start). The same shape as Tasmota's command URL:
 *
 *   GET  /info       health as JSON: version, uptime, reset, heap, PSRAM,
 *                    ring, volume, RSSI, running slot and its state
 *   POST /cmd        body = one console line (hux_console.h): `info`,
 *                    `selftest`, `say 0`, `reboot`, `ota <url>`, ...;
 *                    results are log lines, in the Huxley server's log
 *   GET  /coredump   the core dump partition, raw, for
 *                    `idf.py coredump-info -c <file>`
 *
 * Updates are `ota <url>` (hux_ota: esp_https_ota + ESP-IDF app rollback).
 * tools/dev.py drives all of it.
 */
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/** Start the server with `token` (HUX_DEV_TOKEN from main/secrets.h);
 * NULL or empty leaves it off. Call once at boot. */
void hux_dev_start(const char *token);

#ifdef __cplusplus
}
#endif

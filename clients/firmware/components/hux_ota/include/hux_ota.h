/**
 * hux_ota: firmware updates over Wi-Fi, Espressif's way.
 *
 * The download is esp_https_ota, following ESP-IDF's advanced_https_ota
 * example (read the image header first; refuse an image that isn't this
 * project or is the version already running). Plain http:// is allowed
 * (CONFIG_ESP_HTTPS_OTA_ALLOW_HTTP) because images come from a machine on
 * the LAN (tools/dev.py serves them).
 *
 * Rollback is ESP-IDF's app rollback (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE):
 * a new image boots "pending verify" and has to confirm itself; if it
 * crashes or resets first, the bootloader goes back to the previous image.
 * The self-test is "ran HUX_OTA_SELFTEST_S seconds without crashing", not
 * "reached Wi-Fi or the server": an image must not be reverted just
 * because the network was down (why Fleet turned rollback off).
 */
#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_OTA_SELFTEST_S 30

/** Download `url` into the spare slot and boot it (in a task; returns at
 * once). False if an update is already running or `url` is empty. */
bool hux_ota_start(const char *url, bool force);

/** At boot: log the running slot, and if this image is pending verify,
 * confirm it after HUX_OTA_SELFTEST_S seconds. */
void hux_ota_boot_check(void);

/** "ota_0" / "ota_1", and the image state ("valid", "pending", ...). */
const char *hux_ota_running_slot(void);
const char *hux_ota_running_state(void);

#ifdef __cplusplus
}
#endif

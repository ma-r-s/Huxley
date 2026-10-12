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
 * a new image boots "pending verify" and stays so until `ota confirm`
 * arrives; if it crashes or resets first, the bootloader goes back to the
 * previous image. Confirm is accepted only after HUX_OTA_SELFTEST_S of
 * uptime, and it comes from tools/dev.py over the developer door: an
 * image is kept only if it ran 30 s AND can be reached and updated over
 * Wi-Fi again (code review, F-0021). A pending image keeps running on a
 * bad network day; only a restart before the confirm reverts it, so a
 * server outage can't throw away a good image (why Fleet turned rollback
 * off).
 */
#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_OTA_SELFTEST_S 30

/** Download `url` into the spare slot and boot it (in a task; returns at
 * once). False if an update is already running, `url` is empty, or the
 * walkie is busy (playing or listening) and not `force`. `force` also
 * allows the version already running. */
bool hux_ota_start(const char *url, bool force);

/** Keep the running image (it was pending verify). Refused before
 * HUX_OTA_SELFTEST_S of uptime. */
bool hux_ota_confirm(void);

/** Boot the other slot: undo a pending image now, or go back from a
 * confirmed one that turned out bad, if the other slot holds a valid
 * image. Restarts on success. */
bool hux_ota_rollback(void);

/** At boot: log the running slot, its state, and the image a rollback
 * came from. */
void hux_ota_boot_check(void);

/** firmware.status (F-0028): `phase` is downloading, verifying, rebooting,
 * selftest, accepted or failed; `error` names what failed, else NULL. */
typedef void (*hux_ota_status_fn)(const char *version, const char *phase, const char *error);
void hux_ota_set_status_sink(hux_ota_status_fn fn);

/** The silent self-test a pending image runs before it can be kept:
 * true if the walkie works, else false with `*why` naming the first check
 * that failed. Set by main (it reaches every part). */
typedef bool (*hux_ota_selftest_fn)(const char **why);
void hux_ota_set_selftest(hux_ota_selftest_fn fn);

/** server_event firmware.update (F-0028): download `url` (full http(s)),
 * which must carry `version`, with `Authorization: Bearer <token>`, and
 * boot it. Same refusals as hux_ota_start; firmware.status on every step. */
bool hux_ota_pull(const char *url, const char *version, bool force, const char *token);

/** After each hello: a pending image starts its checks (once a boot):
 * the self-test after 20 s, then the manifest GET with `Authorization:
 * Bearer <token>` (the device token, as for the image). */
void hux_ota_after_hello(const char *token);

/** server_event ota.accept: keep the running image if `version` is it and
 * its self-test passed, the manifest matched and the server's close was
 * followed by a reconnect; else firmware.status failed with the reason. */
void hux_ota_accept(const char *version);

/** "ota_0" / "ota_1", and the image state ("valid", "pending", ...). */
const char *hux_ota_running_slot(void);
const char *hux_ota_running_state(void);

/** The image the bootloader rejected, as "<slot> <version>" (a rollback:
 * it crashed before confirming itself), or "" if none. Stays until that
 * slot is written again. */
const char *hux_ota_last_invalid(void);

#ifdef __cplusplus
}
#endif

/**
 * hux_ota_policy: when the walkie goes back to its previous image by
 * itself, and what it tells the server about a rollback (F-0023, F-0028's
 * rollback bookkeeping). Pure, host-tested (tests/test_hux_ota_policy.c);
 * hux_ota keeps the state in RTC memory and NVS and acts on it.
 *
 * A pending image (just updated, not yet accepted) goes back by itself on
 * any reset: the bootloader does it. An accepted image used to crash-loop
 * for good (one that crashed on every press, say). Now: crash resets
 * (panic, a watchdog) under 10 min after boot on the same image, three in
 * a row, send it back to the other slot. A brownout (a low cell) neither
 * counts nor breaks the run; any other reset (power on, a software
 * restart, an update) breaks it.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_CRASH_EARLY_S        600u /* a crash this soon after boot counts */
#define HUX_CRASH_ROLLBACK_AFTER 3u
#define HUX_CRASH_MAGIC          0x43525348u /* 'CRSH' */

typedef enum {
    HUX_RESET_COLD = 0, /* power on: RTC memory is garbage anyway */
    HUX_RESET_CRASH,    /* a panic, the interrupt or task watchdog (not esptool's reset) */
    HUX_RESET_BROWNOUT,
    HUX_RESET_OTHER,    /* a software restart (an update, the ladder), a deep sleep, USB */
} hux_reset_class_t;

/* Kept in RTC memory across resets (not a power cut). */
typedef struct {
    uint32_t magic;
    uint32_t image;    /* which image the count is for (the first ELF sha bytes) */
    uint32_t early;    /* early crashes in a row on it */
    uint32_t uptime_s; /* last written uptime: how long the boot that ended lasted */
} hux_crash_rtc_t;

/** At boot: the run of early crashes on `image`, updated from the reset
 * that ended the last boot. The uptime is reset for this boot. */
uint32_t hux_crash_boot(hux_crash_rtc_t *r, uint32_t image, hux_reset_class_t reset);

/** Why the bootloader took a pending image back, as the hello's
 * `rollback.reason`: its self-test failed (it said so before), it crashed
 * (this boot's reset), else it never got the server's accept (no_server).
 * A crash loop's own rollback and the console's `ota rollback` record
 * "crash" and "manual" themselves. */
const char *hux_rollback_reason(bool selftest_failed, hux_reset_class_t reset);

#ifdef __cplusplus
}
#endif

/**
 * hux_wifi_retry: how long to wait before Wi-Fi reconnect attempt N.
 *
 * Pure (no I/O), host-tested. The walkie never stops trying: a router
 * reboot takes a minute or two, and a device that gave up would stay
 * offline until someone power-cycled it, which a blind user can't
 * diagnose.
 */
#pragma once

#include <stdint.h>

#define HUX_WIFI_FAST_RETRIES   5      /* back to back, for a blip */
#define HUX_WIFI_RETRY_MAX_MS   30000  /* then doubling from 1 s up to this */

/** Delay before reconnect attempt `attempt` (1 = the first after a
 * disconnect): 0 for the first HUX_WIFI_FAST_RETRIES, then 1 s, 2 s,
 * 4 s, ... capped at HUX_WIFI_RETRY_MAX_MS, forever. */
uint32_t hux_wifi_retry_delay_ms(uint32_t attempt);

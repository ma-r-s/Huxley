#include "hux_wifi_retry.h"

uint32_t hux_wifi_retry_delay_ms(uint32_t attempt) {
    if (attempt <= HUX_WIFI_FAST_RETRIES) {
        return 0;
    }
    uint32_t slow = attempt - HUX_WIFI_FAST_RETRIES - 1; /* 0, 1, 2, ... */
    if (slow >= 15) {
        return HUX_WIFI_RETRY_MAX_MS; /* 1000 << 15 is past the cap; no overflow */
    }
    uint32_t ms = 1000u << slow;
    return ms > HUX_WIFI_RETRY_MAX_MS ? HUX_WIFI_RETRY_MAX_MS : ms;
}

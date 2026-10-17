#include "hux_ota_policy.h"

#include <string.h>

uint32_t hux_crash_boot(hux_crash_rtc_t *r, uint32_t image, hux_reset_class_t reset) {
    if (r->magic != HUX_CRASH_MAGIC || reset == HUX_RESET_COLD) {
        memset(r, 0, sizeof(*r));
        r->magic = HUX_CRASH_MAGIC;
    }
    bool same = r->image == image;
    if (reset == HUX_RESET_CRASH && same && r->uptime_s < HUX_CRASH_EARLY_S) {
        r->early++;
    } else if (!(reset == HUX_RESET_BROWNOUT && same)) {
        /* Another image crashed (a pending one the bootloader took back):
         * not this one's crash. */
        r->early = 0;
    }
    r->image = image;
    r->uptime_s = 0;
    return r->early;
}

const char *hux_rollback_reason(bool selftest_failed, hux_reset_class_t reset) {
    if (selftest_failed) {
        return "selftest";
    }
    return reset == HUX_RESET_CRASH ? "crash" : "no_server";
}

/**
 * Host unit tests for `hux_ota_policy`: going back to the previous image
 * after a crash loop (F-0023), and the rollback reason the hello reports.
 */
#include <string.h>

#include "test_helpers.h"

#include "hux_ota_policy.h"

#define IMG_A 0xa1a1a1a1u
#define IMG_B 0xb2b2b2b2u

static uint32_t crash_after(hux_crash_rtc_t *r, uint32_t up_s) {
    r->uptime_s = up_s;
    return hux_crash_boot(r, IMG_A, HUX_RESET_CRASH);
}

static void test_three_early_crashes(void) {
    hux_crash_rtc_t r;
    memset(&r, 0xee, sizeof(r)); /* a power cut leaves garbage */
    HX_ASSERT_EQ(hux_crash_boot(&r, IMG_A, HUX_RESET_COLD), 0, "power on: nothing counted");
    HX_ASSERT_EQ(crash_after(&r, 90), 1, "a crash 90 s in");
    HX_ASSERT_EQ(crash_after(&r, 300), 2, "another, 5 min in");
    HX_ASSERT_EQ(crash_after(&r, 599), HUX_CRASH_ROLLBACK_AFTER, "a third: time to go back");
}

static void test_what_breaks_the_run(void) {
    hux_crash_rtc_t r = {0};
    hux_crash_boot(&r, IMG_A, HUX_RESET_COLD);
    crash_after(&r, 60);
    crash_after(&r, 60);
    HX_ASSERT_EQ(crash_after(&r, 600), 0, "a boot that lasted 10 min: not a loop");
    crash_after(&r, 60);
    crash_after(&r, 60);
    r.uptime_s = 30;
    HX_ASSERT_EQ(hux_crash_boot(&r, IMG_A, HUX_RESET_BROWNOUT), 2,
                 "a brownout (a low cell): neither counts nor breaks it");
    HX_ASSERT_EQ(crash_after(&r, 60), 3, "so the next crash is the third");
    crash_after(&r, 60);
    r.uptime_s = 20;
    HX_ASSERT_EQ(hux_crash_boot(&r, IMG_A, HUX_RESET_OTHER), 0,
                 "a software restart (an update, the ladder) breaks it");
    crash_after(&r, 60);
    r.uptime_s = 20;
    HX_ASSERT_EQ(hux_crash_boot(&r, IMG_A, HUX_RESET_COLD), 0, "a power cut too");
}

static void test_counts_belong_to_one_image(void) {
    hux_crash_rtc_t r = {0};
    hux_crash_boot(&r, IMG_A, HUX_RESET_COLD);
    crash_after(&r, 60);
    crash_after(&r, 60);
    r.uptime_s = 60;
    HX_ASSERT_EQ(hux_crash_boot(&r, IMG_B, HUX_RESET_CRASH), 0,
                 "another image crashed (a pending one taken back): not this one's crash");
    r.uptime_s = 60;
    HX_ASSERT_EQ(hux_crash_boot(&r, IMG_B, HUX_RESET_CRASH), 1, "its own count starts here");
    r.uptime_s = 30;
    HX_ASSERT_EQ(hux_crash_boot(&r, IMG_A, HUX_RESET_BROWNOUT), 0,
                 "a brownout across images: a fresh count");
    HX_ASSERT_EQ(r.uptime_s, 0u, "each boot's uptime starts at 0");
}

static void test_rollback_reason(void) {
    HX_ASSERT_TRUE(strcmp(hux_rollback_reason(true, HUX_RESET_CRASH), "selftest") == 0,
                   "its self-test said so first");
    HX_ASSERT_TRUE(strcmp(hux_rollback_reason(false, HUX_RESET_CRASH), "crash") == 0,
                   "the pending image crashed");
    HX_ASSERT_TRUE(strcmp(hux_rollback_reason(false, HUX_RESET_OTHER), "no_server") == 0,
                   "restarted without the accept (the ladder, an outage)");
}

int main(void) {
    HX_RUN(test_three_early_crashes);
    HX_RUN(test_what_breaks_the_run);
    HX_RUN(test_counts_belong_to_one_image);
    HX_RUN(test_rollback_reason);
    HX_SUMMARY();
}

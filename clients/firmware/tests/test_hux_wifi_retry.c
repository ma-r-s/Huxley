/**
 * Host unit tests for `hux_wifi_retry`: the walkie must keep trying to
 * reach Wi-Fi forever. The old code gave up after five tries, so a
 * router reboot left the device offline until someone power-cycled it.
 */
#include "test_helpers.h"

#include "hux_wifi_retry.h"

static void test_first_retries_are_immediate(void) {
    for (uint32_t a = 1; a <= HUX_WIFI_FAST_RETRIES; a++) {
        HX_ASSERT_EQ(hux_wifi_retry_delay_ms(a), 0u, "a blip reconnects at once");
    }
}

static void test_then_doubles_from_one_second(void) {
    HX_ASSERT_EQ(hux_wifi_retry_delay_ms(6), 1000u, "first slow retry: 1 s");
    HX_ASSERT_EQ(hux_wifi_retry_delay_ms(7), 2000u, "2 s");
    HX_ASSERT_EQ(hux_wifi_retry_delay_ms(8), 4000u, "4 s");
    HX_ASSERT_EQ(hux_wifi_retry_delay_ms(9), 8000u, "8 s");
    HX_ASSERT_EQ(hux_wifi_retry_delay_ms(10), 16000u, "16 s");
    HX_ASSERT_EQ(hux_wifi_retry_delay_ms(11), 30000u, "capped at 30 s");
}

static void test_never_gives_up(void) {
    /* Hours of a dead router, and past every shift that could overflow. */
    const uint32_t attempts[] = {12, 20, 21, 36, 37, 100, 1000, 100000, UINT32_MAX};
    for (size_t i = 0; i < sizeof(attempts) / sizeof(attempts[0]); i++) {
        uint32_t d = hux_wifi_retry_delay_ms(attempts[i]);
        HX_ASSERT_EQ(d, (uint32_t)HUX_WIFI_RETRY_MAX_MS, "still retrying every 30 s");
    }
}

static void test_never_longer_than_the_cap(void) {
    for (uint32_t a = 1; a < 200; a++) {
        HX_ASSERT_TRUE(hux_wifi_retry_delay_ms(a) <= HUX_WIFI_RETRY_MAX_MS, "bounded");
    }
}

int main(void) {
    HX_RUN(test_first_retries_are_immediate);
    HX_RUN(test_then_doubles_from_one_second);
    HX_RUN(test_never_gives_up);
    HX_RUN(test_never_longer_than_the_cap);
    HX_SUMMARY();
}

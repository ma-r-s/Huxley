/**
 * Host unit tests for `hux_link`: the backoff and the escalation ladder
 * the walkie climbs alone while it can't reach the server (F-0027).
 */
#include <string.h>

#include "test_helpers.h"

#include "hux_link.h"

static void test_backoff_starts_at_two_seconds(void) {
    /* r % 51 == 25 is the middle of the jitter: exactly the base. */
    HX_ASSERT_EQ(hux_link_backoff_ms(1, 25), 2000u, "a server restart: back in 2 s");
    HX_ASSERT_EQ(hux_link_backoff_ms(0, 25), 2000u, "attempt 0 reads as the first");
}

static void test_backoff_doubles_to_a_minute(void) {
    HX_ASSERT_EQ(hux_link_backoff_ms(2, 25), 4000u, "4 s");
    HX_ASSERT_EQ(hux_link_backoff_ms(3, 25), 8000u, "8 s");
    HX_ASSERT_EQ(hux_link_backoff_ms(4, 25), 16000u, "16 s");
    HX_ASSERT_EQ(hux_link_backoff_ms(5, 25), 32000u, "32 s");
    HX_ASSERT_EQ(hux_link_backoff_ms(6, 25), 60000u, "capped at 60 s");
}

static void test_backoff_jitter_bounds(void) {
    HX_ASSERT_EQ(hux_link_backoff_ms(1, 0), 1500u, "75 % of 2 s");
    HX_ASSERT_EQ(hux_link_backoff_ms(1, 50), 2500u, "125 % of 2 s");
    HX_ASSERT_EQ(hux_link_backoff_ms(7, 0), 45000u, "75 % of the cap");
    HX_ASSERT_EQ(hux_link_backoff_ms(7, 25), 60000u, "the cap itself");
    HX_ASSERT_EQ(hux_link_backoff_ms(7, 26), 45000u, "at the cap, spread over 45-60 s");
    HX_ASSERT_EQ(hux_link_backoff_ms(5, 50), 40000u, "below the cap: 125 % of 32 s");
}

static void test_backoff_never_overflows(void) {
    const uint32_t attempts[] = {30, 31, 32, 33, 1000, UINT32_MAX};
    for (size_t i = 0; i < sizeof(attempts) / sizeof(attempts[0]); i++) {
        for (uint32_t r = 0; r < 200; r++) {
            uint32_t d = hux_link_backoff_ms(attempts[i], r * 7919u);
            HX_ASSERT_TRUE(d >= 45000u && d <= 60000u, "a long outage: once a minute");
        }
    }
}

static void test_backoff_at_the_cap_is_spread(void) {
    uint32_t at_cap = 0;
    for (uint32_t r = 0; r < 260; r++) {
        uint32_t d = hux_link_backoff_ms(9, r);
        HX_ASSERT_TRUE(d >= 45000u && d <= 60000u, "45 to 60 s");
        at_cap += d == 60000u;
    }
    HX_ASSERT_TRUE(at_cap <= 10, "not mostly clamped to 60 s");
}

static void test_uri_ok(void) {
    HX_ASSERT_TRUE(hux_link_uri_ok("ws://192.168.68.71:8765/", 128), "the LAN address");
    HX_ASSERT_TRUE(hux_link_uri_ok("wss://huxley.example.com:443/ws?x=1", 128), "a tunnel");
    HX_ASSERT_TRUE(!hux_link_uri_ok("192.168.68.90:8765/", 128), "no scheme: the brick case");
    HX_ASSERT_TRUE(!hux_link_uri_ok("ws://192.168.68.71/", 128), "no port: the old one stays");
    HX_ASSERT_TRUE(!hux_link_uri_ok("ws://192.168.68.71:8765", 128), "no path: the old one stays");
    HX_ASSERT_TRUE(!hux_link_uri_ok("ws://:8765/", 128), "no host");
    HX_ASSERT_TRUE(!hux_link_uri_ok("ws://h:0/", 128), "port 0");
    HX_ASSERT_TRUE(!hux_link_uri_ok("ws://h:65536/", 128), "port too big");
    HX_ASSERT_TRUE(!hux_link_uri_ok("ws://h:99999999999/", 128), "no overflow");
    HX_ASSERT_TRUE(!hux_link_uri_ok("ws://h:80x/", 128), "port not digits");
    HX_ASSERT_TRUE(!hux_link_uri_ok("ws://user@h:80/", 128), "no credentials");
    HX_ASSERT_TRUE(!hux_link_uri_ok("ws://h:80/a b", 128), "no spaces");
    HX_ASSERT_TRUE(!hux_link_uri_ok("http://h:80/", 128), "only ws and wss");
    HX_ASSERT_TRUE(!hux_link_uri_ok("", 128), "empty");
    HX_ASSERT_TRUE(!hux_link_uri_ok(NULL, 128), "null");
    HX_ASSERT_TRUE(!hux_link_uri_ok("ws://h:80/abcdef", 16), "too long for the buffer");
    HX_ASSERT_TRUE(hux_link_uri_ok("ws://h:80/abcd", 16), "just fits");
}

static void test_uri_complete(void) {
    char out[128];
    HX_ASSERT_TRUE(hux_link_uri_complete("wss://walkie.example.com/", out, sizeof(out)), "wss");
    HX_ASSERT_TRUE(strcmp(out, "wss://walkie.example.com:443/") == 0, "443 filled in");
    HX_ASSERT_TRUE(hux_link_uri_complete("wss://walkie.example.com", out, sizeof(out)), "no path");
    HX_ASSERT_TRUE(strcmp(out, "wss://walkie.example.com:443/") == 0, "443 and / filled in");
    HX_ASSERT_TRUE(hux_link_uri_complete("ws://192.168.68.75:8765/", out, sizeof(out)), "lan");
    HX_ASSERT_TRUE(strcmp(out, "ws://192.168.68.75:8765/") == 0, "a full one is unchanged");
    HX_ASSERT_TRUE(hux_link_uri_complete("ws://h:8765", out, sizeof(out)), "port, no path");
    HX_ASSERT_TRUE(strcmp(out, "ws://h:8765/") == 0, "/ added");
    HX_ASSERT_TRUE(hux_link_uri_complete("ws://h/ws?x=1", out, sizeof(out)), "path, no port");
    HX_ASSERT_TRUE(strcmp(out, "ws://h:80/ws?x=1") == 0, "80 before the path");
    HX_ASSERT_TRUE(hux_link_uri_ok(out, sizeof(out)), "and it then passes the check");
    HX_ASSERT_TRUE(!hux_link_uri_complete("http://h/", out, sizeof(out)), "only ws and wss");
    HX_ASSERT_TRUE(!hux_link_uri_complete("wss://a-very-long-host.example.com/", out, 20), "too long");
}

static void test_ladder_climbs_in_order(void) {
    HX_ASSERT_EQ(hux_ladder_due(0, HUX_LADDER_NONE), HUX_LADDER_NONE, "just lost: wait");
    HX_ASSERT_EQ(hux_ladder_due(119, HUX_LADDER_NONE), HUX_LADDER_NONE, "under 2 min");
    HX_ASSERT_EQ(hux_ladder_due(120, HUX_LADDER_NONE), HUX_LADDER_WS, "2 min: the WebSocket");
    HX_ASSERT_EQ(hux_ladder_due(599, HUX_LADDER_WS), HUX_LADDER_NONE, "WS done, under 10");
    HX_ASSERT_EQ(hux_ladder_due(600, HUX_LADDER_WS), HUX_LADDER_WIFI, "10 min: Wi-Fi");
    HX_ASSERT_EQ(hux_ladder_due(3599, HUX_LADDER_WIFI), HUX_LADDER_NONE, "under an hour");
    HX_ASSERT_EQ(hux_ladder_due(3600, HUX_LADDER_WIFI), HUX_LADDER_REBOOT, "60 min: reboot");
}

static void test_ladder_each_step_once(void) {
    HX_ASSERT_EQ(hux_ladder_due(200, HUX_LADDER_WS), HUX_LADDER_NONE, "WS not again");
    HX_ASSERT_EQ(hux_ladder_due(900, HUX_LADDER_WIFI), HUX_LADDER_NONE, "Wi-Fi not again");
    HX_ASSERT_EQ(hux_ladder_due(7200, HUX_LADDER_REBOOT), HUX_LADDER_NONE, "reboot once");
}

static void test_ladder_late_check_takes_the_highest(void) {
    HX_ASSERT_EQ(hux_ladder_due(700, HUX_LADDER_NONE), HUX_LADDER_WIFI, "skips WS");
    HX_ASSERT_EQ(hux_ladder_due(4000, HUX_LADDER_NONE), HUX_LADDER_REBOOT, "skips to reboot");
    HX_ASSERT_EQ(hux_ladder_due(UINT32_MAX, HUX_LADDER_WS), HUX_LADDER_REBOOT, "no overflow");
}

static void test_ladder_names(void) {
    HX_ASSERT_TRUE(hux_ladder_name(HUX_LADDER_WS)[0] == 'w', "ws");
    HX_ASSERT_TRUE(hux_ladder_name(HUX_LADDER_REBOOT)[0] == 'r', "reboot");
    HX_ASSERT_TRUE(hux_ladder_name((hux_ladder_step_t)99)[0] == 'n', "unknown: none");
}

int main(void) {
    HX_RUN(test_backoff_starts_at_two_seconds);
    HX_RUN(test_backoff_doubles_to_a_minute);
    HX_RUN(test_backoff_jitter_bounds);
    HX_RUN(test_backoff_never_overflows);
    HX_RUN(test_backoff_at_the_cap_is_spread);
    HX_RUN(test_uri_ok);
    HX_RUN(test_uri_complete);
    HX_RUN(test_ladder_climbs_in_order);
    HX_RUN(test_ladder_each_step_once);
    HX_RUN(test_ladder_late_check_takes_the_highest);
    HX_RUN(test_ladder_names);
    HX_SUMMARY();
}

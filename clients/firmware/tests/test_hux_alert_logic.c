/**
 * Host unit tests for `hux_alert_logic` (F-0032): when the walkie tells
 * Mario's phone it can't reach the server, and that it's fixed.
 */
#include <string.h>

#include "test_helpers.h"

#include "hux_alert_logic.h"

#define MIN  (60u * 1000u)
/* 2026-10-13 00:00 UTC; AT(h, m) is that day's h:m in Bogota (UTC-5). */
#define DAY  1791849600LL
#define AT(h, m) (DAY + ((h) + 5) * 3600LL + (m) * 60LL)

static void test_down_after_eleven_minutes_with_an_ip(void) {
    hux_alert_state_t s = {0};
    HX_ASSERT_EQ(hux_alert_step(&s, AT(15, 0), 1000, true, false, 9 * MIN), HUX_ALERT_NONE,
                 "9 min: a restart or a deploy, not yet");
    HX_ASSERT_EQ(hux_alert_step(&s, AT(15, 2), 2000, true, false, 11 * MIN), HUX_ALERT_SEND_DOWN,
                 "11 min: needs you");
    char hhmm[6];
    hux_alert_hhmm(s.down_since, hhmm);
    HX_ASSERT_TRUE(strcmp(hhmm, "14:51") == 0, "since when it was lost (first seen at 9 min)");
}

static void test_never_without_an_ip(void) {
    hux_alert_state_t s = {0};
    HX_ASSERT_EQ(hux_alert_step(&s, AT(15, 0), 1000, false, false, 30 * MIN), HUX_ALERT_NONE,
                 "no IP: it can't send (the page covers that case)");
}

static void test_once_then_fixed_once(void) {
    hux_alert_state_t s = {0};
    hux_alert_action_t a = hux_alert_step(&s, AT(15, 0), 1000, true, false, 11 * MIN);
    hux_alert_sent(&s, a, true, 1000);
    for (uint32_t t = 2; t < 50; t++) {
        HX_ASSERT_EQ(hux_alert_step(&s, AT(15, (int)t), t * 1000, true, false, (11 + t) * MIN),
                     HUX_ALERT_NONE, "never repeated while still down");
    }
    a = hux_alert_step(&s, AT(16, 0), 60000, true, true, 0);
    HX_ASSERT_EQ(a, HUX_ALERT_SEND_FIXED, "back: fixed");
    hux_alert_sent(&s, a, true, 60000);
    HX_ASSERT_EQ(hux_alert_step(&s, AT(16, 1), 61000, true, true, 0), HUX_ALERT_NONE, "said once");
    HX_ASSERT_EQ(s.down_sent, 0u, "cleared");
}

static void test_back_before_the_alert_says_nothing(void) {
    hux_alert_state_t s = {0};
    hux_alert_step(&s, AT(15, 0), 1000, true, false, 5 * MIN);
    HX_ASSERT_EQ(hux_alert_step(&s, AT(15, 1), 2000, true, true, 0), HUX_ALERT_NONE,
                 "a short outage: nothing at all");
    HX_ASSERT_TRUE(s.down_since == 0, "forgotten");
}

static void test_quiet_hours_hold_until_seven(void) {
    hux_alert_state_t s = {0};
    HX_ASSERT_EQ(hux_alert_step(&s, AT(23, 0), 1000, true, false, 30 * MIN), HUX_ALERT_NONE,
                 "23:00: held");
    HX_ASSERT_EQ(hux_alert_step(&s, AT(6, 59) + 86400, 2000, true, false, 500 * MIN),
                 HUX_ALERT_NONE, "06:59: still held");
    HX_ASSERT_EQ(hux_alert_step(&s, AT(7, 0) + 86400, 3000, true, false, 500 * MIN),
                 HUX_ALERT_SEND_DOWN, "07:00: sent");
    HX_ASSERT_TRUE(hux_alert_quiet(AT(22, 0)) && !hux_alert_quiet(AT(21, 59)), "from 22:00");
}

static void test_fixed_in_quiet_hours_goes_at_once(void) {
    hux_alert_state_t s = {0};
    hux_alert_action_t a = hux_alert_step(&s, AT(21, 0), 1000, true, false, 11 * MIN);
    hux_alert_sent(&s, a, true, 1000);
    HX_ASSERT_EQ(hux_alert_step(&s, AT(23, 0), 2000, true, true, 0), HUX_ALERT_SEND_FIXED,
                 "fixed at 23:00: sent (silent), not a stale alarm till morning");
    HX_ASSERT_TRUE(hux_alert_quiet(AT(23, 0)), "the push decides silent from the hour");
    char hhmm[6];
    hux_alert_hhmm(s.fixed_at, hhmm);
    HX_ASSERT_TRUE(strcmp(hhmm, "23:00") == 0, "says when it came back");
}

static void test_a_failed_send_waits_two_minutes_then_longer(void) {
    hux_alert_state_t s = {0};
    hux_alert_action_t a = hux_alert_step(&s, AT(15, 0), 1000, true, false, 11 * MIN);
    hux_alert_sent(&s, a, false, 1000);
    HX_ASSERT_EQ(hux_alert_step(&s, AT(15, 1), 1000 + 119000, true, false, 12 * MIN),
                 HUX_ALERT_NONE, "1:59 later: not yet");
    a = hux_alert_step(&s, AT(15, 2), 1000 + 120000, true, false, 13 * MIN);
    HX_ASSERT_EQ(a, HUX_ALERT_SEND_DOWN, "2:00 later: again");
    hux_alert_sent(&s, a, false, 121000);
    HX_ASSERT_EQ(hux_alert_step(&s, AT(15, 5), 121000 + 239000, true, false, 16 * MIN),
                 HUX_ALERT_NONE, "then 4 min");
    for (int i = 0; i < 8; i++) {
        hux_alert_sent(&s, HUX_ALERT_SEND_DOWN, false, 0);
    }
    HX_ASSERT_EQ(s.retry_ms, HUX_ALERT_RETRY_MAX_MS, "never more than 30 min");
}

static void test_uptime_wrap(void) {
    hux_alert_state_t s = {0};
    uint32_t near_wrap = 0xFFFFFFFFu - 30000u;
    hux_alert_sent(&s, HUX_ALERT_SEND_DOWN, false, near_wrap);
    HX_ASSERT_EQ(hux_alert_step(&s, AT(15, 0), near_wrap + 60000u, true, false, 20 * MIN),
                 HUX_ALERT_NONE, "1 min after a failure, across the wrap: waiting");
    HX_ASSERT_EQ(hux_alert_step(&s, AT(15, 3), near_wrap + 121000u, true, false, 22 * MIN),
                 HUX_ALERT_SEND_DOWN, "2 min after, across the wrap: retried");
    hux_alert_state_t t = {0};
    HX_ASSERT_EQ(hux_alert_step(&t, AT(15, 0), 25u * 86400u * 1000u, true, false, 11 * MIN),
                 HUX_ALERT_SEND_DOWN, "25 days up, no failure pending: sent");
}

static void test_no_clock_no_quiet_hours(void) {
    hux_alert_state_t s = {0};
    HX_ASSERT_EQ(hux_alert_step(&s, 1000, 1000, true, false, 11 * MIN), HUX_ALERT_SEND_DOWN,
                 "unsynced: sent rather than held on a guess");
    char hhmm[6];
    hux_alert_hhmm(0, hhmm);
    HX_ASSERT_TRUE(strcmp(hhmm, "?") == 0, "no time to show");
}

int main(void) {
    HX_RUN(test_down_after_eleven_minutes_with_an_ip);
    HX_RUN(test_never_without_an_ip);
    HX_RUN(test_once_then_fixed_once);
    HX_RUN(test_back_before_the_alert_says_nothing);
    HX_RUN(test_quiet_hours_hold_until_seven);
    HX_RUN(test_fixed_in_quiet_hours_goes_at_once);
    HX_RUN(test_a_failed_send_waits_two_minutes_then_longer);
    HX_RUN(test_uptime_wrap);
    HX_RUN(test_no_clock_no_quiet_hours);
    HX_SUMMARY();
}

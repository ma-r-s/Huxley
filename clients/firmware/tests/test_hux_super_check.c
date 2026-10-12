/**
 * Host unit tests for `hux_super_check`: the supervisor's stall decision
 * (F-0027). A stall is work waiting and none finished, never an idle part.
 */
#include "test_helpers.h"

#include "hux_super_check.h"

static hux_super_part_t parts[2];

static void setup(void) {
    parts[0] = (hux_super_part_t){.name = "decoder", .limit_ms = 10000, .enforced = true};
    parts[1] = (hux_super_part_t){.name = "speaker", .limit_ms = 10000, .enforced = true};
    hux_super_begin(parts, 2, 0);
}

static void test_progress_is_healthy(void) {
    setup();
    uint32_t age = 0;
    for (uint32_t t = 1000; t <= 60000; t += 1000) {
        parts[0].done++;
        parts[0].pending = 3;
        parts[1].done++;
        parts[1].pending = 1;
        HX_ASSERT_EQ(hux_super_check(parts, 2, t, &age), -1, "working parts never stall");
    }
}

static void test_idle_is_healthy(void) {
    setup();
    uint32_t age = 0;
    parts[1].pending = 0; /* nothing to do: not a stall however long */
    for (uint32_t t = 1000; t <= 600000; t += 1000) {
        parts[0].pending = 0;
        HX_ASSERT_EQ(hux_super_check(parts, 2, t, &age), -1, "an idle decoder is fine");
    }
}

static void test_work_waiting_none_done_stalls(void) {
    setup();
    uint32_t age = 0;
    parts[1].pending = 0;
    parts[0].pending = 5; /* packets queued, decoder wedged */
    HX_ASSERT_EQ(hux_super_check(parts, 2, 9999, &age), -1, "under the limit");
    HX_ASSERT_EQ(hux_super_check(parts, 2, 10000, &age), 0, "the decoder stalled");
    HX_ASSERT_EQ(age, 10000u, "for 10 s");
    HX_ASSERT_EQ(hux_super_check(parts, 2, 11000, &age), -1, "returned once, not every check");
}

static void test_the_clock_starts_when_work_arrives(void) {
    setup();
    uint32_t age = 0;
    parts[1].pending = 0;
    parts[0].pending = 0;
    HX_ASSERT_EQ(hux_super_check(parts, 2, 50000, &age), -1, "idle for 50 s");
    parts[0].pending = 2; /* work arrives at 50 s */
    HX_ASSERT_EQ(hux_super_check(parts, 2, 55000, &age), -1, "5 s with work: not yet");
    HX_ASSERT_EQ(hux_super_check(parts, 2, 60000, &age), 0, "10 s with work: stalled");
}

static void test_recovery_rearms(void) {
    setup();
    uint32_t age = 0;
    parts[1].pending = 0;
    parts[0].pending = 1;
    HX_ASSERT_EQ(hux_super_check(parts, 2, 10000, &age), 0, "stalled");
    parts[0].done++;
    HX_ASSERT_EQ(hux_super_check(parts, 2, 11000, &age), -1, "progressed");
    HX_ASSERT_EQ(hux_super_check(parts, 2, 21000, &age), 0, "a second stall is reported");
}

static void test_counter_wrap_and_clock_wrap(void) {
    setup();
    uint32_t age = 0;
    parts[1].pending = 0;
    parts[0].done = UINT32_MAX;
    parts[0].pending = 1;
    hux_super_begin(parts, 2, UINT32_MAX - 2000); /* the ms clock about to wrap */
    parts[0].done++;                              /* wraps to 0: still progress */
    HX_ASSERT_EQ(hux_super_check(parts, 2, UINT32_MAX - 1000, &age), -1, "wrap counts");
    HX_ASSERT_EQ(hux_super_check(parts, 2, 5000, &age), -1, "6 s across the clock wrap");
    HX_ASSERT_EQ(hux_super_check(parts, 2, 9000, &age), 0, "10 s across the wrap");
}

static void test_first_stall_wins_the_rest_later(void) {
    setup();
    uint32_t age = 0;
    parts[0].pending = 1;
    parts[1].pending = 1;
    HX_ASSERT_EQ(hux_super_check(parts, 2, 10000, &age), 0, "the first part first");
    HX_ASSERT_EQ(hux_super_check(parts, 2, 10500, &age), 1, "then the other");
}

int main(void) {
    HX_RUN(test_progress_is_healthy);
    HX_RUN(test_idle_is_healthy);
    HX_RUN(test_work_waiting_none_done_stalls);
    HX_RUN(test_the_clock_starts_when_work_arrives);
    HX_RUN(test_recovery_rearms);
    HX_RUN(test_counter_wrap_and_clock_wrap);
    HX_RUN(test_first_stall_wins_the_rest_later);
    HX_SUMMARY();
}

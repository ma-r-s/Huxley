/**
 * Host unit tests for `hux_debounce`: the talk button. A missed release
 * leaves the mic open; a bounce that leaks through starts a turn the
 * user did not ask for.
 */
#include "test_helpers.h"

#include "hux_debounce.h"

#define N 2 /* hux_button's DEBOUNCE_SAMPLES */

static hux_edge_t feed(hux_debounce_t *d, const int *raw, int count) {
    hux_edge_t last = HUX_EDGE_NONE;
    for (int i = 0; i < count; i++) {
        hux_edge_t e = hux_debounce_step(d, raw[i] != 0, N);
        if (e != HUX_EDGE_NONE) {
            last = e;
        }
    }
    return last;
}

static void test_press_needs_two_samples(void) {
    hux_debounce_t d = {0};
    HX_ASSERT_EQ(hux_debounce_step(&d, true, N), HUX_EDGE_NONE, "first low sample: not yet");
    HX_ASSERT_EQ(hux_debounce_step(&d, true, N), HUX_EDGE_PRESS, "second: press");
    HX_ASSERT_TRUE(d.pressed, "state is pressed");
    HX_ASSERT_EQ(hux_debounce_step(&d, true, N), HUX_EDGE_NONE, "held: no repeat");
}

static void test_release_needs_two_samples(void) {
    hux_debounce_t d = {.pressed = true};
    HX_ASSERT_EQ(hux_debounce_step(&d, false, N), HUX_EDGE_NONE, "first high sample: not yet");
    HX_ASSERT_EQ(hux_debounce_step(&d, false, N), HUX_EDGE_RELEASE, "second: release");
    HX_ASSERT_TRUE(!d.pressed, "state is released");
}

static void test_bounce_never_accumulates(void) {
    hux_debounce_t d = {0};
    const int bounce[] = {1, 0, 1, 0, 1, 0, 1, 0};
    HX_ASSERT_EQ(feed(&d, bounce, 8), HUX_EDGE_NONE, "alternating samples never press");
    HX_ASSERT_TRUE(!d.pressed, "still released");
}

static void test_one_press_per_hold_and_one_release(void) {
    hux_debounce_t d = {0};
    const int hold[] = {1, 1, 1, 1, 0, 1, 1, 1, 0, 0, 0};
    int presses = 0, releases = 0;
    for (int i = 0; i < 11; i++) {
        hux_edge_t e = hux_debounce_step(&d, hold[i] != 0, N);
        presses += e == HUX_EDGE_PRESS;
        releases += e == HUX_EDGE_RELEASE;
    }
    HX_ASSERT_EQ(presses, 1, "a single-sample dropout mid-hold is not a release + press");
    HX_ASSERT_EQ(releases, 1, "letting go releases once");
}

int main(void) {
    HX_RUN(test_press_needs_two_samples);
    HX_RUN(test_release_needs_two_samples);
    HX_RUN(test_bounce_never_accumulates);
    HX_RUN(test_one_press_per_hold_and_one_release);
    HX_SUMMARY();
}

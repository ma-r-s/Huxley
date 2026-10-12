/**
 * Host unit tests for `hux_battery_level`: the cell's voltage as a
 * percentage, and the smoothing that keeps it from flickering (F-0025).
 */
#include "test_helpers.h"

#include "hux_battery_level.h"

static void test_curve_points(void) {
    HX_ASSERT_EQ(hux_battery_pct(4200), 100, "full and above");
    HX_ASSERT_EQ(hux_battery_pct(4150), 100, "full");
    HX_ASSERT_EQ(hux_battery_pct(3790), 50, "half");
    HX_ASSERT_EQ(hux_battery_pct(3670), 20, "the first warning");
    HX_ASSERT_EQ(hux_battery_pct(3610), 10, "the second warning");
    HX_ASSERT_EQ(hux_battery_pct(3300), 0, "empty");
    HX_ASSERT_EQ(hux_battery_pct(2500), 0, "below empty");
}

static void test_between_points_and_monotonic(void) {
    HX_ASSERT_EQ(hux_battery_pct(3770), 45, "halfway between 3750 and 3790");
    int last = -1;
    for (int mv = 3000; mv <= 4300; mv += 5) {
        int p = hux_battery_pct(mv);
        HX_ASSERT_TRUE(p >= last && p >= 0 && p <= 100, "never goes down as voltage rises");
        last = p;
    }
}

static void test_smoothing(void) {
    HX_ASSERT_EQ(hux_battery_smooth(-1, 63), 63, "the first reading as is");
    HX_ASSERT_EQ(hux_battery_smooth(63, 61), 61, "a fall at once");
    HX_ASSERT_EQ(hux_battery_smooth(61, 63), 61, "a small rise ignored (a sag recovering)");
    HX_ASSERT_EQ(hux_battery_smooth(61, 66), 66, "5 points up: charging");
}

int main(void) {
    HX_RUN(test_curve_points);
    HX_RUN(test_between_points_and_monotonic);
    HX_RUN(test_smoothing);
    HX_SUMMARY();
}

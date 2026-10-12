/**
 * Host unit tests for `hux_battery_cutoff`: when the walkie parks itself
 * above the cells' own protection (F-0030), and every reading that must
 * never park it.
 */
#include "test_helpers.h"

#include "hux_battery_cutoff.h"

#define MIN (60 * 1000)

static void test_a_resting_cell_under_3400_parks_after_ten_minutes_in_the_zone(void) {
    hux_cutoff_t c;
    hux_cutoff_reset(&c);
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3600, true, true, 0), HUX_CUTOFF_NONE, "enters the zone");
    HX_ASSERT_TRUE(hux_cutoff_in_zone(&c), "in the 10 % zone: read every 2 min");
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3390, true, true, 9 * MIN), HUX_CUTOFF_NONE,
                 "under 3.40 V but only 9 min in the zone");
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3390, true, true, 10 * MIN), HUX_CUTOFF_QUIET, "parks");
}

static void test_a_sudden_drop_is_not_believed(void) {
    hux_cutoff_t c;
    hux_cutoff_reset(&c);
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3800, true, true, 0), HUX_CUTOFF_NONE, "half full");
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3300, true, true, 2 * MIN), HUX_CUTOFF_NONE,
                 "a fall from half to empty in one step: a glitch until it stays");
}

static void test_broken_readings_never_count(void) {
    hux_cutoff_t c;
    hux_cutoff_reset(&c);
    hux_cutoff_step(&c, 3600, true, true, 0);
    HX_ASSERT_EQ(hux_cutoff_step(&c, 0, true, true, 30 * MIN), HUX_CUTOFF_NONE,
                 "a cracked blob reads 0 V");
    HX_ASSERT_EQ(hux_cutoff_step(&c, 2800, true, true, 31 * MIN), HUX_CUTOFF_NONE,
                 "under 2.9 V: not believable");
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3300, true, false, 32 * MIN), HUX_CUTOFF_NONE,
                 "calibration out of 5 %");
    HX_ASSERT_TRUE(hux_cutoff_in_zone(&c), "a broken reading leaves the state alone");
}

static void test_a_long_book_parks_on_three_loaded_readings(void) {
    hux_cutoff_t c;
    hux_cutoff_reset(&c);
    hux_cutoff_step(&c, 3500, false, true, 0);
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3240, false, true, 10 * MIN), HUX_CUTOFF_NONE, "one");
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3230, false, true, 10 * MIN + 20000), HUX_CUTOFF_NONE,
                 "20 s later: the same sag, not a second reading");
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3230, false, true, 11 * MIN), HUX_CUTOFF_NONE, "two");
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3220, false, true, 12 * MIN), HUX_CUTOFF_LOADED, "three");
}

static void test_loaded_count_restarts(void) {
    hux_cutoff_t c;
    hux_cutoff_reset(&c);
    hux_cutoff_step(&c, 3500, false, true, 0);
    hux_cutoff_step(&c, 3240, false, true, 10 * MIN);
    hux_cutoff_step(&c, 3240, false, true, 11 * MIN);
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3300, false, true, 12 * MIN), HUX_CUTOFF_NONE,
                 "a loaded reading above 3.25 V");
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3240, false, true, 13 * MIN), HUX_CUTOFF_NONE,
                 "counting starts over");
    hux_cutoff_step(&c, 3240, false, true, 14 * MIN);
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3450, true, true, 15 * MIN), HUX_CUTOFF_NONE,
                 "a resting reading above 3.40 V clears it too");
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3240, false, true, 16 * MIN), HUX_CUTOFF_NONE, "one again");
}

static void test_charging_leaves_the_zone(void) {
    hux_cutoff_t c;
    hux_cutoff_reset(&c);
    hux_cutoff_step(&c, 3600, true, true, 0);
    hux_cutoff_step(&c, 3700, true, true, 5 * MIN);
    HX_ASSERT_TRUE(!hux_cutoff_in_zone(&c), "out of the zone");
    HX_ASSERT_EQ(hux_cutoff_step(&c, 3390, true, true, 11 * MIN), HUX_CUTOFF_NONE,
                 "the 10 min start again");
}

static void test_boot_and_resume(void) {
    HX_ASSERT_TRUE(hux_cutoff_park_at_boot(true, 0, false), "the running firmware decided");
    HX_ASSERT_TRUE(hux_cutoff_park_at_boot(false, 3440, true), "a resting boot under 3.45 V");
    HX_ASSERT_TRUE(!hux_cutoff_park_at_boot(false, 3460, true), "above it");
    HX_ASSERT_TRUE(!hux_cutoff_park_at_boot(false, 0, true), "never on a broken reading");
    HX_ASSERT_TRUE(!hux_cutoff_park_at_boot(false, 3300, false), "never with a bad calibration");
    HX_ASSERT_TRUE(!hux_cutoff_resume(3590, true), "parked until 3.60 V");
    HX_ASSERT_TRUE(hux_cutoff_resume(3600, true), "a charger brings it back");
    HX_ASSERT_TRUE(hux_cutoff_resume(0, true), "a broken reading never keeps it parked");
    HX_ASSERT_TRUE(hux_cutoff_resume(3300, false), "nor a bad calibration");
}

int main(void) {
    HX_RUN(test_a_resting_cell_under_3400_parks_after_ten_minutes_in_the_zone);
    HX_RUN(test_a_sudden_drop_is_not_believed);
    HX_RUN(test_broken_readings_never_count);
    HX_RUN(test_a_long_book_parks_on_three_loaded_readings);
    HX_RUN(test_loaded_count_restarts);
    HX_RUN(test_charging_leaves_the_zone);
    HX_RUN(test_boot_and_resume);
    HX_SUMMARY();
}

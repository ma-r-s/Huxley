/**
 * Host unit tests for `hux_blackbox`: the ring in RTC memory that keeps
 * the last warnings across a reset (F-0027).
 */
#include <stdio.h>
#include <string.h>

#include "test_helpers.h"

#include "hux_blackbox.h"

static hux_blackbox_t box;
static char out[HUX_BLACKBOX_BYTES + 1];

static void put(const char *s) {
    hux_blackbox_put(&box, s, strlen(s));
}

static void test_empty(void) {
    hux_blackbox_reset(&box);
    uint32_t kept = 99;
    HX_ASSERT_TRUE(hux_blackbox_valid(&box), "a reset box is valid");
    HX_ASSERT_EQ(hux_blackbox_read(&box, out, sizeof(out), &kept), 0u, "nothing held");
    HX_ASSERT_EQ(kept, 0u, "no lines");
}

static void test_lines_in_order(void) {
    hux_blackbox_reset(&box);
    put("1 W hux_net: ladder.lost why=closed");
    put("2 E hux_app: boom");
    uint32_t kept = 0;
    size_t n = hux_blackbox_read(&box, out, sizeof(out), &kept);
    HX_ASSERT_EQ(kept, 2u, "two lines");
    HX_ASSERT_TRUE(strcmp(out, "1 W hux_net: ladder.lost why=closed\n2 E hux_app: boom\n") == 0,
                   "oldest first, newline after each");
    HX_ASSERT_EQ(n, strlen(out), "length matches");
}

static void test_wrap_keeps_the_newest_whole_lines(void) {
    hux_blackbox_reset(&box);
    char line[64];
    for (int i = 0; i < 1000; i++) { /* ~20 KB through a 4 KB box */
        snprintf(line, sizeof(line), "%06d W tag: line number %d", i, i);
        put(line);
    }
    uint32_t kept = 0;
    size_t n = hux_blackbox_read(&box, out, sizeof(out), &kept);
    HX_ASSERT_TRUE(kept > 50 && kept < 1000, "the newest ~4 KB of lines");
    HX_ASSERT_TRUE(n <= HUX_BLACKBOX_BYTES, "fits");
    HX_ASSERT_TRUE(strncmp(out + n - 33, "000999 W tag: line number 999\n", 31) == 0 ||
                       strstr(out, "000999 W tag: line number 999\n") != NULL,
                   "ends with the last line");
    /* Every kept line is whole: starts with six digits and a space. */
    const char *s = out;
    uint32_t seen = 0;
    while (*s) {
        HX_ASSERT_TRUE(s[6] == ' ' && s[7] == 'W', "a whole line, not a cut one");
        s = strchr(s, '\n') + 1;
        seen++;
    }
    HX_ASSERT_EQ(seen, kept, "kept counts the lines copied");
    HX_ASSERT_EQ(box.lines, 1000u, "every put counted: dropped = lines - kept");
}

static void test_small_out_buffer_never_cuts_a_line(void) {
    hux_blackbox_reset(&box);
    put("aaaa");
    put("bbbb");
    char small[8];
    uint32_t kept = 0;
    size_t n = hux_blackbox_read(&box, small, sizeof(small), &kept);
    HX_ASSERT_EQ(n, 5u, "only the first whole line fits");
    HX_ASSERT_EQ(kept, 1u, "one line");
    HX_ASSERT_TRUE(strcmp(small, "aaaa\n") == 0, "no partial line");
}

static void test_garbage_is_not_valid(void) {
    memset(&box, 0x5A, sizeof(box)); /* what a power cut leaves */
    HX_ASSERT_TRUE(!hux_blackbox_valid(&box), "random RTC memory is rejected");
    hux_blackbox_reset(&box);
    put("x");
    box.used = HUX_BLACKBOX_BYTES + 1;
    HX_ASSERT_TRUE(!hux_blackbox_valid(&box), "out-of-range header rejected");
    hux_blackbox_reset(&box);
    put("x");
    box.head ^= 1; /* a header torn by a reset mid-update */
    HX_ASSERT_TRUE(!hux_blackbox_valid(&box), "checksum catches a torn header");
}

static void test_newline_inside_a_line_and_a_huge_line(void) {
    hux_blackbox_reset(&box);
    put("two\nparts");
    uint32_t kept = 0;
    hux_blackbox_read(&box, out, sizeof(out), &kept);
    HX_ASSERT_EQ(kept, 1u, "still one line");
    static char huge[HUX_BLACKBOX_BYTES * 2];
    memset(huge, 'h', sizeof(huge) - 1);
    huge[sizeof(huge) - 2] = 'Z';
    huge[sizeof(huge) - 1] = '\0';
    put(huge);
    HX_ASSERT_TRUE(hux_blackbox_valid(&box), "still valid");
    HX_ASSERT_EQ(box.used, (uint32_t)HUX_BLACKBOX_BYTES, "full");
}

int main(void) {
    HX_RUN(test_empty);
    HX_RUN(test_lines_in_order);
    HX_RUN(test_wrap_keeps_the_newest_whole_lines);
    HX_RUN(test_small_out_buffer_never_cuts_a_line);
    HX_RUN(test_garbage_is_not_valid);
    HX_RUN(test_newline_inside_a_line_and_a_huge_line);
    HX_SUMMARY();
}

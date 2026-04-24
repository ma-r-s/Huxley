/**
 * Minimal test-helper macros. Intentionally not a full framework —
 * no suite fixtures, no parametrisation, no fancy output. If you
 * need more than this, we've probably outgrown this file and should
 * pull in Unity or cmocka.
 *
 * Usage:
 *   static void test_something(void) {
 *       HX_ASSERT_TRUE(condition, "why");
 *       HX_ASSERT_EQ(a, b, "why");
 *   }
 *   int main(void) {
 *       HX_RUN(test_something);
 *       HX_SUMMARY();
 *   }
 *
 * Exit code 0 on all-pass, 1 on any failure.
 */
#pragma once

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int hx_tests_run = 0;
static int hx_tests_failed = 0;
static int hx_current_failed = 0;

#define HX_ASSERT_TRUE(cond, msg)                                          \
    do {                                                                   \
        hx_tests_run++;                                                    \
        if (!(cond)) {                                                     \
            fprintf(stderr, "    FAIL %s:%d  %s\n", __FILE__, __LINE__, (msg)); \
            hx_tests_failed++;                                             \
            hx_current_failed++;                                           \
        }                                                                  \
    } while (0)

#define HX_ASSERT_EQ(actual, expected, msg) \
    HX_ASSERT_TRUE((actual) == (expected), (msg))

#define HX_ASSERT_STREQ(actual, expected, msg) \
    HX_ASSERT_TRUE((actual) != NULL && (expected) != NULL && strcmp((actual), (expected)) == 0, (msg))

#define HX_RUN(fn)                                                         \
    do {                                                                   \
        hx_current_failed = 0;                                             \
        fn();                                                              \
        printf("  [%s] %s\n", hx_current_failed == 0 ? " ok " : "FAIL", #fn); \
    } while (0)

#define HX_SUMMARY()                                                       \
    do {                                                                   \
        printf("\n%d tests, %d failures\n", hx_tests_run, hx_tests_failed); \
        return hx_tests_failed == 0 ? 0 : 1;                               \
    } while (0)

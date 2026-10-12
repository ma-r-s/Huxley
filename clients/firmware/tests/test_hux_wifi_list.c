/**
 * Host unit tests for `hux_wifi_list`: which known network the walkie joins
 * from what a scan heard (F-0027's Wi-Fi list), and the hex that carries
 * names with spaces over the console.
 */
#include <string.h>

#include "test_helpers.h"

#include "hux_wifi_list.h"

/* Home (compiled), a phone hotspot (compiled), the place's network (saved). */
static const hux_wifi_net_t KNOWN[] = {
    {"Casa", "x", true},
    {"Telefono", "x", true},
    {"Hogar 2G", "x", false},
};
#define N 3

static void test_heard_good_first_then_signal(void) {
    hux_wifi_heard_t heard[] = {{"Casa", -40}, {"Hogar 2G", -70}, {"Vecino", -30}};
    int order[8];
    int n = hux_wifi_rank(KNOWN, N, "Hogar 2G", heard, 3, order, 8);
    HX_ASSERT_EQ(n, 2, "only the known networks heard");
    HX_ASSERT_EQ(order[0], 2, "good first, though weaker");
    HX_ASSERT_EQ(order[1], 0, "then the other heard");
    n = hux_wifi_rank(KNOWN, N, "", heard, 3, order, 8);
    HX_ASSERT_EQ(order[0], 0, "no good: the strongest");
}

static void test_good_not_heard(void) {
    hux_wifi_heard_t heard[] = {{"Telefono", -60}, {"Casa", -50}};
    int order[8];
    int n = hux_wifi_rank(KNOWN, N, "Hogar 2G", heard, 2, order, 8);
    HX_ASSERT_EQ(n, 2, "never a network not heard when others are");
    HX_ASSERT_EQ(order[0], 0, "strongest first");
    HX_ASSERT_EQ(order[1], 1, "then the hotspot");
}

static void test_nothing_heard(void) {
    hux_wifi_heard_t heard[] = {{"Vecino", -30}};
    int order[8];
    HX_ASSERT_EQ(hux_wifi_rank(KNOWN, N, "Hogar 2G", heard, 1, order, 8), 0,
                 "none heard by name: stay (never a network not heard)");
    HX_ASSERT_EQ(hux_wifi_rank(KNOWN, N, "", NULL, 0, order, 8), 0, "a failed scan: stay");
}

static void test_hidden_network_never_counts(void) {
    hux_wifi_heard_t heard[] = {{"Telefono", -60}, {"", -55}};
    int order[8];
    int n = hux_wifi_rank(KNOWN, N, "Hogar 2G", heard, 2, order, 8);
    HX_ASSERT_EQ(n, 1, "only the one heard by name");
    HX_ASSERT_EQ(order[0], 1, "the hotspot");
}

static void test_cap(void) {
    hux_wifi_heard_t heard[] = {{"Casa", -40}, {"Hogar 2G", -70}, {"Telefono", -50}};
    int order[1];
    HX_ASSERT_EQ(hux_wifi_rank(KNOWN, N, "", heard, 3, order, 1), 1, "never more than cap");
}

static void test_hex(void) {
    char out[33];
    HX_ASSERT_EQ(hux_hex_decode("4361736120417a756c", out, sizeof(out)), 9, "a name");
    HX_ASSERT_TRUE(strcmp(out, "Casa Azul") == 0, "with its space");
    HX_ASSERT_EQ(hux_hex_decode("4A4b", out, sizeof(out)), 2, "either case");
    HX_ASSERT_EQ(hux_hex_decode("466", out, sizeof(out)), -1, "odd length");
    HX_ASSERT_EQ(hux_hex_decode("4g", out, sizeof(out)), -1, "not hex");
    HX_ASSERT_EQ(hux_hex_decode("4100", out, sizeof(out)), -1, "a NUL inside");
    char small[3];
    HX_ASSERT_EQ(hux_hex_decode("414243", small, sizeof(small)), -1, "too long for the buffer");
    HX_ASSERT_EQ(hux_hex_decode("4142", small, sizeof(small)), 2, "just fits");
}

int main(void) {
    HX_RUN(test_heard_good_first_then_signal);
    HX_RUN(test_good_not_heard);
    HX_RUN(test_nothing_heard);
    HX_RUN(test_hidden_network_never_counts);
    HX_RUN(test_cap);
    HX_RUN(test_hex);
    HX_SUMMARY();
}

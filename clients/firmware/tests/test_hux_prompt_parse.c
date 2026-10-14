/**
 * Host unit tests for `hux_prompt_parse`: reading the prompt files the
 * walkie says on its own (F-0027), and refusing a bad one whole.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "test_helpers.h"

#include "hux_prompt_parse.h"

static void test_reads_packets_in_order(void) {
    const uint8_t f[] = {'H', 'X', 'P', '1', 2, 0, 0xAA, 0xBB, 1, 0, 0xCC};
    hux_prompt_iter_t it;
    HX_ASSERT_TRUE(hux_prompt_iter_init(&it, f, sizeof(f)), "magic accepted");
    const uint8_t *pkt;
    size_t n;
    HX_ASSERT_EQ(hux_prompt_iter_next(&it, &pkt, &n), 1, "first packet");
    HX_ASSERT_EQ((int)n, 2, "first length");
    HX_ASSERT_EQ(pkt[0], 0xAA, "first byte");
    HX_ASSERT_EQ(hux_prompt_iter_next(&it, &pkt, &n), 1, "second packet");
    HX_ASSERT_EQ((int)n, 1, "second length");
    HX_ASSERT_EQ(pkt[0], 0xCC, "second byte");
    HX_ASSERT_EQ(hux_prompt_iter_next(&it, &pkt, &n), 0, "clean end");
    HX_ASSERT_EQ(hux_prompt_count(f, sizeof(f)), 2, "count");
}

static void test_refuses_bad_files(void) {
    const uint8_t wrong_magic[] = {'H', 'X', 'C', 'L', 1, 0, 0};
    HX_ASSERT_EQ(hux_prompt_count(wrong_magic, sizeof(wrong_magic)), -1, "wrong magic");
    HX_ASSERT_EQ(hux_prompt_count(NULL, 0), -1, "nothing");
    HX_ASSERT_EQ(hux_prompt_count((const uint8_t *)"HXP", 3), -1, "shorter than the magic");
    const uint8_t cut_packet[] = {'H', 'X', 'P', '1', 3, 0, 1, 2};
    HX_ASSERT_EQ(hux_prompt_count(cut_packet, sizeof(cut_packet)), -1, "a packet cut short");
    const uint8_t cut_length[] = {'H', 'X', 'P', '1', 1, 0, 9, 7};
    HX_ASSERT_EQ(hux_prompt_count(cut_length, sizeof(cut_length)), -1, "a length cut short");
    const uint8_t empty_packet[] = {'H', 'X', 'P', '1', 0, 0};
    HX_ASSERT_EQ(hux_prompt_count(empty_packet, sizeof(empty_packet)), -1, "an empty packet");
    uint8_t big[4 + 2 + 1501];
    memset(big, 0, sizeof(big));
    memcpy(big, "HXP1", 4);
    big[4] = 1501 & 0xFF;
    big[5] = 1501 >> 8;
    HX_ASSERT_EQ(hux_prompt_count(big, sizeof(big)), -1, "a packet over 1500 bytes");
    big[4] = 1500 & 0xFF;
    big[5] = 1500 >> 8;
    HX_ASSERT_EQ(hux_prompt_count(big, sizeof(big) - 1), 1, "1500 bytes is the limit");
}

static void test_magic_only_is_zero_packets(void) {
    HX_ASSERT_EQ(hux_prompt_count((const uint8_t *)"HXP1", 4), 0, "nothing to play");
}

static int shipped_packets(const char *name) {
    char path[512];
    snprintf(path, sizeof(path), "%s/%s.hxp", HUX_PROMPTS_FILES, name);
    FILE *f = fopen(path, "rb");
    HX_ASSERT_TRUE(f != NULL, path);
    if (f == NULL) {
        return -1;
    }
    static uint8_t buf[64 * 1024];
    size_t len = fread(buf, 1, sizeof(buf), f);
    fclose(f);
    return hux_prompt_count(buf, len);
}

/* The files the firmware embeds: each one whole, and short enough to say
 * while the walkie waits (a bad render must fail here, not on the walkie). */
static void test_shipped_prompts_parse(void) {
    const char *names[] = {"offline", "battery_empty", "updating"};
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        int packets = shipped_packets(names[i]);
        HX_ASSERT_TRUE(packets > 10 && packets * 60 <= 6000, names[i]);
    }
}

/* The connection sounds (F-0033): whole, and short (a tick every 3 s must
 * leave room to talk). */
static void test_shipped_sounds_parse(void) {
    const char *names[] = {"startup", "searching", "connected", "lost"};
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        int packets = shipped_packets(names[i]);
        HX_ASSERT_TRUE(packets > 0 && packets * 60 <= 2000, names[i]);
    }
    HX_ASSERT_TRUE(shipped_packets("searching") * 60 <= 600, "the tick is short");
}

int main(void) {
    HX_RUN(test_reads_packets_in_order);
    HX_RUN(test_refuses_bad_files);
    HX_RUN(test_magic_only_is_zero_packets);
    HX_RUN(test_shipped_prompts_parse);
    HX_RUN(test_shipped_sounds_parse);
    HX_SUMMARY();
}

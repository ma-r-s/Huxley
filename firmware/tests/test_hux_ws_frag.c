/**
 * Host unit tests for `hux_ws_frag`. Reassembly is single-threaded,
 * input-range-sensitive, and silent data loss is the worst failure
 * mode — exactly the shape this file is here to prevent.
 */
#include "test_helpers.h"

#include "hux_ws_frag.h"

#include <stdint.h>
#include <string.h>

#define OP_TEXT   0x01
#define OP_BINARY 0x02
#define OP_PING   0x09

static char scratch[128];

static hux_ws_reassembler_t make(void) {
    hux_ws_reassembler_t r;
    hux_ws_reassembler_init(&r, scratch, sizeof(scratch));
    return r;
}

/* ---- happy paths --------------------------------------------------- */

static void test_single_frame_passes_through(void) {
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;

    const char *payload = "{\"type\":\"hello\",\"protocol\":2}";
    int pl = (int)strlen(payload);

    hux_frag_result_t rc =
        hux_ws_reassemble(&r, OP_TEXT, 0, pl, pl, payload, &out, &len);

    HX_ASSERT_EQ(rc, HUX_FRAG_READY, "single-frame returns READY");
    HX_ASSERT_TRUE(out == payload, "single-frame zero-copies from data_ptr");
    HX_ASSERT_EQ((int)len, pl, "length matches payload_len");
    HX_ASSERT_EQ(r.expected, (size_t)0, "reassembler idle after single frame");
}

static void test_two_fragment_reassembly(void) {
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;

    const char *first = "hello ";
    const char *second = "world";
    int total = 11;

    HX_ASSERT_EQ(
        hux_ws_reassemble(&r, OP_TEXT, 0, 6, total, first, &out, &len),
        HUX_FRAG_NEED_MORE, "first fragment -> NEED_MORE");

    HX_ASSERT_EQ(
        hux_ws_reassemble(&r, OP_TEXT, 6, 5, total, second, &out, &len),
        HUX_FRAG_READY, "second fragment -> READY");

    HX_ASSERT_EQ((int)len, total, "assembled length");
    HX_ASSERT_TRUE(memcmp(out, "hello world", (size_t)total) == 0,
                   "bytes round-trip verbatim");
    HX_ASSERT_EQ(r.expected, (size_t)0, "reassembler idle after completion");
}

static void test_three_fragment_reassembly(void) {
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;

    const char *a = "abc";
    const char *b = "def";
    const char *c = "ghi";
    int total = 9;

    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 0, 3, total, a, &out, &len),
                 HUX_FRAG_NEED_MORE, "frag 1");
    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 3, 3, total, b, &out, &len),
                 HUX_FRAG_NEED_MORE, "frag 2");
    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 6, 3, total, c, &out, &len),
                 HUX_FRAG_READY, "frag 3 -> READY");
    HX_ASSERT_TRUE(memcmp(out, "abcdefghi", 9) == 0, "all three bytes assembled");
}

static void test_back_to_back_messages(void) {
    /* One reassembler, two sequential messages. State must reset
     * between them so the second starts clean. */
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;

    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 0, 3, 6, "foo", &out, &len),
                 HUX_FRAG_NEED_MORE, "m1 frag 1");
    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 3, 3, 6, "bar", &out, &len),
                 HUX_FRAG_READY, "m1 frag 2 ready");
    HX_ASSERT_TRUE(memcmp(out, "foobar", 6) == 0, "m1 bytes");

    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 0, 3, 3, "hey", &out, &len),
                 HUX_FRAG_READY, "m2 single-frame after m1 completed");
    HX_ASSERT_TRUE(memcmp(out, "hey", 3) == 0, "m2 bytes");
}

/* ---- failure modes ------------------------------------------------- */

static void test_oversized_message_dropped(void) {
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;

    int too_big = (int)sizeof(scratch) + 1;
    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 0, 16, too_big,
                                   "............", &out, &len),
                 HUX_FRAG_DROPPED, "payload_len > buf_size drops");
    HX_ASSERT_EQ(r.expected, (size_t)0, "dropped reset to idle");
}

static void test_continuation_without_start_dropped(void) {
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;

    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 5, 3, 8, "xxx", &out, &len),
                 HUX_FRAG_DROPPED, "continuation without start -> DROPPED");
}

static void test_out_of_order_fragment_dropped(void) {
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;

    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 0, 3, 9, "abc", &out, &len),
                 HUX_FRAG_NEED_MORE, "start");
    /* Expected next offset is 3; feeding 6 is a gap. */
    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 6, 3, 9, "ghi", &out, &len),
                 HUX_FRAG_DROPPED, "gap -> DROPPED");
    HX_ASSERT_EQ(r.expected, (size_t)0, "reset on drop");
}

static void test_binary_opcode_dropped(void) {
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;

    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_BINARY, 0, 3, 3, "bin", &out, &len),
                 HUX_FRAG_DROPPED, "binary frames dropped");
}

static void test_control_opcode_dropped_without_clobbering_state(void) {
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;

    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 0, 3, 9, "abc", &out, &len),
                 HUX_FRAG_NEED_MORE, "mid-message");

    /* A stray PING between fragments should NOT reset the reassembler
     * (it's a data-frame continuation boundary, not part of the
     * message). We return DROPPED to the caller but expected/written
     * are preserved. */
    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_PING, 0, 0, 0, NULL, &out, &len),
                 HUX_FRAG_DROPPED, "PING -> DROPPED but state preserved");
    HX_ASSERT_EQ(r.expected, (size_t)9, "expected retained");
    HX_ASSERT_EQ(r.written, (size_t)3, "written retained");

    /* Continuation picks up where we left off. */
    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 3, 6, 9, "defghi", &out, &len),
                 HUX_FRAG_READY, "continuation after interleaved PING");
    HX_ASSERT_TRUE(memcmp(out, "abcdefghi", 9) == 0, "assembled bytes");
}

static void test_fragment_past_payload_end_dropped(void) {
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;

    /* offset 5 + data_len 10 = 15, > payload_len 8 */
    HX_ASSERT_EQ(
        hux_ws_reassemble(&r, OP_TEXT, 5, 10, 8, "................", &out, &len),
        HUX_FRAG_DROPPED, "offset+len > payload_len dropped");
}

static void test_zero_length_fragment_dropped(void) {
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;
    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 0, 0, 0, "", &out, &len),
                 HUX_FRAG_DROPPED, "zero-length rejected");
}

static void test_null_data_ptr_dropped(void) {
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;
    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 0, 4, 4, NULL, &out, &len),
                 HUX_FRAG_DROPPED, "NULL data_ptr rejected");
}

static void test_reset_api(void) {
    hux_ws_reassembler_t r = make();
    const char *out = NULL;
    size_t len = 0;

    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 0, 3, 9, "abc", &out, &len),
                 HUX_FRAG_NEED_MORE, "start");
    hux_ws_reassembler_reset(&r);
    HX_ASSERT_EQ(r.expected, (size_t)0, "reset clears expected");
    HX_ASSERT_EQ(r.written, (size_t)0, "reset clears written");

    /* After reset, a continuation is invalid; must restart at offset=0. */
    HX_ASSERT_EQ(hux_ws_reassemble(&r, OP_TEXT, 3, 3, 6, "def", &out, &len),
                 HUX_FRAG_DROPPED, "continuation after reset -> DROPPED");
}

int main(void) {
    printf("== test_hux_ws_frag ==\n");
    HX_RUN(test_single_frame_passes_through);
    HX_RUN(test_two_fragment_reassembly);
    HX_RUN(test_three_fragment_reassembly);
    HX_RUN(test_back_to_back_messages);
    HX_RUN(test_oversized_message_dropped);
    HX_RUN(test_continuation_without_start_dropped);
    HX_RUN(test_out_of_order_fragment_dropped);
    HX_RUN(test_binary_opcode_dropped);
    HX_RUN(test_control_opcode_dropped_without_clobbering_state);
    HX_RUN(test_fragment_past_payload_end_dropped);
    HX_RUN(test_zero_length_fragment_dropped);
    HX_RUN(test_null_data_ptr_dropped);
    HX_RUN(test_reset_api);
    HX_SUMMARY();
}

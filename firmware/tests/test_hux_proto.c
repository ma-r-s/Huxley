/**
 * Host unit tests for `hux_proto`. Pure JSON-to-typed-message
 * marshalling — no I/O, no tasks. This is the wire contract with
 * the Huxley server; a silent regression here breaks every turn.
 *
 * Run via the CMake target in this directory (see README.md).
 */
#include "test_helpers.h"

#include "hux_proto.h"

#include <stdio.h>
#include <string.h>

static bool parse(const char *json, hux_msg_t *out) {
    return hux_proto_parse(json, strlen(json), out);
}

/* ---- basic shape --------------------------------------------------- */

static void test_parse_hello_v2(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"hello\",\"protocol\":2}", &m), "hello parses");
    HX_ASSERT_EQ(m.kind, HUX_MSG_HELLO, "kind=HELLO");
    HX_ASSERT_EQ(m.as.hello.protocol, 2, "protocol=2");
}

static void test_parse_hello_with_odd_field_order(void) {
    /* Python json.dumps preserves insertion order so in practice
     * `type` is always first, but the parser must not depend on it. */
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"protocol\":5,\"type\":\"hello\"}", &m), "reordered parses");
    HX_ASSERT_EQ(m.kind, HUX_MSG_HELLO, "kind=HELLO");
    HX_ASSERT_EQ(m.as.hello.protocol, 5, "protocol=5");
}

static void test_parse_hello_missing_protocol(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"hello\"}", &m), "no protocol still parses");
    /* Sentinel so the caller can distinguish "server sent 0" from
     * "server didn't send the field". */
    HX_ASSERT_EQ(m.as.hello.protocol, -1, "missing protocol -> -1");
}

/* ---- malformed ---------------------------------------------------- */

static void test_parse_not_json(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(!parse("not a json", &m), "garbage rejected");
}

static void test_parse_empty_string(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(!parse("", &m), "empty rejected");
}

static void test_parse_missing_type(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(!parse("{\"foo\":\"bar\"}", &m), "missing `type` field rejected");
}

static void test_parse_non_string_type(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(!parse("{\"type\":123}", &m), "numeric type rejected");
}

static void test_parse_array_at_top(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(!parse("[1,2,3]", &m), "top-level array rejected");
}

/* ---- kind dispatch ------------------------------------------------ */

static void test_parse_unknown_kind(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"banana\"}", &m), "unknown kind still parses");
    HX_ASSERT_EQ(m.kind, HUX_MSG_UNKNOWN, "kind=UNKNOWN");
}

static void test_all_protocol_kinds(void) {
    /* Every kind the server can send per docs/protocol.md. If a new
     * one lands without an entry here, the `default` branch proves
     * it falls through to UNKNOWN and we know to add a handler. */
    struct {
        const char *type;
        hux_msg_kind_t expected;
    } cases[] = {
        {"hello",          HUX_MSG_HELLO},
        {"state",          HUX_MSG_STATE},
        {"status",         HUX_MSG_STATUS},
        {"transcript",     HUX_MSG_TRANSCRIPT},
        {"audio",          HUX_MSG_AUDIO},
        {"model_speaking", HUX_MSG_MODEL_SPEAKING},
        {"set_volume",     HUX_MSG_SET_VOLUME},
        {"input_mode",     HUX_MSG_INPUT_MODE},
        {"claim_started",  HUX_MSG_CLAIM_STARTED},
        {"claim_ended",    HUX_MSG_CLAIM_ENDED},
        {"stream_started", HUX_MSG_STREAM_STARTED},
        {"stream_ended",   HUX_MSG_STREAM_ENDED},
        {"dev_event",      HUX_MSG_DEV_EVENT},
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        char buf[128];
        snprintf(buf, sizeof(buf), "{\"type\":\"%s\"}", cases[i].type);
        hux_msg_t m;
        HX_ASSERT_TRUE(parse(buf, &m), cases[i].type);
        HX_ASSERT_EQ(m.kind, cases[i].expected, cases[i].type);
    }
}

static void test_kind_name_round_trip(void) {
    HX_ASSERT_STREQ(hux_proto_kind_name(HUX_MSG_HELLO), "hello", "hello");
    HX_ASSERT_STREQ(hux_proto_kind_name(HUX_MSG_AUDIO), "audio", "audio");
    HX_ASSERT_STREQ(hux_proto_kind_name(HUX_MSG_DEV_EVENT), "dev_event", "dev_event");
    /* Unknown falls back to a fixed string so logs are never empty. */
    HX_ASSERT_STREQ(hux_proto_kind_name(HUX_MSG_UNKNOWN), "unknown", "unknown");
}

/* ---- robustness --------------------------------------------------- */

static void test_parse_hello_with_extra_fields(void) {
    /* Servers may add fields in the future (e.g. capabilities). We
     * must accept-and-ignore, not reject. */
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"hello\",\"protocol\":2,\"capabilities\":[\"audio\"]}", &m),
                   "forward-compat: extra fields OK");
    HX_ASSERT_EQ(m.kind, HUX_MSG_HELLO, "kind");
    HX_ASSERT_EQ(m.as.hello.protocol, 2, "protocol");
}

static void test_parse_nested_whitespace(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("   {\n  \"type\":  \"hello\" , \"protocol\" :2 }\n", &m),
                   "whitespace tolerated");
    HX_ASSERT_EQ(m.kind, HUX_MSG_HELLO, "kind");
    HX_ASSERT_EQ(m.as.hello.protocol, 2, "protocol");
}

static void test_null_out_rejected(void) {
    HX_ASSERT_TRUE(!hux_proto_parse("{\"type\":\"hello\"}", 17, NULL), "NULL out rejected");
}

/* ---- entry -------------------------------------------------------- */

int main(void) {
    printf("== test_hux_proto ==\n");
    HX_RUN(test_parse_hello_v2);
    HX_RUN(test_parse_hello_with_odd_field_order);
    HX_RUN(test_parse_hello_missing_protocol);
    HX_RUN(test_parse_not_json);
    HX_RUN(test_parse_empty_string);
    HX_RUN(test_parse_missing_type);
    HX_RUN(test_parse_non_string_type);
    HX_RUN(test_parse_array_at_top);
    HX_RUN(test_parse_unknown_kind);
    HX_RUN(test_all_protocol_kinds);
    HX_RUN(test_kind_name_round_trip);
    HX_RUN(test_parse_hello_with_extra_fields);
    HX_RUN(test_parse_nested_whitespace);
    HX_RUN(test_null_out_rejected);
    HX_SUMMARY();
}

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

/* ---- server_event (F-0028: the device contract) ---------------------- */

static void test_server_event_dev_cmd(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"server_event\",\"event\":\"dev.cmd\","
                         "\"data\":{\"id\":\"a1\",\"line\":\"battery\"}}",
                         &m),
                   "parses");
    HX_ASSERT_EQ(m.kind, HUX_MSG_SERVER_EVENT, "kind");
    HX_ASSERT_EQ(m.as.server_event.event, HUX_SERVER_EV_DEV_CMD, "dev.cmd");
    HX_ASSERT_TRUE(strcmp(m.as.server_event.id, "a1") == 0, "id");
    HX_ASSERT_TRUE(strcmp(m.as.server_event.line, "battery") == 0, "line");
    HX_ASSERT_TRUE(!m.as.server_event.line_truncated, "not truncated");
    HX_ASSERT_TRUE(parse("{\"type\":\"server_event\",\"event\":\"dev.cmd\","
                         "\"data\":{\"id\":42,\"line\":\"info\"}}",
                         &m),
                   "numeric id parses");
    HX_ASSERT_TRUE(strcmp(m.as.server_event.id, "42") == 0, "a number id as digits");
}

static void test_server_event_firmware_update_and_accept(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"server_event\",\"event\":\"firmware.update\",\"data\":"
                         "{\"version\":\"3de141d8\",\"url\":\"/firmware/3de141d8.bin\","
                         "\"force\":true}}",
                         &m),
                   "parses");
    HX_ASSERT_EQ(m.as.server_event.event, HUX_SERVER_EV_FIRMWARE_UPDATE, "firmware.update");
    HX_ASSERT_TRUE(strcmp(m.as.server_event.version, "3de141d8") == 0, "version");
    HX_ASSERT_TRUE(strcmp(m.as.server_event.url, "/firmware/3de141d8.bin") == 0, "url");
    HX_ASSERT_TRUE(m.as.server_event.force, "force");
    HX_ASSERT_TRUE(parse("{\"type\":\"server_event\",\"event\":\"ota.accept\","
                         "\"data\":{\"version\":\"3de141d8\"}}",
                         &m),
                   "accept parses");
    HX_ASSERT_EQ(m.as.server_event.event, HUX_SERVER_EV_OTA_ACCEPT, "ota.accept");
    HX_ASSERT_TRUE(!m.as.server_event.force, "force defaults to false");
}

static void test_server_event_unknown_and_odd_payloads(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"server_event\",\"event\":\"something.new\"}", &m),
                   "an unknown event parses");
    HX_ASSERT_EQ(m.as.server_event.event, HUX_SERVER_EV_UNKNOWN, "UNKNOWN");
    HX_ASSERT_TRUE(strcmp(m.as.server_event.name, "something.new") == 0, "its name kept");
    HX_ASSERT_TRUE(parse("{\"type\":\"server_event\",\"event\":\"dev.cmd\",\"data\":7}", &m),
                   "a non-object data parses");
    HX_ASSERT_TRUE(m.as.server_event.line[0] == '\0', "no line");
    char big[600];
    int n = snprintf(big, sizeof(big), "{\"type\":\"server_event\",\"event\":\"dev.cmd\","
                                       "\"data\":{\"id\":\"x\",\"line\":\"");
    for (int i = 0; i < 400; i++) {
        big[n++] = 'a';
    }
    snprintf(big + n, sizeof(big) - (size_t)n, "\"}}");
    HX_ASSERT_TRUE(parse(big, &m), "a long line parses");
    HX_ASSERT_TRUE(m.as.server_event.line_truncated, "and says it was cut");
    HX_ASSERT_EQ((int)strlen(m.as.server_event.line), 255, "kept within the buffer");
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

static void test_hello_audio_params(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"hello\",\"protocol\":2,\"audio_params\":{\"format\":\"opus\","
                         "\"sample_rate\":24000,\"channels\":1,\"frame_duration\":60}}",
                         &m),
                   "hello with audio_params parses");
    HX_ASSERT_TRUE(m.as.hello.audio_opus, "opus 24 kHz mono 60 ms -> opus");
    HX_ASSERT_TRUE(parse("{\"type\":\"hello\",\"protocol\":2}", &m), "plain hello");
    HX_ASSERT_TRUE(!m.as.hello.audio_opus, "no audio_params -> PCM");
    HX_ASSERT_TRUE(parse("{\"type\":\"hello\",\"audio_params\":{\"format\":\"opus\","
                         "\"sample_rate\":16000,\"channels\":1,\"frame_duration\":60}}",
                         &m),
                   "16 kHz hello parses");
    HX_ASSERT_TRUE(!m.as.hello.audio_opus, "a rate we don't speak -> PCM, not garbage");
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
        {"audio_clear",    HUX_MSG_AUDIO_CLEAR},
        {"server_event",   HUX_MSG_SERVER_EVENT},
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

/* ---- payloads the firmware acts on -------------------------------- */

static void test_hello_session_ready(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"hello\",\"protocol\":2}", &m), "parses");
    HX_ASSERT_TRUE(m.as.hello.session_ready, "missing session_ready (old server) = ready");
    HX_ASSERT_TRUE(parse("{\"type\":\"hello\",\"protocol\":2,\"session_ready\":false,"
                         "\"session_error\":\"persona failed\"}", &m), "parses");
    HX_ASSERT_TRUE(!m.as.hello.session_ready, "session_ready=false");
    HX_ASSERT_TRUE(parse("{\"type\":\"hello\",\"protocol\":2,\"session_ready\":true}", &m),
                   "parses");
    HX_ASSERT_TRUE(m.as.hello.session_ready, "session_ready=true");
}

static void test_state_values(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"state\",\"value\":\"CONVERSING\"}", &m), "parses");
    HX_ASSERT_EQ(m.as.state.value, HUX_SERVER_STATE_CONVERSING, "CONVERSING");
    HX_ASSERT_TRUE(parse("{\"type\":\"state\",\"value\":\"IDLE\"}", &m), "parses");
    HX_ASSERT_EQ(m.as.state.value, HUX_SERVER_STATE_IDLE, "IDLE");
    HX_ASSERT_TRUE(parse("{\"type\":\"state\",\"value\":\"CONNECTING\"}", &m), "parses");
    HX_ASSERT_EQ(m.as.state.value, HUX_SERVER_STATE_CONNECTING, "CONNECTING");
    HX_ASSERT_TRUE(parse("{\"type\":\"state\",\"value\":\"PLAYING\"}", &m), "parses");
    HX_ASSERT_EQ(m.as.state.value, HUX_SERVER_STATE_UNKNOWN, "an unknown value is UNKNOWN");
}

static void test_set_volume_level(void) {
    /* The server's system skill sends absolute levels, clamped to 0..100. */
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"set_volume\",\"level\":40}", &m), "parses");
    HX_ASSERT_EQ(m.kind, HUX_MSG_SET_VOLUME, "kind");
    HX_ASSERT_EQ(m.as.set_volume.level, 40, "level=40");
    HX_ASSERT_TRUE(parse("{\"type\":\"set_volume\",\"level\":250}", &m), "parses");
    HX_ASSERT_EQ(m.as.set_volume.level, 100, "clamped to 100");
    HX_ASSERT_TRUE(parse("{\"type\":\"set_volume\",\"level\":-5}", &m), "parses");
    HX_ASSERT_EQ(m.as.set_volume.level, 0, "clamped to 0");
    HX_ASSERT_TRUE(parse("{\"type\":\"set_volume\",\"level\":62.6}", &m), "parses");
    HX_ASSERT_EQ(m.as.set_volume.level, 63, "rounded");
    HX_ASSERT_TRUE(parse("{\"type\":\"set_volume\",\"level\":\"loud\"}", &m), "parses");
    HX_ASSERT_EQ(m.as.set_volume.level, -1, "not a number -> -1 (ignored)");
    HX_ASSERT_TRUE(parse("{\"type\":\"set_volume\"}", &m), "parses");
    HX_ASSERT_EQ(m.as.set_volume.level, -1, "missing -> -1 (ignored)");
}

static void test_input_mode(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"input_mode\",\"mode\":\"skill_continuous\","
                         "\"reason\":\"claim_started\",\"claim_id\":\"c1\"}", &m), "parses");
    HX_ASSERT_EQ(m.kind, HUX_MSG_INPUT_MODE, "kind");
    HX_ASSERT_TRUE(m.as.input_mode.known && m.as.input_mode.continuous, "skill_continuous");
    HX_ASSERT_TRUE(parse("{\"type\":\"input_mode\",\"mode\":\"assistant_ptt\","
                         "\"reason\":\"idle\",\"claim_id\":null}", &m), "parses");
    HX_ASSERT_TRUE(m.as.input_mode.known && !m.as.input_mode.continuous, "assistant_ptt");
    HX_ASSERT_TRUE(parse("{\"type\":\"input_mode\",\"mode\":\"assistant_gated\"}", &m),
                   "parses");
    HX_ASSERT_TRUE(!m.as.input_mode.known, "a future mode is not known (caller keeps its mode)");
}

static void test_model_speaking(void) {
    /* Barge-in holds the speaker only while the server says it's speaking. */
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"model_speaking\",\"value\":true}", &m), "parses");
    HX_ASSERT_TRUE(m.as.model_speaking.known && m.as.model_speaking.value, "true");
    HX_ASSERT_TRUE(parse("{\"type\":\"model_speaking\",\"value\":false}", &m), "parses");
    HX_ASSERT_TRUE(m.as.model_speaking.known && !m.as.model_speaking.value, "false");
    HX_ASSERT_TRUE(parse("{\"type\":\"model_speaking\",\"value\":\"yes\"}", &m), "parses");
    HX_ASSERT_TRUE(!m.as.model_speaking.known, "a non-boolean is not known (caller keeps its state)");
    HX_ASSERT_TRUE(parse("{\"type\":\"model_speaking\"}", &m), "parses");
    HX_ASSERT_TRUE(!m.as.model_speaking.known, "missing value is not known");
}

static void test_audio_clear_is_not_audio(void) {
    hux_msg_t m;
    HX_ASSERT_TRUE(parse("{\"type\":\"audio_clear\"}", &m), "parses");
    HX_ASSERT_EQ(m.kind, HUX_MSG_AUDIO_CLEAR, "audio_clear has its own kind");
}

/* ---- entry -------------------------------------------------------- */

int main(void) {
    printf("== test_hux_proto ==\n");
    HX_RUN(test_parse_hello_v2);
    HX_RUN(test_hello_session_ready);
    HX_RUN(test_state_values);
    HX_RUN(test_set_volume_level);
    HX_RUN(test_input_mode);
    HX_RUN(test_model_speaking);
    HX_RUN(test_audio_clear_is_not_audio);
    HX_RUN(test_parse_hello_with_odd_field_order);
    HX_RUN(test_parse_hello_missing_protocol);
    HX_RUN(test_hello_audio_params);
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
    HX_RUN(test_server_event_dev_cmd);
    HX_RUN(test_server_event_firmware_update_and_accept);
    HX_RUN(test_server_event_unknown_and_odd_payloads);
    HX_SUMMARY();
}

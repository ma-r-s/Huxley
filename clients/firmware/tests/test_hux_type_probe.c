/**
 * Host unit tests for `hux_type_probe`: every inbound audio chunk and every
 * audio_clear is routed by it. A miss sends audio to the slow control
 * plane; a false match clears the speaker or misroutes a message.
 */
#include "test_helpers.h"

#include "hux_type_probe.h"

#include <string.h>

static bool is(const char *json, const char *value) {
    return hux_has_type(json, strlen(json), value);
}

static void test_audio_and_audio_clear_are_told_apart(void) {
    const char *audio = "{\"type\": \"audio\", \"data\": \"AAAA\"}";
    const char *clear = "{\"type\": \"audio_clear\"}";
    HX_ASSERT_TRUE(is(audio, "\"audio\""), "audio is audio");
    HX_ASSERT_TRUE(!is(audio, "\"audio_clear\""), "audio is not audio_clear");
    HX_ASSERT_TRUE(is(clear, "\"audio_clear\""), "audio_clear is audio_clear");
    HX_ASSERT_TRUE(!is(clear, "\"audio\""), "audio_clear is not audio");
}

static void test_spacing_variants(void) {
    HX_ASSERT_TRUE(is("{\"type\":\"audio\",\"data\":\"AAAA\"}", "\"audio\""), "no spaces");
    HX_ASSERT_TRUE(is("{\"type\" :\t\"audio\",\"data\":\"AAAA\"}", "\"audio\""), "space, tab");
}

static void test_only_the_first_type_key_counts(void) {
    /* A dev_event or server_event can carry a nested "type". */
    const char *dev = "{\"type\": \"dev_event\", \"kind\": \"tool_call\", "
                      "\"payload\": {\"type\": \"audio_clear\"}}";
    const char *srv = "{\"type\":\"server_event\",\"data\":{\"type\":\"audio\"}}";
    HX_ASSERT_TRUE(!is(dev, "\"audio_clear\""), "nested audio_clear does not clear");
    HX_ASSERT_TRUE(!is(srv, "\"audio\""), "nested audio is not audio");
}

static void test_type_beyond_the_probe_is_not_seen(void) {
    char buf[256];
    memset(buf, ' ', sizeof(buf));
    buf[0] = '{';
    memcpy(buf + 120, "\"type\":\"audio\"}", 15);
    HX_ASSERT_TRUE(!hux_has_type(buf, 135, "\"audio\""), "type past 100 bytes: not seen");
}

static void test_short_and_odd_input(void) {
    HX_ASSERT_TRUE(!is("{}", "\"audio\""), "too short");
    HX_ASSERT_TRUE(!is("{\"type\"\"audio\",\"x\":1234567}", "\"audio\""), "no colon");
    HX_ASSERT_TRUE(!is("{\"kind\":\"audio\",\"x\":12345678}", "\"audio\""), "no type key");
}

int main(void) {
    printf("== test_hux_type_probe ==\n");
    HX_RUN(test_audio_and_audio_clear_are_told_apart);
    HX_RUN(test_spacing_variants);
    HX_RUN(test_only_the_first_type_key_counts);
    HX_RUN(test_type_beyond_the_probe_is_not_seen);
    HX_RUN(test_short_and_odd_input);
    HX_SUMMARY();
}

#include "hux_type_probe.h"

#include <string.h>

/* Cheap probe: is this message of type `value` (quotes included, e.g.
 * "\"audio\"")? Avoids parsing the whole JSON on the hot path. Relies
 * on the server always serialising `"type"` early in the document.
 * Tolerates whitespace around the colon and inside the value pair so it
 * works regardless of whether the server side uses
 * `json.dumps({"type":"audio"})` (no spaces) or `json.dumps(d)` with
 * default separators (space after `:`). The closing quote is part of
 * the match, so "audio" never matches "audio_clear". If this ever
 * misses, an `audio` message falls through to the control plane and is
 * logged as `ws.rx.audio (unhandled)`: correct but slow. Only the first
 * `"type"` key counts (the server writes it first). */
bool hux_has_type(const char *json, size_t len, const char *value) {
    if (len < 16) {
        return false;
    }
    /* Bound the scan: the `"type"` key lives in the first ~50 bytes
     * for any envelope shape we're likely to see. */
    size_t probe = len < 100 ? len : 100;
    static const char key[] = "\"type\"";
    static const size_t key_len = sizeof(key) - 1; /* 6 */
    const size_t value_len = strlen(value);

    for (size_t i = 0; i + key_len <= probe; i++) {
        if (memcmp(json + i, key, key_len) != 0) {
            continue;
        }
        size_t j = i + key_len;
        /* Skip whitespace before colon. */
        while (j < probe && (json[j] == ' ' || json[j] == '\t')) {
            j++;
        }
        if (j >= probe || json[j] != ':') {
            continue;
        }
        j++;
        /* Skip whitespace before value. */
        while (j < probe && (json[j] == ' ' || json[j] == '\t')) {
            j++;
        }
        /* The first "type" key decides: a nested one (a dev_event's tool
         * args, a server_event's data) must not route the message. */
        return j + value_len <= probe && memcmp(json + j, value, value_len) == 0;
    }
    return false;
}

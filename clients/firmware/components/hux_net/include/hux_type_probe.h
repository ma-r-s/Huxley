/**
 * hux_type_probe: find a message's type without parsing it.
 *
 * `hux_net` routes `audio` and `audio_clear` on the data plane by looking
 * for the top-level `"type"` in the first 100 bytes, not by running cJSON
 * over a 16 KB envelope. Pure, host-testable (firmware/tests/).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

/**
 * True when the first `"type"` key in the first 100 bytes of `json` has
 * the value `value`, quotes included (e.g. "\"audio\""): so "audio"
 * never matches "audio_clear", and a nested "type" further in never
 * counts.
 */
bool hux_has_type(const char *json, size_t len, const char *value);

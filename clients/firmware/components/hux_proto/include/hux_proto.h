/**
 * hux_proto — pure marshalling for the Huxley WebSocket protocol.
 *
 * Source of truth for the wire contract lives in docs/protocol.md.
 * This module has no I/O, no tasks, no globals: JSON string in, tagged
 * union out (and vice versa when we add outbound builders). It compiles
 * against cJSON only, which makes it testable on a host.
 *
 * The message-kind enum names every server→client type in the protocol
 * doc, even the ones we don't handle yet. Adding a handler later means
 * populating a new branch of `as` — never touching the parse dispatch.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/** Protocol version this client speaks. Must match server's hello.protocol. */
#define HUX_PROTOCOL_VERSION 2

typedef enum {
    HUX_MSG_UNKNOWN = 0,
    HUX_MSG_HELLO,
    HUX_MSG_STATE,
    HUX_MSG_STATUS,
    HUX_MSG_TRANSCRIPT,
    HUX_MSG_AUDIO,
    HUX_MSG_MODEL_SPEAKING,
    HUX_MSG_SET_VOLUME,
    HUX_MSG_INPUT_MODE,
    HUX_MSG_CLAIM_STARTED,
    HUX_MSG_CLAIM_ENDED,
    HUX_MSG_STREAM_STARTED,
    HUX_MSG_STREAM_ENDED,
    HUX_MSG_DEV_EVENT,
    HUX_MSG_AUDIO_CLEAR,
    HUX_MSG_SERVER_EVENT,
} hux_msg_kind_t;

typedef struct {
    int protocol;
    /** T1.16: false when the server has no live session to talk to (its
     * `session_error` says why). Missing field (older server) = true. */
    bool session_ready;
    /** F-0024: the server agreed to Opus (`audio_params` format "opus",
     * 24000 Hz, 1 channel, 60 ms frames, the xiaozhi-esp32 contract):
     * audio both ways is then binary WebSocket frames, one Opus packet
     * each. False for anything else (an older server: base64 PCM). */
    bool audio_opus;
} hux_msg_hello_t;

/** Server session state (`state.value`). */
typedef enum {
    HUX_SERVER_STATE_UNKNOWN = 0,
    HUX_SERVER_STATE_IDLE,
    HUX_SERVER_STATE_CONNECTING,
    HUX_SERVER_STATE_CONVERSING,
} hux_server_state_t;

typedef struct {
    hux_server_state_t value;
} hux_msg_state_t;

typedef struct {
    /** 0..100, clamped; -1 when the field is missing or not a number. */
    int level;
} hux_msg_set_volume_t;

typedef struct {
    /** `mode` was one of the two known values. */
    bool known;
    /** `skill_continuous`: a skill owns the mic, stream it continuously.
     * `assistant_ptt`: stream only while the talk button is held. */
    bool continuous;
} hux_msg_input_mode_t;

typedef struct {
    /** `value` was a boolean. */
    bool known;
    /** The server is speaking: model audio or a skill's stream is
     * playing out (a press now interrupts it, and the server sends
     * `audio_clear`). */
    bool value;
} hux_msg_model_speaking_t;

/**
 * Parsed server→client message. The `kind` tag discriminates which
 * member of `as` is populated. For kinds not yet handled the tag is set
 * but `as` is zeroed — callers log-and-ignore without tripping on union
 * contents.
 */
typedef struct {
    hux_msg_kind_t kind;
    union {
        hux_msg_hello_t hello;
        hux_msg_state_t state;
        hux_msg_set_volume_t set_volume;
        hux_msg_input_mode_t input_mode;
        hux_msg_model_speaking_t model_speaking;
    } as;
} hux_msg_t;

/**
 * Parse a UTF-8 JSON text (length-delimited, not NUL-delimited) into
 * `*out`. Returns true on a recognized top-level object with a `type`
 * string field; false on malformed JSON or missing `type`. An
 * unrecognized `type` value succeeds with kind=HUX_MSG_UNKNOWN.
 */
bool hux_proto_parse(const char *json, size_t len, hux_msg_t *out);

/** Human-readable kind name for logs. Returns a static string. */
const char *hux_proto_kind_name(hux_msg_kind_t kind);

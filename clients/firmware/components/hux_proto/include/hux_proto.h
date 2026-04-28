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
} hux_msg_kind_t;

typedef struct {
    int protocol;
} hux_msg_hello_t;

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

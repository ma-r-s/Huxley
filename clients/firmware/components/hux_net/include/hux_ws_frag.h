/**
 * hux_ws_frag — WebSocket message fragment reassembly.
 *
 * The esp_websocket_client library hands us fragments of any
 * incoming message larger than its internal buffer (32 KB on this
 * build). Each fragment has a `payload_offset`, the chunk's
 * `data_len`, and the total `payload_len`. This module accumulates
 * those into a caller-provided scratch buffer, and returns the full
 * message when the last fragment arrives.
 *
 * Pure: no I/O, no FreeRTOS, no IDF headers. Host-testable in the
 * firmware/tests/ tree alongside hux_proto.
 *
 * Threading: the reassembler state (`hux_ws_reassembler_t`) is
 * single-writer. On ESP-IDF the only caller is esp_websocket_client's
 * internal RX task; there is no concurrency. Tests drive it
 * synchronously.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    char *buf;        /* caller-provided scratch (expected to be PSRAM on target) */
    size_t buf_size; /* capacity of `buf` in bytes */
    size_t expected; /* `payload_len` of the in-flight message; 0 if idle */
    size_t written;  /* bytes accumulated in `buf` so far */
} hux_ws_reassembler_t;

typedef enum {
    /**
     * Fragment accepted; more fragments still needed. Caller should
     * keep feeding. `out_msg` / `out_msg_len` are untouched.
     */
    HUX_FRAG_NEED_MORE,
    /**
     * Complete message ready. `*out_msg` points into the scratch
     * (or at `data_ptr` directly for a single-frame message — the
     * pointer is only valid until the next call on the same
     * reassembler OR until the caller returns from the WS event
     * handler, whichever is sooner).
     */
    HUX_FRAG_READY,
    /**
     * Fragment rejected (oversized, out-of-order, bad inputs, or a
     * non-text frame). The reassembler is reset to idle; the next
     * call must start a fresh message with `payload_offset == 0`.
     */
    HUX_FRAG_DROPPED,
} hux_frag_result_t;

/** Initialise with caller-owned buffer. Idle after this call. */
void hux_ws_reassembler_init(hux_ws_reassembler_t *r, char *buf, size_t buf_size);

/** Reset to idle without changing the buffer binding. */
void hux_ws_reassembler_reset(hux_ws_reassembler_t *r);

/**
 * Feed one WebSocket data-frame fragment.
 *
 * `op_code` must be 0x01 (TEXT). Control frames (0x8/0x9/0xA) should
 * be filtered before reaching this function. Binary (0x2) is
 * rejected — the protocol is JSON-only.
 *
 * `payload_offset + data_len` must not exceed `payload_len`.
 * `payload_len` must not exceed the scratch `buf_size`, otherwise
 * the message is dropped and the reassembler reset.
 *
 * On `HUX_FRAG_READY`, `*out_msg` and `*out_msg_len` are set. The
 * pointer is valid until the next call. The reassembler is reset
 * to idle.
 */
hux_frag_result_t hux_ws_reassemble(
    hux_ws_reassembler_t *r,
    uint8_t op_code,
    int payload_offset,
    int data_len,
    int payload_len,
    const char *data_ptr,
    const char **out_msg,
    size_t *out_msg_len);

#ifdef __cplusplus
}
#endif

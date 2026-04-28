#include "hux_ws_frag.h"

#include <string.h>

/* WebSocket opcode for TEXT data frames (RFC 6455 §5.2). */
#define WS_OP_TEXT 0x01

void hux_ws_reassembler_init(hux_ws_reassembler_t *r, char *buf, size_t buf_size) {
    r->buf = buf;
    r->buf_size = buf_size;
    r->expected = 0;
    r->written = 0;
}

void hux_ws_reassembler_reset(hux_ws_reassembler_t *r) {
    r->expected = 0;
    r->written = 0;
}

hux_frag_result_t hux_ws_reassemble(
    hux_ws_reassembler_t *r,
    uint8_t op_code,
    int payload_offset,
    int data_len,
    int payload_len,
    const char *data_ptr,
    const char **out_msg,
    size_t *out_msg_len) {

    /* Only text frames are meaningful for our JSON protocol. Non-text
     * frames leave state untouched so a text message mid-fragmentation
     * isn't derailed by an interleaved binary frame (RFC 6455 allows
     * control frames to interleave but data frames of the same
     * message must be contiguous — esp_websocket_client delivers
     * data-frame continuations as op_code=0x01 with advancing
     * payload_offset). If a real binary frame ever arrives we just
     * drop it silently here; the caller logs it at its own layer. */
    if (op_code != WS_OP_TEXT) {
        return HUX_FRAG_DROPPED;
    }

    /* Guardrails on inputs. Library bugs or a misbehaving peer
     * shouldn't be allowed to scribble past the scratch. */
    if (payload_offset < 0 || data_len < 0 || payload_len < 0 ||
        data_ptr == NULL || data_len == 0) {
        r->expected = 0;
        r->written = 0;
        return HUX_FRAG_DROPPED;
    }
    if ((size_t)(payload_offset + data_len) > (size_t)payload_len) {
        r->expected = 0;
        r->written = 0;
        return HUX_FRAG_DROPPED;
    }

    /* Fast path: single-frame message. Offset=0, fits entirely in the
     * first chunk, and we're not mid-reassembly of something else.
     * Point straight into the caller's data — no copy, no state
     * change. */
    if (payload_offset == 0 && data_len == payload_len && r->expected == 0) {
        *out_msg = data_ptr;
        *out_msg_len = (size_t)data_len;
        return HUX_FRAG_READY;
    }

    /* Fragmented path. */
    if (payload_offset == 0) {
        /* Start of a new multi-fragment message. If something is
         * already in flight we abandon it — the peer just told us
         * the previous never completes. */
        if ((size_t)payload_len > r->buf_size) {
            r->expected = 0;
            r->written = 0;
            return HUX_FRAG_DROPPED;
        }
        r->expected = (size_t)payload_len;
        r->written = 0;
    } else {
        /* Continuation — must match the in-flight message exactly.
         * Any gap or overlap is a bug we can't recover from. */
        if (r->expected == 0 || (size_t)payload_offset != r->written) {
            r->expected = 0;
            r->written = 0;
            return HUX_FRAG_DROPPED;
        }
    }

    /* Bounds check the append. The offset/len consistency check
     * above should already guarantee this, but belt and braces
     * since we're writing raw bytes into a bounded buffer. */
    if (r->written + (size_t)data_len > r->buf_size) {
        r->expected = 0;
        r->written = 0;
        return HUX_FRAG_DROPPED;
    }

    memcpy(r->buf + r->written, data_ptr, (size_t)data_len);
    r->written += (size_t)data_len;

    if (r->written < r->expected) {
        return HUX_FRAG_NEED_MORE;
    }

    /* Complete. Hand out the buffer, reset to idle. */
    *out_msg = r->buf;
    *out_msg_len = r->written;
    r->expected = 0;
    r->written = 0;
    return HUX_FRAG_READY;
}

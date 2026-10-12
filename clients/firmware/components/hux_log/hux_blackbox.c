#include "hux_blackbox.h"

#include <string.h>

#define BLACKBOX_MAGIC 0x48424F58u /* 'HBOX' */

static uint32_t checksum(const hux_blackbox_t *b) {
    return b->magic ^ (b->head * 2654435761u) ^ (b->used * 40503u) ^ b->lines ^ 0xA5A5A5A5u;
}

void hux_blackbox_reset(hux_blackbox_t *b) {
    b->magic = BLACKBOX_MAGIC;
    b->head = 0;
    b->used = 0;
    b->lines = 0;
    b->check = checksum(b);
}

bool hux_blackbox_valid(const hux_blackbox_t *b) {
    return b->magic == BLACKBOX_MAGIC && b->head < HUX_BLACKBOX_BYTES &&
           b->used <= HUX_BLACKBOX_BYTES && b->check == checksum(b);
}

static void put_byte(hux_blackbox_t *b, char c) {
    b->buf[b->head] = c;
    b->head = (b->head + 1) % HUX_BLACKBOX_BYTES;
    if (b->used < HUX_BLACKBOX_BYTES) {
        b->used++;
    }
}

void hux_blackbox_put(hux_blackbox_t *b, const char *line, size_t len) {
    if (len >= HUX_BLACKBOX_BYTES) { /* keep the end, room for the newline */
        line += len - (HUX_BLACKBOX_BYTES - 1);
        len = HUX_BLACKBOX_BYTES - 1;
    }
    for (size_t i = 0; i < len; i++) {
        /* A stray newline inside a line would split it on the way out. */
        put_byte(b, line[i] == '\n' ? ' ' : line[i]);
    }
    put_byte(b, '\n');
    b->lines++;
    b->check = checksum(b);
}

size_t hux_blackbox_read(const hux_blackbox_t *b, char *out, size_t cap, uint32_t *kept) {
    *kept = 0;
    if (cap == 0) {
        return 0;
    }
    size_t start = (b->head + HUX_BLACKBOX_BYTES - b->used) % HUX_BLACKBOX_BYTES;
    size_t i = 0;
    if (b->used == HUX_BLACKBOX_BYTES) {
        /* Full: the oldest line was probably cut by the wrap. Skip to the
         * end of the first line. */
        while (i < b->used && b->buf[(start + i) % HUX_BLACKBOX_BYTES] != '\n') {
            i++;
        }
        i++;
    }
    size_t n = 0;
    size_t line_start = 0;
    for (; i < b->used && n + 1 < cap; i++) {
        char c = b->buf[(start + i) % HUX_BLACKBOX_BYTES];
        out[n++] = c;
        if (c == '\n') {
            (*kept)++;
            line_start = n;
        }
    }
    n = line_start; /* never a partial last line (cap reached) */
    out[n] = '\0';
    return n;
}

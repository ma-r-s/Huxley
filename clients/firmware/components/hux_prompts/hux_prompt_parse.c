#include "hux_prompt_parse.h"

#include <string.h>

bool hux_prompt_iter_init(hux_prompt_iter_t *it, const uint8_t *buf, size_t len) {
    it->buf = buf;
    it->len = len;
    it->off = 4;
    return buf != NULL && len >= 4 && memcmp(buf, HUX_PROMPT_MAGIC, 4) == 0;
}

int hux_prompt_iter_next(hux_prompt_iter_t *it, const uint8_t **pkt, size_t *pkt_len) {
    if (it->off == it->len) {
        return 0;
    }
    if (it->len - it->off < 2) {
        return -1;
    }
    size_t n = (size_t)it->buf[it->off] | ((size_t)it->buf[it->off + 1] << 8);
    if (n == 0 || n > HUX_PROMPT_MAX_PACKET || it->len - it->off - 2 < n) {
        return -1;
    }
    *pkt = it->buf + it->off + 2;
    *pkt_len = n;
    it->off += 2 + n;
    return 1;
}

int hux_prompt_count(const uint8_t *buf, size_t len) {
    hux_prompt_iter_t it;
    if (!hux_prompt_iter_init(&it, buf, len)) {
        return -1;
    }
    const uint8_t *pkt;
    size_t n;
    int count = 0, r;
    while ((r = hux_prompt_iter_next(&it, &pkt, &n)) == 1) {
        count++;
    }
    return r == 0 ? count : -1;
}

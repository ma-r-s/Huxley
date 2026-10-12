/**
 * hux_prompt_parse: reading a prompt file (`.hxp`, tools/render_prompts.py).
 *
 * Little-endian: "HXP1", then per packet a u16 length and one raw Opus
 * packet (24 kHz mono, 60 ms). Pure C, host-tested
 * (tests/test_hux_prompt_parse.c).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_PROMPT_MAGIC      "HXP1"
#define HUX_PROMPT_MAX_PACKET 1500 /* hux_opus's HUX_OPUS_MAX_PACKET */

typedef struct {
    const uint8_t *buf;
    size_t len;
    size_t off;
} hux_prompt_iter_t;

/** Start reading `buf`. False if it doesn't start with the magic. */
bool hux_prompt_iter_init(hux_prompt_iter_t *it, const uint8_t *buf, size_t len);

/** The next packet: 1 with `*pkt` and `*pkt_len` set, 0 at the clean end,
 * -1 if the file is cut short or a packet is empty or too big. */
int hux_prompt_iter_next(hux_prompt_iter_t *it, const uint8_t **pkt, size_t *pkt_len);

/** Packets in a whole file, or -1 if it is malformed (checked before
 * playing, so a bad file never plays half). */
int hux_prompt_count(const uint8_t *buf, size_t len);

#ifdef __cplusplus
}
#endif

/**
 * hux_blackbox: the last warnings and errors before a reset (F-0027).
 *
 * Pure (no I/O), host-tested. hux_log keeps one in RTC memory, which
 * survives a panic, a watchdog reset and esp_restart (not a power cut),
 * and the app sends the previous boot's lines after the next hello
 * (`device.blackbox`). Without it the lines that explain a reset die
 * with the PSRAM log queue.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define HUX_BLACKBOX_BYTES 4096

typedef struct {
    uint32_t magic;
    uint32_t head;  /* next write offset in buf */
    uint32_t used;  /* bytes held, at most HUX_BLACKBOX_BYTES */
    uint32_t lines; /* lines ever put since the reset */
    uint32_t check; /* over the four above: a power cut leaves garbage */
    char buf[HUX_BLACKBOX_BYTES];
} hux_blackbox_t;

/** Empty it (and make it valid). */
void hux_blackbox_reset(hux_blackbox_t *b);

/** True when it holds what a previous boot put (not power-on garbage). */
bool hux_blackbox_valid(const hux_blackbox_t *b);

/** Append `line` (without its newline; one is added), overwriting the
 * oldest bytes when full. A line longer than the box keeps its end. */
void hux_blackbox_put(hux_blackbox_t *b, const char *line, size_t len);

/** Copy the whole lines it holds, oldest first, '\n'-separated, into
 * `out` (NUL-terminated, at most `cap` - 1 bytes). Returns the length;
 * `*kept` gets the number of lines copied (lines put minus kept were
 * overwritten). A line cut by the wrap is left out. */
size_t hux_blackbox_read(const hux_blackbox_t *b, char *out, size_t cap, uint32_t *kept);

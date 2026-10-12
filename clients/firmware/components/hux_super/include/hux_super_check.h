/**
 * hux_super_check: has a part of the walkie stopped working? (F-0027)
 *
 * Pure (no I/O), host-tested. The supervisor samples each part's counters
 * once a second; a part is stalled when it had work waiting and finished
 * none for longer than its limit. Counting work done, not loop turns, is
 * the point: a wedged decoder leaves the speaker loop writing silence and
 * every turn counter advancing while he hears nothing (design critic,
 * 2026-10-12).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    const char *name;
    uint32_t limit_ms; /* no progress for this long, with work waiting: stalled */
    bool enforced;     /* false: log the stall but don't reset (degraded boot) */
    /* Sampled each check: */
    uint32_t done;     /* work finished, ever (wraps) */
    uint32_t pending;  /* work waiting now; parts that must always progress say 1 */
    /* Kept by hux_super_check: */
    uint32_t last_done;
    uint32_t progress_ms; /* last time it progressed, or had nothing to do */
    bool reported;        /* its stall was returned once already */
} hux_super_part_t;

/** Start watching: every part counts as having progressed at `now_ms`. */
void hux_super_begin(hux_super_part_t *parts, size_t n, uint32_t now_ms);

/** After sampling `done` and `pending`: the index of the first part that
 * just stalled (each stall returned once until it progresses again), or
 * -1. `*age_ms` gets how long it has been without progress. */
int hux_super_check(hux_super_part_t *parts, size_t n, uint32_t now_ms, uint32_t *age_ms);

#include "hux_super_check.h"

void hux_super_begin(hux_super_part_t *parts, size_t n, uint32_t now_ms) {
    for (size_t i = 0; i < n; i++) {
        parts[i].last_done = parts[i].done;
        parts[i].progress_ms = now_ms;
        parts[i].reported = false;
    }
}

int hux_super_check(hux_super_part_t *parts, size_t n, uint32_t now_ms, uint32_t *age_ms) {
    int stalled = -1;
    for (size_t i = 0; i < n; i++) {
        hux_super_part_t *p = &parts[i];
        if (p->done != p->last_done || p->pending == 0) {
            /* Finished something, or had nothing to do: healthy. */
            p->last_done = p->done;
            p->progress_ms = now_ms;
            p->reported = false;
            continue;
        }
        uint32_t age = now_ms - p->progress_ms; /* unsigned: survives the wrap */
        if (age >= p->limit_ms && !p->reported && stalled < 0) {
            p->reported = true;
            stalled = (int)i;
            *age_ms = age;
        }
    }
    return stalled;
}

/**
 * hux_super: the supervisor (remote-operation W5, F-0027).
 *
 * Nobody at the home can restart the walkie. Once a second this task
 * checks that each part of it is still finishing work (hux_super_check),
 * and feeds the task watchdog only while they are. A part with work
 * waiting and none done past its limit is logged (`super.stalled`, which
 * also lands in the RTC black box) and the watchdog is left to panic:
 * a core dump of every task, then a restart.
 *
 * Reset loops: a stall in the first 10 min of a boot is counted in RTC
 * memory; after 3 in a row the audio parts only log (the app, the timer
 * task and the ladder, the repair path, stay enforced), so a part that
 * wedges on every boot can't keep the walkie from taking an update.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Start the supervisor. After every part it watches has started. */
void hux_super_start(void);

/** Stalls reported this boot (an update's self-test wants none, F-0028). */
uint32_t hux_super_stalls(void);

/** Log the parts' counters and the boot's stall history (console `super`). */
void hux_super_status(void);

/** Bench test (console `hang app|spk`): block that part for good, so the
 * stall, the black box, the core dump and the restart can be seen. */
void hux_super_test_hang(const char *part);

#ifdef __cplusplus
}
#endif

/**
 * hux_button — the talk input: the HX-1 case's lever on GPIO7 (header
 * pin 2, to GND) or the on-board K2 via the TCA9555. Either one held is
 * talk; they post the same press/release events.
 *
 * The board doesn't wire the TCA9555 INT line to an MCU GPIO, so the
 * only path is polling. We poll both at 10 ms with a 2-sample debounce
 * (~20 ms effective), well below human-perceptible PTT latency. The
 * polling task runs at low priority; its I2C reads share the bus
 * with audio peripherals without contention (the esp-idf i2c_master
 * driver is thread-safe via internal mutex). A failed K2 read counts
 * as not pressed.
 *
 * K1 and K3 are reserved for future use — press/release events for
 * them will be added to `hux_app_event_kind_t` when a consumer
 * appears.
 */
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Configure GPIO7 (input, pull-up) and start the polling task. Call
 * once after `hux_board_init()` — the task reads K2 through the shared
 * TCA9555 and GPIO7 directly, every 10 ms.
 */
void hux_button_start(void);

#ifdef __cplusplus
}
#endif

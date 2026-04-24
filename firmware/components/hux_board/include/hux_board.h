/**
 * hux_board — shared on-board peripherals for the Waveshare
 * ESP32-S3-AUDIO-Board.
 *
 * Owns the I2C master bus (SDA=11, SCL=10) and the TCA9555 I/O
 * expander. Both `hux_button` (inputs K1/K2/K3 on P9/P10/P11) and
 * `hux_audio` (speaker-PA enable on P8) share these — splitting the
 * bus / expander into separate components would force an awkward
 * cross-dependency, so they live together here.
 *
 * Pins and expander-bit assignments are specific to this board and
 * documented in firmware/docs/decisions.md (board pinout section).
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "driver/i2c_master.h"

#ifdef __cplusplus
extern "C" {
#endif

/* TCA9555 logical pin numbers (0..15 = P0..P15 in datasheet order). */
#define HUX_TCA_LCD_RESET     0   /* output — LCD reset, unused in v0.x */
#define HUX_TCA_TOUCH_RESET   1   /* output — touch reset, unused */
#define HUX_TCA_CAM_PWDN      5   /* output — camera power-down, high=off */
#define HUX_TCA_AUDIO_PA_EN   8   /* output — speaker amp enable, active-high */
#define HUX_TCA_BTN_K1        9   /* input  — left button */
#define HUX_TCA_BTN_K2        10  /* input  — middle button (PTT) */
#define HUX_TCA_BTN_K3        11  /* input  — right button */

/**
 * Bring up I2C and the TCA9555. Call once at boot, before any
 * component that calls the accessors below. Safe to call exactly
 * once; idempotent second calls are a no-op.
 *
 * Initial expander state puts the LCD + touch in reset, the camera
 * off, and the audio PA off — everything inert until a consumer
 * explicitly enables it.
 */
void hux_board_init(void);

/**
 * Read one TCA9555 input pin. Returns `true` for a logic-high line,
 * `false` for logic-low. I2C bus error or pin out of range returns
 * `false` (fail-safe: a button that can't be read is not pressed).
 */
bool hux_board_tca_read(uint8_t pin);

/**
 * Drive one TCA9555 output pin. No-op if the pin is configured as
 * an input. Returns `true` on successful I2C write.
 */
bool hux_board_tca_write(uint8_t pin, bool high);

/**
 * Borrow the shared I2C bus handle. `hux_audio` uses this to register
 * ES8311 + ES7210 as devices on the same bus without re-initialising
 * the peripheral.
 */
i2c_master_bus_handle_t hux_board_i2c_bus(void);

#ifdef __cplusplus
}
#endif

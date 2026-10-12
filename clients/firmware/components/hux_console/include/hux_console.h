/**
 * hux_console: bench commands typed on the USB serial console, so the
 * board can be tested from the Mac with no hand on it.
 *
 *   talk | release      hold / let go of talk (the same button as the
 *                       lever and K2)
 *   say <n>             a whole turn with nobody speaking: holds talk while
 *                       clip n (tools/clips.py, in flash) replaces the mic
 *   tone [hz] [ms]      play a sine on the speaker (default 440 Hz, 1 s)
 *   vol <0..100>        set the speaker volume, as set_volume does
 *   pa on | off         speaker amplifier: off plays everything, silently
 *   selftest [hz]       electrical loopback: tone out, read back on the
 *                       ADC's reference channel (default 1000 Hz)
 *   wifi off | on       stop / restart Wi-Fi (offline behaviour, reconnect)
 *   wifistat            the Wi-Fi driver's buffer / RX / TX counters
 *   info                heap, PSRAM, task stack headroom, ring, volume
 *                       (and the codec's register), RSSI
 *
 * Each command is logged as `console.cmd ...` (and so streamed to the
 * server), so a bench press never passes for a real one in the logs.
 * Reads the console without the USB driver: polled, non-blocking, and
 * harmless when no host is attached.
 */
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/** Start the console task. Call once, after the components it drives. */
void hux_console_start(void);

#ifdef __cplusplus
}
#endif

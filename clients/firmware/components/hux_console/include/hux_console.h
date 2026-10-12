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
 *   wifi off [s] | on   stop Wi-Fi, back on by itself after s seconds
 *                       (default 10) / restart it now
 *   wifi list           the known networks (F-0027) and the MAC
 *   wifi addhex <s> <p> add a network: name and password in hex (USB only;
 *                       tools/wifi.py sends it; the password is never logged)
 *   wifi forget <s>     forget one added on site (name in hex; USB only)
 *   server <uri> [min]  WebSocket to another server, back after min minutes
 *                       (default 15) or a reboot (bench.py's proxy)
 *   quiet [s]           amp off for s seconds, kept through restarts
 *                       (0 ends it; no s: how long is left)
 *   wifistat            the Wi-Fi driver's buffer / RX / TX counters
 *   info                heap, PSRAM, task stack headroom, ring, volume
 *                       (and the codec's register), RSSI
 *   ota <url> [force]   download the image at url, check it, boot it
 *                       (refused while playing or listening, unless force)
 *   ota confirm         keep the image just updated to (after 30 s up)
 *   ota rollback        boot the other slot's image
 *                       (hux_ota; `force` takes the same version again)
 *   reboot              restart in software: the safe way for a tool to
 *                       get a fresh boot (an RTS pulse can leave the chip
 *                       in download mode until a power cut)
 *
 * Each command is logged as `console.cmd ...` (and so streamed to the
 * server), so a bench press never passes for a real one in the logs.
 * Reads the console without the USB driver: polled, non-blocking, and
 * harmless when no host is attached.
 */
#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Start the console task. Call once, after the components it drives. */
void hux_console_start(void);

/** Run one command line (the commands above; `line` is modified). The
 * serial console calls it, and so does hux_dev's POST /cmd over Wi-Fi.
 * Its results are log lines (serial, and the server's log). */
void hux_console_run(char *line);

/** The line carries a secret (`wifi addhex`): log it redacted. */
bool hux_console_is_secret(const char *line);

#ifdef __cplusplus
}
#endif

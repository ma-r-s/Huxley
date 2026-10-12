/**
 * hux_audio — I2S + on-board codecs for the Waveshare
 * ESP32-S3-AUDIO-Board.
 *
 * v0.2.1 scope: mic path only. ES7210 ADC over a duplex I2S channel
 * on I2S1, 24 kHz, 32-bit slots, 4-channel TDM (RMNM = Reference,
 * Mic1, Noise, Mic2). Only Mic1 is forwarded downstream — the four-
 * channel array is a beamforming / noise-rejection hint that we'll
 * ignore until there's a reason not to.
 *
 * v0.3 will extend this to drive ES8311 for playback on the same
 * duplex channel (one I2S clock, one LRCK, both codecs slaved to it).
 *
 * Consumer contract: the sink callback runs on the mic task. It
 * receives one `~20 ms` block of mono PCM16 per call. Non-blocking
 * only — slow sinks stall the mic pipeline.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * One mic frame delivered from the capture task.
 *
 * `pcm` points to `samples` int16_t values of mono PCM16 at 24 kHz.
 * The pointer is only valid for the duration of the call — the sink
 * must copy (typically into an outbound-WS buffer) before returning.
 */
typedef void (*hux_audio_mic_frame_fn)(const int16_t *pcm, size_t samples);

/**
 * Boot the audio subsystem: init I2S1 duplex, bring up ES7210, open
 * the ADC at 24 kHz / 4-channel TDM / 32-bit slots. Call once after
 * `hux_board_init()`. Leaves the mic task created but idle — no
 * frames flow until `hux_audio_mic_start()`.
 */
void hux_audio_init(void);

/**
 * Register (or clear with NULL) the callback that receives mic
 * frames. Safe to swap while the mic is running; the next frame is
 * delivered to the new sink. Release-store semantics so a producer
 * sees a fully-constructed sink.
 */
void hux_audio_set_mic_sink(hux_audio_mic_frame_fn sink);

/**
 * Begin capturing. The mic task wakes up and begins pulling frames
 * from the ADC; each frame is delivered to the current sink if one
 * is set, otherwise discarded. Idempotent — second call while
 * running is a no-op.
 */
void hux_audio_mic_start(void);

/**
 * Stop capturing. The mic task finishes any in-flight read (~20 ms
 * worst case) then idles. Idempotent.
 */
void hux_audio_mic_stop(void);

/** `true` if the mic is actively producing frames. */
bool hux_audio_mic_is_running(void);

/* -- Speaker (playback) path ---------------------------------------- */

/**
 * Push a PCM16 mono chunk at 24 kHz into the speaker ring buffer: the
 * `hux_net_set_audio_sink` target, one writer (the WS task). When the
 * ring (87 s) is full it waits for room, which backs the server off
 * through TCP; it returns at once (dropping the chunk) while a barge-in
 * hold is on, and gives up after 2 s if playback is stuck. `pcm` is
 * copied; the caller can reuse it on return.
 */
void hux_audio_spk_push(const uint8_t *pcm, size_t len);

/**
 * Drop everything currently in the speaker ring: on `audio_clear` (from
 * the WS client task) and on a talk press (barge-in, from the app task).
 * The playback task marks it stale and answers within a 20 ms chunk (this
 * waits for that, up to 100 ms), then reads it out over the next passes;
 * audio pushed after the call plays after it. Don't call it from that task.
 */
void hux_audio_spk_clear(void);

/**
 * Barge-in: drop inbound audio (the interrupted reply's tail, already on
 * its way) until the server's `audio_clear`, `hux_audio_spk_unhold`, or
 * 3 s. Call on a talk press while the server is speaking.
 */
void hux_audio_spk_hold(void);

/** End the hold early (`why` is logged): the server stopped speaking on
 * its own, so no `audio_clear` will come. No-op if not holding. */
void hux_audio_spk_unhold(const char *why);

/** The server's `audio_clear`: ends a hold, then clears the ring. The
 * `audio_clear` sink (WS task, in order with the audio). */
void hux_audio_on_audio_clear(void);

/** Speaker volume 0..100 (`set_volume` from the server): boots at 50
 * (-15 dB), never below 10; out of range is ignored. */
void hux_audio_set_volume(int level);

/** The volume level applied last (0..100). */
int hux_audio_get_volume(void);

/**
 * Queue a local tone: a sine of `hz` (0 = a silent gap) for `ms`, faded
 * in and out. Segments play in order once the reply audio in the ring
 * has played; a speaker clear drops them. For sounds the device makes on
 * its own (it can't reach the server) and the bench `tone`. Returns
 * false if rejected (out of range, queue of 8 full).
 */
bool hux_audio_tone(int hz, int ms);

/** Bytes of reply audio waiting in the speaker ring. */
size_t hux_audio_spk_ring_used(void);

/* -- Bench tests with no sound (serial console) ---------------------- */

/**
 * Electrical loopback: play a tone of `hz` and read it back on the ES7210
 * reference channel, which is wired to the speaker codec's output. Logs
 * `audio.selftest` per channel and a pass/FAIL line. Refused while the
 * mic is in use; runs on the mic task, about a second.
 */
void hux_audio_selftest(int hz);

/**
 * Arm clip `clip` from the `storage` partition (written by
 * tools/clips.py): the next mic session streams it instead of Mic1, then
 * silence. Returns the clip's length in ms, or -1 (no clips, bad index,
 * mic busy).
 */
int hux_audio_inject(int clip);

/** Disarm a clip `hux_audio_inject` armed that no mic session took (the
 * press was ignored): it must never leak into a later, real press. */
void hux_audio_inject_cancel(void);

/** Speaker amplifier on/off (TCA9555 P8). Off: everything still plays
 * through the codec, nothing is heard. On at boot, unless quiet. `on`
 * also ends a quiet period. */
void hux_audio_pa(bool on);

/** Amp off for `seconds`, then on by itself; survives restarts and
 * updates in between (the end time is kept in RTC memory; a power cut
 * ends it). 0 ends it now. For nights with the board on the bench. */
void hux_audio_quiet(uint32_t seconds);

/** Seconds of quiet left, 0 if none. */
uint32_t hux_audio_quiet_left_s(void);

/** ES8311 DAC volume register (0xBF = 0 dB, 0.5 dB steps), or -1. */
int hux_audio_dac_volume_reg(void);

#ifdef __cplusplus
}
#endif

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

/** A call (F-0031: the mic streams while the speaker plays the far end):
 * the speaker's rebuffer target drops to 240 ms and the mic's echo
 * suppressor runs. `audio.call on|off`, `audio.echo` once a second. */
void hux_audio_set_call(bool on);

/** The call's echo mode (`echo off|shadow|on`, NVS; shadow by default:
 * measure, change nothing) and jitter buffer (`calljb <ms>`, 100-1000,
 * NVS; 240 by default). */
enum { HUX_ECHO_MODE_OFF = 0, HUX_ECHO_MODE_SHADOW = 1, HUX_ECHO_MODE_ON = 2 };
void hux_audio_set_echo_mode(int mode);
/** Console `echo depth <dB>` (20-70, 50 by default): the gate, saved; the
 * next call uses it (F-0031). */
void hux_audio_set_echo_depth(int db);
/** Bench (console `call on|off`): call mode with the mic read for the echo
 * gate only, nothing sent (F-0031). */
void hux_audio_bench_call(bool on);
void hux_audio_set_call_jb(int ms);

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

/** Speaker volume 0..100 (`set_volume` from the server): kept in NVS
 * and applied at boot (50, -11.5 dB, the first time), never below 10; out
 * of range is ignored. */
void hux_audio_set_volume(int level);

/** The volume level applied last (0..100). */
int hux_audio_get_volume(void);

/** The supervisor's view (F-0027): successful speaker writes (the loop
 * writes every 20 ms, audio or silence), and mic passes that did their
 * job (a read while capturing, an idle frame while not). */
uint32_t hux_audio_spk_writes(void);
uint32_t hux_audio_mic_turns(void);

/** Milliseconds played out of the speaker ring since boot: the server's
 * audio (and a stored prompt, hux_prompts); local tones and silence don't
 * count. */
uint32_t hux_audio_played_ms(void);

/** Milliseconds since the speaker last made a sound (ring audio or a tone;
 * since boot if never): a battery reading is resting only after a quiet
 * while (F-0030). */
uint32_t hux_audio_quiet_ms(void);

/** Before hux_audio_init only (the park, F-0030): put both codecs in their
 * own power-down state over I2C; a CPU reset leaves them powered. False if
 * a write failed or audio is already open. */
bool hux_audio_codecs_power_down(void);

/** Supervisor test (console `hang spk`): the speaker task blocks for good. */
void hux_audio_debug_hang_spk(void);

/** device.health (F-0027): underruns, their silent ms, and the ms of queued
 * audio dropped (clears, barge-in holds) since the last call. */
void hux_audio_take_health(uint32_t *underruns, uint32_t *underrun_ms, uint32_t *discarded_ms);

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

/** Before the clips partition is rewritten (developer door, POST /clips):
 * cancel any injection and unmap it; the next `say` maps it again.
 * False if a clip is playing right now. */
bool hux_audio_clips_release(void);

/** Speaker amplifier on/off (TCA9555 P8). Off: everything still plays
 * through the codec, nothing is heard. On at boot, unless quiet. `on`
 * also ends a quiet period. */
void hux_audio_pa(bool on);

/** The server is sending a reply or a stream (its model_speaking). A ring
 * that runs dry meanwhile is a dropout (the network fell behind): the
 * speaker then rebuffers, waiting for SPK_REBUFFER_MS of audio before it
 * plays again, so a slow link gives a few pauses instead of stutter. */
void hux_audio_set_stream_active(bool active);

/** Amp off for `seconds`, then on by itself; survives restarts and
 * updates in between (the end time is kept in RTC memory; a power cut
 * ends it). 0 ends it now. For nights with the board on the bench. */
void hux_audio_quiet(uint32_t seconds);

/** Seconds of quiet left, 0 if none. */
uint32_t hux_audio_quiet_left_s(void);

/** ES8311 DAC volume register (0xBF = 0 dB, 0.5 dB steps), or -1. */
int hux_audio_dac_volume_reg(void);

/** Both codecs answer on I2C (an update's silent self-test, F-0028): their
 * ID registers into `*dac_id` (ES8311) and `*adc_id` (ES7210), -1 if not. */
bool hux_audio_codecs_answer(int *dac_id, int *adc_id);

#ifdef __cplusplus
}
#endif

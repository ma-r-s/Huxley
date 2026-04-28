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
 * Push a PCM16 mono chunk at 24 kHz into the speaker ring buffer.
 * Safe to call from any task; designed to be the target of
 * `hux_net_set_audio_sink`. Non-blocking — on ring overflow (consumer
 * falling behind producer) the tail is dropped and a WARN is logged.
 *
 * `pcm` points to a caller-owned buffer; the push copies into the
 * ring, so `pcm` can be freed / reused immediately after return.
 */
void hux_audio_spk_push(const uint8_t *pcm, size_t len);

/**
 * Drop everything currently in the speaker ring. Called on
 * `audio_clear` (v0.3.2) and on `stream_ended (interrupted)` to cut
 * playback immediately rather than finishing the buffered tail.
 */
void hux_audio_spk_clear(void);

#ifdef __cplusplus
}
#endif

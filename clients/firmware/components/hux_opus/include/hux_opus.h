/**
 * hux_opus: Opus for the WebSocket audio (F-0024).
 *
 * The contract is xiaozhi-esp32's (the most used open-source ESP32 voice
 * assistant, docs/websocket.md there): the client asks for it, the server's
 * hello confirms `audio_params` {format "opus", 24000 Hz, 1 channel, 60 ms},
 * and audio then goes both ways as binary WebSocket frames, one raw Opus
 * packet each (their binary protocol version 1). ~4 KB/s instead of ~64 KB/s
 * of base64 PCM: a lossy 2.4 GHz link that TCP couldn't keep at 64 KB/s
 * carries it (measured 2026-10-12: playback starved 34 % of a half hour).
 *
 * Codec: Espressif's esp_audio_codec (what xiaozhi uses on the S3).
 * Decoding runs in its own task (stack in PSRAM, ~20 KB per the codec's
 * README) fed by a queue that also carries `audio_clear`, so a clear lands
 * after exactly the packets that came before it, as on the WebSocket.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_OPUS_SAMPLE_RATE   24000
#define HUX_OPUS_FRAME_MS      60
#define HUX_OPUS_FRAME_SAMPLES (HUX_OPUS_SAMPLE_RATE / 1000 * HUX_OPUS_FRAME_MS) /* 1440 */
#define HUX_OPUS_MAX_PACKET    1500

typedef void (*hux_opus_pcm_sink_fn)(const uint8_t *pcm, size_t len);
typedef void (*hux_opus_marker_fn)(void);

/** Open the encoder and decoder, start the decoder task. Decoded PCM goes
 * to `pcm_sink` (the speaker ring), a queued clear calls `clear_sink`, a
 * queued release calls `unhold_sink`. False if any of it failed: then the
 * client must not ask the server for Opus. */
bool hux_opus_init(hux_opus_pcm_sink_fn pcm_sink, hux_opus_marker_fn clear_sink,
                   hux_opus_marker_fn unhold_sink);

/** Opened, and its decoder task is running. */
bool hux_opus_ready(void);

/** From the WebSocket task: one received packet, decoded in order. Waits
 * (up to 5 s) for room: a full speaker pushes back on TCP as before. */
void hux_opus_rx_packet(const uint8_t *data, size_t len);

/** From the WebSocket task: `audio_clear`, in order with the packets. */
void hux_opus_rx_clear(void);

/** Any task: release the barge-in hold once the packets queued before
 * this call have been decoded (model_speaking false with no audio_clear:
 * an earlier release would let the old reply's tail play). */
void hux_opus_rx_unhold(void);

/** Any task: the connection dropped or a new one began: what is queued
 * belongs to the old one, drop it and reset the decoder. */
void hux_opus_rx_reset(void);

/** Packets waiting to be decoded, decode errors so far, the decoder
 * task's smallest free stack (bytes). For the stats lines. */
uint32_t hux_opus_rx_waiting(void);
uint32_t hux_opus_dec_errors(void);
uint32_t hux_opus_dec_stack_free(void);

/** From the mic sender only: one 60 ms frame of 24 kHz mono PCM16 in,
 * one Opus packet out. Returns its size, or -1. */
int hux_opus_encode(const int16_t *pcm, uint8_t *out, size_t cap);

/** From the mic sender only: a new press starts a fresh stream. */
void hux_opus_encode_reset(void);

/** Average microseconds per encode / per decode since the last call (for
 * the mic and receive stats lines; 0 if none). */
uint32_t hux_opus_take_enc_us(void);
uint32_t hux_opus_take_dec_us(void);

/** The supervisor's view (F-0027): items the decoder took off its queue,
 * ever, and how many wait now. */
void hux_opus_rx_progress(uint32_t *done, uint32_t *pending);

#ifdef __cplusplus
}
#endif

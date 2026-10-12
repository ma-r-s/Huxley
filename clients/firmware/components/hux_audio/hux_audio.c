#include "hux_audio.h"

#include <math.h>
#include <stdatomic.h>
#include <string.h>

#include "driver/i2s_std.h"
#include "esp_attr.h"
#include "esp_check.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_rtc_time.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"

#include "hux_board.h"

static const char *TAG = "hux_audio";

/* -- I2S topology -------------------------------------------------------
 *
 * Both ES7210 (mic ADC) and ES8311 (speaker codec) share a single duplex
 * I2S channel on I2S1. LRCK, BCLK, MCLK drive both; DIN is from ES7210,
 * DOUT goes to ES8311. One i2s_new_channel() call produces handles for
 * RX + TX; individual channels are enabled independently. v0.2.1 only
 * activates RX; v0.3 extends to TX.
 *
 * Pins (from Waveshare schematic, see firmware/docs/decisions.md):
 */
#define I2S_PORT     I2S_NUM_1
#define I2S_GPIO_MCLK   12
#define I2S_GPIO_BCLK   13
#define I2S_GPIO_LRCK   14
#define I2S_GPIO_DIN    15
#define I2S_GPIO_DOUT   16

/* -- Sample / frame math ------------------------------------------------
 *
 * 24 kHz mono PCM16 matches the Huxley server's wire format exactly
 * (docs/protocol.md), so no resampling is needed on either side.
 *
 * The ES7210 streams 4-channel TDM in the "RMNM" layout: each frame is
 * [Ref, Mic1, Noise, Mic2] interleaved 16-bit samples. We extract Mic1
 * (index 1) for a clean single-speaker voice capture. The other three
 * channels are a beamforming hint we won't use until there's a reason.
 */
#define MIC_SAMPLE_RATE_HZ    24000
#define MIC_CHANNELS_TDM      4        /* RMNM */
#define MIC_BITS_PER_SAMPLE   16
#define FRAME_MS              20
#define FRAME_SAMPLES         (MIC_SAMPLE_RATE_HZ * FRAME_MS / 1000)   /* 480 */
#define FRAME_RAW_BYTES       (FRAME_SAMPLES * MIC_CHANNELS_TDM * sizeof(int16_t)) /* 3840 */

#define MIC_TASK_STACK        6144
#define MIC_TASK_PRIO         8   /* above hux_app (5); audio pacing wins */

/* -- Speaker (playback) side ------------------------------------------
 *
 * ES8311 driven by the TX side of the same I2S channel. Inbound PCM
 * arrives via `hux_audio_spk_push` (called from hux_net's WS RX task
 * after fragment reassembly + base64 decode); the bytes land in a
 * PSRAM ring. A dedicated consumer task (`spk_task`) pulls from the
 * ring and writes into ES8311 via `esp_codec_dev_write`, which
 * blocks on DMA and paces the stream naturally. On ring underrun
 * the task writes silence so the DMA never underflows (no click /
 * pop from the PA when the stream pauses).
 *
 * Ring size: see SPK_RING_BYTES (4 MB, about 87 s of 24 kHz PCM16
 * mono) for why it holds a whole reply. */
#define SPK_SAMPLE_RATE_HZ    24000
#define SPK_CHUNK_MS          20
#define SPK_CHUNK_SAMPLES     (SPK_SAMPLE_RATE_HZ * SPK_CHUNK_MS / 1000) /* 480 */
#define SPK_CHUNK_BYTES       (SPK_CHUNK_SAMPLES * sizeof(int16_t))      /* 960 */
/* The server forwards a reply as fast as the model makes it: 5-6x real
 * time measured on the bench once the socket was fixed (F-0015), so an
 * 80 s story arrives in ~14 s. The ring holds a whole reply (4 MB = 87 s;
 * the longest seen was 80 s) so it never has to push back on the server:
 * when it does (spk_push waits for room), macOS lets the server's socket
 * buffer grow to 4 MB, the server's keepalive ping queues behind it, and
 * after 20 s the server drops the connection. Radio and audiobooks come
 * at real time (ffmpeg -re), so only model replies burst. PSRAM: about
 * 3.4 MB stays free. */
#define SPK_RING_BYTES        (4 * 1024 * 1024)
#define SPK_DISCARD_PER_CHUNK (128 * 1024) /* clear read-out per 20 ms pass */
#define SPK_TASK_STACK        6144
#define SPK_TASK_PRIO         8
/* Volume 0..100. The server's system skill sends absolute levels and
 * assumes 50 to start with. Level 50 is the loudness every hardware test
 * so far used (the old boot level 70: ES8311 DAC at -11.5 dB), so the
 * device boots where the server thinks it is and "sube" / "baja" go the
 * right way from there; 100 is 0 dB, the most the DAC gives without
 * clipping (measured by the loopback self-test: the curve below is the
 * DAC register, 1:1). Below SPK_VOLUME_MIN is raised to it: a blind user
 * can't be left with a device too quiet to hear. */
#define SPK_VOLUME_DEFAULT    50
#define SPK_VOLUME_MIN        10
#define SPK_PA_SETTLE_MS      10  /* Waveshare factsheet recommended PA-on delay */
static _Atomic int s_volume = SPK_VOLUME_DEFAULT; /* level applied, for reports */

static i2s_chan_handle_t s_i2s_tx = NULL;
static i2s_chan_handle_t s_i2s_rx = NULL;

/* Mic (ES7210) — unchanged from v0.2.1. */
static const audio_codec_data_if_t *s_mic_data_if = NULL;
static const audio_codec_ctrl_if_t *s_mic_ctrl_if = NULL;
static const audio_codec_if_t *s_mic_codec_if = NULL;
static esp_codec_dev_handle_t s_mic_dev = NULL;

/* Speaker (ES8311) — new in v0.3.1. */
static const audio_codec_data_if_t *s_spk_data_if = NULL;
static const audio_codec_ctrl_if_t *s_spk_ctrl_if = NULL;
static const audio_codec_gpio_if_t *s_spk_gpio_if = NULL;
static const audio_codec_if_t *s_spk_codec_if = NULL;
static esp_codec_dev_handle_t s_spk_dev = NULL;

/* Playback ring — single-writer (WS RX task via spk_push),
 * single-reader (spk_task). PSRAM-backed via the stream buffer's
 * internal heap_caps_malloc which routes > 16 KB to PSRAM per our
 * SPIRAM_MALLOC_ALWAYSINTERNAL threshold. */
static StreamBufferHandle_t s_spk_ring = NULL;

/* Clearing the ring: the reader (spk_task) empties it by reading it out,
 * never by xStreamBufferReset. A reset from any task can land mid-send
 * (the writer copies and moves the head without a lock) and leave the
 * ring "full" of 0x55 fill, which plays as a loud DC hum; reading out is
 * what the reader always does, so it is safe next to a writer. A caller
 * bumps `s_spk_clear_req` and waits until spk_task reports it done. */
/* Barge-in hold. A press interrupts a reply, but the reply's audio the
 * server had already sent is still on its way (measured: 1.5 s of it,
 * queued in the server's socket ahead of the `audio_clear`), and it
 * would play over the user. From the press until the server's
 * `audio_clear` (it marks where the old audio ends) inbound audio is
 * dropped. Set by the app task, checked by the WS task in spk_push. */
/* One word, so the expiry below can't wipe a newer press's hold: 0 = no
 * hold, else (the press's uptime_ms | 1). */
static _Atomic uint32_t s_spk_hold = 0;
static uint32_t s_spk_hold_dropped = 0; /* WS task only */
#define SPK_HOLD_MAX_MS 3000  /* the clear comes in ~1.5 s; never mute longer */
#define SPK_PUSH_POLL_MS 20    /* ring full: re-check the hold this often */
#define SPK_PUSH_MAX_MS  2000  /* the ring drains 48 KB/s; 2 s full means stuck */
static uint32_t s_spk_push_max_wait_ms = 0; /* WS task only; reported by the heartbeat */

static _Atomic uint32_t s_spk_clear_req = 0;
static _Atomic uint32_t s_spk_clear_done = 0;
#define SPK_GAP_MAX_CHUNKS 50  /* 1 s: a silence shorter than this mid-reply is a dropout */
/* Rebuffering (F-0022): after the ring runs dry mid-reply, play silence
 * until this much is buffered (or the reply ends, or SPK_REBUFFER_MAX_MS),
 * as streaming players do. Measured with bench.py --wifi --steps slowdown:
 * the downlink at 40% of real time for 8 s gave 25 dropouts before. */
#define SPK_REBUFFER_MS     1000
#define SPK_REBUFFER_BYTES  (SPK_REBUFFER_MS * 48)  /* 24 kHz mono PCM16 */
#define SPK_REBUFFER_MAX_MS 4000

static _Atomic bool s_stream_active = false;

void hux_audio_set_stream_active(bool active) {
    atomic_store_explicit(&s_stream_active, active, memory_order_release);
}
#define SPK_CLEAR_WAIT_MS 100  /* spk_task services a request within one chunk (~20 ms) */

/* Local tones: the "can't reach the server" cue (hux_app) and the bench
 * `tone` (hux_console). spk_task synthesises them itself in place of
 * silence, so nothing but the WS task ever writes the ring (it has one
 * writer); reply audio in the ring plays first. A clear drops them too. */
typedef struct {
    uint16_t hz;      /* 0 = a silent gap */
    uint16_t ms;
} tone_seg_t;
#define TONE_QUEUE_DEPTH 8
#define TONE_AMPLITUDE   8000  /* about -12 dBFS: clear, not harsh */
#define TONE_FADE_SAMPLES (SPK_SAMPLE_RATE_HZ / 200)  /* 5 ms in and out: no clicks */
static QueueHandle_t s_tone_q = NULL;

/* Bench tests with no air in between (serial console; hux_console.h).
 *
 * Self-test: ES7210 channel 0 is the echo-cancellation reference (RMNM),
 * wired to the speaker codec's output, so a tone played on the DAC comes
 * back on it electrically: DAC, I2S both ways and the ADC, no sound
 * needed. Runs on the mic task (the codec's only reader); refused while
 * the mic is in use.
 *
 * Inject: the next talk press streams a recorded clip from the `storage`
 * partition instead of Mic1 (the codec is still read, for pacing), so a
 * full turn can run against the server with nobody speaking. Clips are
 * written by tools/clips.py. */
static _Atomic uint32_t s_selftest_hz = 0;
#define CLIP_MAGIC 0x4C435848u /* "HXCL" little-endian */
typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t count;
} clip_header_t;
typedef struct {
    uint32_t offset; /* from the partition start */
    uint32_t bytes;  /* PCM16 mono 24 kHz */
} clip_entry_t;
static const esp_partition_t *s_clips = NULL;
static const uint8_t *s_clips_map = NULL; /* mapped once: reads never stall the cache */
static esp_partition_mmap_handle_t s_clips_map_handle;
static _Atomic bool s_inject_armed = false;
static uint32_t s_inject_off = 0;   /* mic task only once armed */
static uint32_t s_inject_left = 0;
static int s_inject_clip = -1;

static _Atomic(hux_audio_mic_frame_fn) s_mic_sink = NULL;
static _Atomic bool s_mic_running = false;

/* Static capture scratch — zero heap on the hot path. BSS-resident,
 * single-writer (mic task). */
static uint8_t s_raw_buf[FRAME_RAW_BYTES];
static int16_t s_mono_buf[FRAME_SAMPLES];

static esp_err_t i2s_bring_up(void) {
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_PORT, I2S_ROLE_MASTER);
    /* If spk_task is ever late, the DMA sends zeros. The default replays
     * the last buffer it sent, a 10 ms piece of the reply heard again as
     * a stutter or a buzz. */
    chan_cfg.auto_clear_after_cb = true;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &s_i2s_tx, &s_i2s_rx),
                        TAG, "i2s.new_channel");

    /* Standard Philips I2S, 32-bit slots, stereo (ES7210 packs 4 mics
     * into the 2 × 32-bit slot pair via its own internal TDM-ish
     * muxing — the `esp_codec_dev` abstraction knows to read the
     * right count). Keeping the clock on a nice multiple of the
     * sample rate avoids needing APLL. */
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(MIC_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                       I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_MCLK,
            .bclk = I2S_GPIO_BCLK,
            .ws   = I2S_GPIO_LRCK,
            .dout = I2S_GPIO_DOUT,
            .din  = I2S_GPIO_DIN,
        },
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_tx, &std_cfg),
                        TAG, "i2s.init_tx");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_rx, &std_cfg),
                        TAG, "i2s.init_rx");

    /* Enable both TX and RX even though v0.2.1 only reads. In MASTER
     * role the controller drives MCLK/BCLK/LRCK from whichever
     * direction is active; empirically (and per Waveshare's BSP)
     * enabling just RX leaves ES7210 not locking — symptom is
     * `esp_codec_dev_read` returning ESP_FAIL. Idle TX pushes
     * silence out DOUT; harmless until v0.3 wires ES8311. */
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_i2s_tx), TAG, "i2s.enable_tx");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_i2s_rx), TAG, "i2s.enable_rx");
    return ESP_OK;
}

static esp_err_t es7210_bring_up(void) {
    audio_codec_i2s_cfg_t data_cfg = {
        .port = I2S_PORT,
        .rx_handle = s_i2s_rx,
        .tx_handle = NULL,
    };
    s_mic_data_if = audio_codec_new_i2s_data(&data_cfg);
    if (s_mic_data_if == NULL) {
        ESP_LOGE(TAG, "es7210.data_if_null");
        return ESP_FAIL;
    }

    audio_codec_i2c_cfg_t ctrl_cfg = {
        .addr = ES7210_CODEC_DEFAULT_ADDR,
        .bus_handle = hux_board_i2c_bus(),
    };
    s_mic_ctrl_if = audio_codec_new_i2c_ctrl(&ctrl_cfg);
    if (s_mic_ctrl_if == NULL) {
        ESP_LOGE(TAG, "es7210.ctrl_if_null");
        return ESP_FAIL;
    }

    /* All four mic channels enabled so the TDM frame shape matches
     * the Waveshare reference. We pick ch1 downstream; enabling only
     * one would change the slot layout and complicate extraction. */
    es7210_codec_cfg_t es7210_cfg = {
        .ctrl_if = s_mic_ctrl_if,
        .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2 | ES7210_SEL_MIC3 | ES7210_SEL_MIC4,
    };
    s_mic_codec_if = es7210_codec_new(&es7210_cfg);
    if (s_mic_codec_if == NULL) {
        ESP_LOGE(TAG, "es7210.codec_if_null");
        return ESP_FAIL;
    }

    esp_codec_dev_cfg_t dev_cfg = {
        .codec_if = s_mic_codec_if,
        .data_if = s_mic_data_if,
        .dev_type = ESP_CODEC_DEV_TYPE_IN,
    };
    s_mic_dev = esp_codec_dev_new(&dev_cfg);
    if (s_mic_dev == NULL) {
        ESP_LOGE(TAG, "es7210.dev_null");
        return ESP_FAIL;
    }

    /* Open at the wire-target rate. `channel = 2` means 2 × 32-bit
     * stereo slots per frame; ES7210 packs 4 × 16-bit mics into
     * those 64 bits. `bits_per_sample = 32` matches I2S slot width.
     * The decoded-into-int16_t buffer at the read site gives us the
     * 4-channel interleaved view. */
    esp_codec_dev_sample_info_t fs = {
        .sample_rate = MIC_SAMPLE_RATE_HZ,
        .channel = 2,
        .bits_per_sample = 32,
    };
    ESP_RETURN_ON_ERROR(esp_codec_dev_open(s_mic_dev, &fs), TAG, "es7210.open");

    /* Moderate gain — 30 dB is the Waveshare default. The ES7210 has
     * per-channel gain registers; we set all four identically to
     * keep the raw frame layout uniform. */
    for (int ch = 0; ch < 4; ch++) {
        esp_codec_dev_set_in_channel_gain(s_mic_dev,
                                          ESP_CODEC_DEV_MAKE_CHANNEL_MASK(ch),
                                          30.0f);
    }
    return ESP_OK;
}

static esp_err_t es8311_bring_up(void) {
    audio_codec_i2s_cfg_t data_cfg = {
        .port = I2S_PORT,
        .rx_handle = NULL,
        .tx_handle = s_i2s_tx,
    };
    s_spk_data_if = audio_codec_new_i2s_data(&data_cfg);
    if (s_spk_data_if == NULL) {
        ESP_LOGE(TAG, "es8311.data_if_null");
        return ESP_FAIL;
    }

    audio_codec_i2c_cfg_t ctrl_cfg = {
        .addr = ES8311_CODEC_DEFAULT_ADDR,
        .bus_handle = hux_board_i2c_bus(),
    };
    s_spk_ctrl_if = audio_codec_new_i2c_ctrl(&ctrl_cfg);
    if (s_spk_ctrl_if == NULL) {
        ESP_LOGE(TAG, "es8311.ctrl_if_null");
        return ESP_FAIL;
    }

    s_spk_gpio_if = audio_codec_new_gpio();

    /* `use_mclk=false` matches Waveshare's BSP: the codec clocks off
     * BCLK alone. `pa_pin=-1` opts out of codec-driven PA control —
     * we toggle TCA9555 P8 ourselves. */
    es8311_codec_cfg_t es8311_cfg = {
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
        .ctrl_if = s_spk_ctrl_if,
        .gpio_if = s_spk_gpio_if,
        .pa_pin = -1,
        .use_mclk = false,
        /* No "PA headroom" compensation: left at its defaults (a 5 V PA,
         * a 3.3 V DAC) esp_codec_dev adds 3.6 dB to every level, so 100
         * was +3.5 dB of digital gain and a full-scale reply clipped in
         * the DAC. Same rail = 0 dB: the volume curve is the register. */
        .hw_gain = {.pa_voltage = 3.3f, .codec_dac_voltage = 3.3f, .pa_gain = 0.0f},
    };
    s_spk_codec_if = es8311_codec_new(&es8311_cfg);
    if (s_spk_codec_if == NULL) {
        ESP_LOGE(TAG, "es8311.codec_if_null");
        return ESP_FAIL;
    }

    esp_codec_dev_cfg_t dev_cfg = {
        .codec_if = s_spk_codec_if,
        .data_if = s_spk_data_if,
        .dev_type = ESP_CODEC_DEV_TYPE_OUT,
    };
    s_spk_dev = esp_codec_dev_new(&dev_cfg);
    if (s_spk_dev == NULL) {
        ESP_LOGE(TAG, "es8311.dev_null");
        return ESP_FAIL;
    }

    /* Match the I2S format ES7210 was opened with — channel=2,
     * bits_per_sample=32. Opening ES8311 with a different format
     * reconfigures the shared I2S channel clocks and breaks the
     * mic (`esp_codec_dev_read` returns ESP_FAIL). spk_task
     * up-converts mono PCM16 from the ring to stereo PCM32 before
     * each write. */
    esp_codec_dev_sample_info_t fs = {
        .sample_rate = SPK_SAMPLE_RATE_HZ,
        .channel = 2,
        .bits_per_sample = 32,
    };
    ESP_RETURN_ON_ERROR(esp_codec_dev_open(s_spk_dev, &fs), TAG, "es8311.open");
    esp_codec_dev_vol_map_t points[] = {{0, -50.0f}, {50, -11.5f}, {100, 0.0f}};
    esp_codec_dev_vol_curve_t curve = {.vol_map = points, .count = 3};
    if (esp_codec_dev_set_vol_curve(s_spk_dev, &curve) != ESP_CODEC_DEV_OK) {
        ESP_LOGW(TAG, "es8311.vol_curve_failed (default curve: 50 is quieter)");
    }
    esp_codec_dev_set_out_vol(s_spk_dev, SPK_VOLUME_DEFAULT);
    return ESP_OK;
}

/* Consumer task: always running, always feeding I2S TX. On empty
 * ring it writes silence so DMA never underflows — underflow on
 * the ES8311 data path causes audible pops and occasionally wedges
 * the codec's internal state machine.
 *
 * Format conversion: the ring holds mono PCM16 (the wire format).
 * esp_codec_dev was opened at stereo PCM32 to match ES7210's I2S
 * setup. We expand each incoming sample to a stereo 32-bit pair
 * by `(sample << 16)` for both L and R — the codec reads the high
 * 16 bits as the audio data; the low 16 are zeros. 4x size growth.
 * Note this happens AFTER the ring drain, so the ring holds mono. */
static void spk_task(void *unused) {
    (void)unused;
    ESP_LOGI(TAG, "spk_task started chunk_ms=%d chunk_bytes=%d ring_bytes=%d",
             SPK_CHUNK_MS, SPK_CHUNK_BYTES, SPK_RING_BYTES);

    /* `mono16` holds one chunk pulled from the ring (the wire format).
     * `stereo32` holds the same chunk after upconversion — what
     * esp_codec_dev_write expects given our open params. Both are
     * BSS-resident; spk_task is the only writer. */
    static int16_t mono16[SPK_CHUNK_SAMPLES];
    static int32_t stereo32[SPK_CHUNK_SAMPLES * 2];

    /* Heartbeat counters. Iters tick every loop pass; silence/audio
     * count which branch was taken. If iters stops advancing while
     * the ring still has bytes (`ring_used > 0`) and `audio` is not
     * climbing, spk_task is wedged inside `esp_codec_dev_write`. If
     * iters keeps advancing but audio doesn't, the ring isn't being
     * fed (upstream WS / decode problem). One log line per second
     * (50 iters at 20 ms/chunk) is plenty for diagnosis without
     * flooding the console. */
    uint32_t iters = 0;
    size_t discard_left = 0; /* stale bytes a clear left in the ring */
    /* The tone segment playing now: its total and remaining samples. */
    tone_seg_t tone = {0};
    uint32_t tone_total = 0;
    uint32_t tone_left = 0;
    float tone_phase = 0.0f;
    uint32_t silence_chunks = 0;
    uint32_t audio_chunks = 0;
    /* Underruns: the ring ran dry mid-reply (audio, a short silence,
     * audio again): an audible dropout, usually the network falling
     * behind. A silence longer than SPK_GAP_MAX_CHUNKS is the reply's end. */
    uint32_t silent_run = 0;
    uint32_t underruns = 0;
    bool had_audio = false;
    bool rebuffering = false;
    uint32_t rebuffer_start_ms = 0;
    uint32_t write_errs = 0;

    for (;;) {
        /* Wait for reply audio up to a chunk, unless a tone is due: then
         * the DMA write below paces the loop, as it does for audio. */
        bool tone_due = tone_left > 0 || uxQueueMessagesWaiting(s_tone_q) > 0;
        size_t got_bytes = 0;
        if (atomic_load_explicit(&s_spk_hold, memory_order_acquire) != 0) {
            /* Barge-in hold: whatever is in the ring is the interrupted
             * reply, including a chunk the WS task was still writing when
             * the press cleared the ring (it waits for room and lands after
             * the clear). Never play during a hold. */
            discard_left = xStreamBufferBytesAvailable(s_spk_ring);
        }
        if (discard_left > 0) {
            /* Stale audio from a clear: read out (into stereo32, rewritten
             * below) up to SPK_DISCARD_PER_CHUNK per pass, silence meanwhile,
             * so a full 4 MB ring empties in ~0.6 s without starving the DMA. */
            size_t step = 0;
            size_t n = 1;
            while (discard_left > 0 && step < SPK_DISCARD_PER_CHUNK && n > 0) {
                size_t want = discard_left < sizeof(stereo32) ? discard_left : sizeof(stereo32);
                n = xStreamBufferReceive(s_spk_ring, stereo32, want, 0);
                discard_left -= n;
                step += n;
            }
            if (n == 0) {
                discard_left = 0; /* nothing left to read: the ring is empty */
            }
            if (discard_left == 0) {
                ESP_LOGI(TAG, "spk.discard done");
            }
        } else {
            if (rebuffering) {
                size_t avail = xStreamBufferBytesAvailable(s_spk_ring);
                bool active = atomic_load_explicit(&s_stream_active, memory_order_acquire);
                uint32_t waited = (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS) - rebuffer_start_ms;
                if (avail >= SPK_REBUFFER_BYTES || !active || waited >= SPK_REBUFFER_MAX_MS) {
                    rebuffering = false;
                    underruns++;
                    ESP_LOGW(TAG, "spk.underrun silent_ms=%u count=%u rebuffered ring_ms=%u why=%s",
                             (unsigned)waited, (unsigned)underruns, (unsigned)(avail / 48),
                             avail >= SPK_REBUFFER_BYTES ? "buffered" : !active ? "reply_end" : "max_wait");
                    silent_run = 0; /* counted here, not again when audio resumes */
                }
            }
            if (!rebuffering) {
                got_bytes = xStreamBufferReceive(s_spk_ring, mono16, sizeof(mono16),
                                                 tone_due ? 0 : pdMS_TO_TICKS(SPK_CHUNK_MS));
            }
        }
        uint32_t req = atomic_load_explicit(&s_spk_clear_req, memory_order_acquire);
        if (req != atomic_load_explicit(&s_spk_clear_done, memory_order_relaxed)) {
            /* A clear was asked for: this chunk and everything in the ring
             * now is stale. Count it and answer at once; the read-out happens
             * over the next passes (above). What is written after this point
             * queues behind it and plays. */
            size_t stale = xStreamBufferBytesAvailable(s_spk_ring);
            discard_left = stale;
            got_bytes = 0;
            unsigned tones = (unsigned)uxQueueMessagesWaiting(s_tone_q) + (tone_left > 0);
            xQueueReset(s_tone_q);
            tone_left = 0;
            had_audio = false; /* what plays next is a new reply, not a resumed one */
            rebuffering = false;
            atomic_store_explicit(&s_spk_clear_done, req, memory_order_release);
            ESP_LOGI(TAG, "spk.cleared dropped_bytes=%u tones=%u", (unsigned)stale, tones);
        }
        size_t got_samples = got_bytes / sizeof(int16_t);

        if (got_samples == 0 && tone_left == 0 && xQueueReceive(s_tone_q, &tone, 0) == pdTRUE) {
            tone_total = (uint32_t)SPK_SAMPLE_RATE_HZ / 1000 * tone.ms;
            tone_left = tone_total;
            tone_phase = 0.0f;
        }
        if (got_samples == 0 && tone_left > 0) {
            /* One chunk of the current segment (a sine, or a gap), faded in
             * and out; segments shorter than a chunk end mid-chunk. */
            float step = 2.0f * (float)M_PI * (float)tone.hz / SPK_SAMPLE_RATE_HZ;
            for (size_t i = 0; i < SPK_CHUNK_SAMPLES; i++) {
                int32_t v = 0;
                if (tone_left > 0) {
                    uint32_t pos = tone_total - tone_left;
                    float gain = 1.0f;
                    if (pos < TONE_FADE_SAMPLES) {
                        gain = (float)pos / TONE_FADE_SAMPLES;
                    } else if (tone_left < TONE_FADE_SAMPLES) {
                        gain = (float)tone_left / TONE_FADE_SAMPLES;
                    }
                    if (tone.hz != 0) {
                        v = (int32_t)(TONE_AMPLITUDE * gain * sinf(tone_phase)) * 65536;
                    }
                    tone_phase += step;
                    if (tone_phase > 2.0f * (float)M_PI) {
                        tone_phase -= 2.0f * (float)M_PI;
                    }
                    tone_left--;
                }
                stereo32[2 * i + 0] = v;
                stereo32[2 * i + 1] = v;
            }
            got_samples = SPK_CHUNK_SAMPLES;
            audio_chunks++;
        } else if (got_samples == 0) {
            /* Ring empty — write a full chunk of silence so the DMA
             * stays fed. esp_codec_dev_write blocks on DMA; this
             * paces the loop at ~20 ms. */
            memset(stereo32, 0, sizeof(stereo32));
            got_samples = SPK_CHUNK_SAMPLES;
            silence_chunks++;
            if (!rebuffering && had_audio && silent_run == 0 &&
                atomic_load_explicit(&s_stream_active, memory_order_acquire)) {
                /* Ran dry mid-reply: wait for a cushion before playing on. */
                rebuffering = true;
                rebuffer_start_ms = (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS);
            }
            silent_run++;
        } else {
            if (had_audio && silent_run > 0 && silent_run <= SPK_GAP_MAX_CHUNKS) {
                underruns++;
                ESP_LOGW(TAG, "spk.underrun silent_ms=%u count=%u", (unsigned)(silent_run * SPK_CHUNK_MS),
                         (unsigned)underruns);
            }
            had_audio = true;
            silent_run = 0;
            /* Mono PCM16 -> stereo PCM32. Pad the tail with silence
             * if we got a short read so the codec writes whole
             * frames. */
            for (size_t i = 0; i < got_samples; i++) {
                int32_t s = ((int32_t)mono16[i]) << 16;
                stereo32[2 * i + 0] = s; /* L */
                stereo32[2 * i + 1] = s; /* R */
            }
            for (size_t i = got_samples; i < SPK_CHUNK_SAMPLES; i++) {
                stereo32[2 * i + 0] = 0;
                stereo32[2 * i + 1] = 0;
            }
            got_samples = SPK_CHUNK_SAMPLES;
            audio_chunks++;
        }

        esp_err_t err = esp_codec_dev_write(
            s_spk_dev, stereo32, got_samples * 2 * sizeof(int32_t));
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "spk.write_failed err=%s", esp_err_to_name(err));
            write_errs++;
            /* Don't spin: throttle so a broken codec doesn't burn CPU. */
            vTaskDelay(pdMS_TO_TICKS(50));
        }

        /* Heartbeat every 5 sec (250 iters × 20 ms). Sampling iters
         * AFTER the write means a wedged write produces NO log line —
         * exactly what we want: a missing heartbeat is the signal.
         * Counters keep climbing so a long absence is recoverable
         * from the next line ("we missed N seconds, here's the new
         * audio_chunks delta"). */
        if ((++iters % 250) == 0) {
            size_t ring_used = xStreamBufferBytesAvailable(s_spk_ring);
            ESP_LOGI(TAG, "spk.alive iters=%u audio=%u silence=%u underruns=%u errs=%u ring_used=%u "
                          "push_wait_max_ms=%u",
                     (unsigned)iters, (unsigned)audio_chunks,
                     (unsigned)silence_chunks, (unsigned)underruns, (unsigned)write_errs,
                     (unsigned)ring_used, (unsigned)s_spk_push_max_wait_ms);
            s_spk_push_max_wait_ms = 0; /* a racy reset is fine: a stat */
        }
    }
}

static uint32_t uptime_ms(void) {
    return (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS);
}

void hux_audio_spk_push(const uint8_t *pcm, size_t len) {
    if (s_spk_ring == NULL || pcm == NULL || len == 0) {
        return;
    }
    uint32_t hold = atomic_load_explicit(&s_spk_hold, memory_order_acquire);
    if (hold != 0) {
        uint32_t held = uptime_ms() - (hold & ~1u);
        if (held < SPK_HOLD_MAX_MS) {
            s_spk_hold_dropped += len;
            return; /* the interrupted reply's tail */
        }
        /* Only this hold: a press since then set a new word and keeps it. */
        if (atomic_compare_exchange_strong(&s_spk_hold, &hold, 0)) {
            ESP_LOGW(TAG, "spk.hold expired after_ms=%u dropped_bytes=%u: no audio_clear came",
                     (unsigned)held, (unsigned)s_spk_hold_dropped);
            s_spk_hold_dropped = 0;
        }
    }
    /* Backpressure: a full ring makes the WS task wait for room instead
     * of dropping audio. While it waits it doesn't read the socket, TCP's
     * window closes and the server's send blocks, so the rest of a long
     * reply (or a stream) waits on the server, paced to playback. The
     * server sends far faster than real time (5-6x measured): dropping
     * the overflow lost 60 s of an 80 s story. The wait re-checks the
     * barge-in hold every SPK_PUSH_POLL_MS, so a press ends it at once;
     * a wedged speaker task can't stall the socket past SPK_PUSH_MAX_MS. */
    size_t off = 0;
    uint32_t waited_ms = 0;
    while (off < len) {
        if (atomic_load_explicit(&s_spk_hold, memory_order_acquire) != 0) {
            s_spk_hold_dropped += len - off;
            return;
        }
        size_t n = xStreamBufferSend(s_spk_ring, pcm + off, len - off,
                                     pdMS_TO_TICKS(SPK_PUSH_POLL_MS));
        off += n;
        if (n == 0) {
            waited_ms += SPK_PUSH_POLL_MS;
            if (waited_ms >= SPK_PUSH_MAX_MS) {
                ESP_LOGW(TAG, "spk.ring.stuck dropped=%u of=%u waited_ms=%u: is spk_task running?",
                         (unsigned)(len - off), (unsigned)len, (unsigned)waited_ms);
                return;
            }
        }
    }
    if (waited_ms > s_spk_push_max_wait_ms) {
        s_spk_push_max_wait_ms = waited_ms;
    }
}

void hux_audio_spk_clear(void) {
    if (s_spk_ring == NULL) {
        return;
    }
    uint32_t want = atomic_fetch_add_explicit(&s_spk_clear_req, 1, memory_order_acq_rel) + 1;
    TickType_t start = xTaskGetTickCount();
    while (atomic_load_explicit(&s_spk_clear_done, memory_order_acquire) - want > UINT32_MAX / 2) {
        /* done < want (wrap-safe): spk_task hasn't got to it yet. */
        if (xTaskGetTickCount() - start > pdMS_TO_TICKS(SPK_CLEAR_WAIT_MS)) {
            /* The request stays pending: if spk_task comes back it clears
             * then, which may drop the start of what plays next. */
            ESP_LOGW(TAG, "spk.clear_timeout req=%u: spk_task not running?", (unsigned)want);
            return;
        }
        vTaskDelay(1);
    }
}

void hux_audio_spk_hold(void) {
    atomic_store_explicit(&s_spk_hold, uptime_ms() | 1u, memory_order_release);
    ESP_LOGI(TAG, "spk.hold on: dropping the interrupted reply until audio_clear");
}

void hux_audio_spk_unhold(const char *why) {
    uint32_t hold = atomic_exchange_explicit(&s_spk_hold, 0, memory_order_acq_rel);
    if (hold != 0) {
        ESP_LOGI(TAG, "spk.hold off by=%s held_ms=%u", why,
                 (unsigned)(uptime_ms() - (hold & ~1u)));
    }
}

void hux_audio_on_audio_clear(void) {
    /* On the WS task, the ring's only writer: nothing is written while
     * this runs. Clear first, hold still on (spk_task plays nothing), then
     * end the hold: un-holding first would let spk_task play a stale chunk
     * in the moment before it sees the clear. */
    bool held = atomic_load_explicit(&s_spk_hold, memory_order_acquire) != 0;
    hux_audio_spk_clear();
    if (held) {
        ESP_LOGI(TAG, "spk.hold dropped_bytes=%u", (unsigned)s_spk_hold_dropped);
        s_spk_hold_dropped = 0;
        hux_audio_spk_unhold("audio_clear");
    }
}

void hux_audio_inject_cancel(void) {
    if (atomic_exchange_explicit(&s_inject_armed, false, memory_order_acq_rel)) {
        ESP_LOGI(TAG, "mic.inject cancelled clip=%d (no mic session took it)", s_inject_clip);
    }
}

void hux_audio_set_volume(int level) {
    if (s_spk_dev == NULL || level < 0 || level > 100) {
        return;
    }
    int applied = level < SPK_VOLUME_MIN ? SPK_VOLUME_MIN : level;
    esp_err_t err = esp_codec_dev_set_out_vol(s_spk_dev, applied);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "spk.volume_failed level=%d err=%s", applied, esp_err_to_name(err));
        return;
    }
    atomic_store_explicit(&s_volume, applied, memory_order_relaxed);
    /* esp_codec_dev doesn't report the codec's own I2C write, so this says
     * what was asked of it. */
    ESP_LOGI(TAG, "spk.volume level=%d requested=%d", applied, level);
}

int hux_audio_get_volume(void) {
    return atomic_load_explicit(&s_volume, memory_order_relaxed);
}

bool hux_audio_tone(int hz, int ms) {
    if (s_tone_q == NULL || (hz != 0 && (hz < 20 || hz > 12000)) || ms <= 0 || ms > 10000) {
        ESP_LOGW(TAG, "spk.tone rejected hz=%d ms=%d", hz, ms);
        return false;
    }
    tone_seg_t seg = {.hz = (uint16_t)hz, .ms = (uint16_t)ms};
    if (xQueueSend(s_tone_q, &seg, 0) != pdTRUE) {
        ESP_LOGW(TAG, "spk.tone queue_full hz=%d ms=%d", hz, ms);
        return false;
    }
    return true;
}

size_t hux_audio_spk_ring_used(void) {
    return s_spk_ring == NULL ? 0 : xStreamBufferBytesAvailable(s_spk_ring);
}

/* Goertzel power of `hz` in channel `ch` of one raw RMNM frame. */
static float goertzel(const int16_t *in, int ch, float hz) {
    float coeff = 2.0f * cosf(2.0f * (float)M_PI * hz / MIC_SAMPLE_RATE_HZ);
    float s1 = 0.0f, s2 = 0.0f;
    for (size_t i = 0; i < FRAME_SAMPLES; i++) {
        float s0 = (float)in[MIC_CHANNELS_TDM * i + ch] / 32768.0f + coeff * s1 - s2;
        s2 = s1;
        s1 = s0;
    }
    return s1 * s1 + s2 * s2 - coeff * s1 * s2;
}

/* Measure `frames` frames: per channel, the RMS and the level at `hz`
 * (dBFS of a sine's amplitude). */
static bool measure(int frames, float hz, float rms_db[4], float tone_db[4]) {
    double sq[4] = {0}, pw[4] = {0};
    for (int f = 0; f < frames; f++) {
        if (esp_codec_dev_read(s_mic_dev, s_raw_buf, sizeof(s_raw_buf)) != ESP_OK) {
            return false;
        }
        const int16_t *in = (const int16_t *)s_raw_buf;
        for (int ch = 0; ch < MIC_CHANNELS_TDM; ch++) {
            for (size_t i = 0; i < FRAME_SAMPLES; i++) {
                double v = in[MIC_CHANNELS_TDM * i + ch] / 32768.0;
                sq[ch] += v * v;
            }
            pw[ch] += goertzel(in, ch, hz);
        }
    }
    for (int ch = 0; ch < MIC_CHANNELS_TDM; ch++) {
        double rms = sqrt(sq[ch] / (frames * FRAME_SAMPLES));
        double amp = 2.0 * sqrt(pw[ch] / frames) / FRAME_SAMPLES;
        rms_db[ch] = (float)(20.0 * log10(rms + 1e-9));
        tone_db[ch] = (float)(20.0 * log10(amp + 1e-9));
    }
    return true;
}

static void run_selftest(float hz) {
    float rms0[4], tone0[4], rms1[4], tone1[4];
    /* Quiet first, then the tone; skip its first 200 ms (start-up and DMA
     * latency) and measure 300 ms of it. */
    bool ok = measure(10, hz, rms0, tone0);
    hux_audio_tone((int)hz, 700);
    if (ok) {
        float skip_r[4], skip_t[4];
        ok = measure(10, hz, skip_r, skip_t) && measure(15, hz, rms1, tone1);
    }
    if (!ok) {
        ESP_LOGW(TAG, "audio.selftest read_failed");
        return;
    }
    static const char *const NAMES[] = {"ref", "mic1", "noise", "mic2"};
    for (int ch = 0; ch < MIC_CHANNELS_TDM; ch++) {
        ESP_LOGI(TAG, "audio.selftest ch=%d(%s) quiet_rms=%.1f quiet_tone=%.1f tone_rms=%.1f "
                      "tone=%.1f dBFS rise=%.1f dB",
                 ch, NAMES[ch], rms0[ch], tone0[ch], rms1[ch], tone1[ch], tone1[ch] - tone0[ch]);
    }
    /* Pass: the tone stands well clear on the reference channel, and the
     * mic channels are alive (not a stuck-at-zero ADC). */
    float rise = tone1[0] - tone0[0];
    bool mic_alive = rms0[1] > -90.0f && rms0[3] > -90.0f;
    ESP_LOGI(TAG, "audio.selftest result=%s hz=%.0f ref_rise=%.1f dB ref_level=%.1f dBFS "
                  "volume=%d mic_alive=%d",
             rise > 20.0f && mic_alive ? "pass" : "FAIL", hz, rise, tone1[0],
             atomic_load_explicit(&s_volume, memory_order_relaxed), mic_alive);
}

static void inject_frame(void) {
    if (s_inject_left == 0) {
        memset(s_mono_buf, 0, sizeof(s_mono_buf));
        return;
    }
    uint32_t n = s_inject_left < sizeof(s_mono_buf) ? s_inject_left : sizeof(s_mono_buf);
    memcpy(s_mono_buf, s_clips_map + s_inject_off, n);
    memset((uint8_t *)s_mono_buf + n, 0, sizeof(s_mono_buf) - n);
    s_inject_off += n;
    s_inject_left -= n;
    if (s_inject_left == 0) {
        ESP_LOGI(TAG, "mic.inject done clip=%d", s_inject_clip);
    }
}

static void mic_task(void *unused) {
    (void)unused;
    ESP_LOGI(TAG, "mic_task started sr=%d frame_ms=%d samples=%d",
             MIC_SAMPLE_RATE_HZ, FRAME_MS, FRAME_SAMPLES);

    bool injecting = false;
    for (;;) {
        uint32_t st_hz = atomic_exchange_explicit(&s_selftest_hz, 0, memory_order_acq_rel);
        if (st_hz != 0) {
            if (atomic_load_explicit(&s_mic_running, memory_order_acquire)) {
                ESP_LOGW(TAG, "audio.selftest refused: the mic is in use");
            } else {
                run_selftest((float)st_hz);
            }
        }
        if (!atomic_load_explicit(&s_mic_running, memory_order_acquire)) {
            injecting = false;
            /* Idle — park for a frame period, not forever, so
             * start()/stop() transitions are responsive. */
            vTaskDelay(pdMS_TO_TICKS(FRAME_MS));
            continue;
        }

        esp_err_t err = esp_codec_dev_read(s_mic_dev, s_raw_buf, sizeof(s_raw_buf));
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "mic.read_failed err=%s", esp_err_to_name(err));
            vTaskDelay(pdMS_TO_TICKS(FRAME_MS));
            continue;
        }

        /* A clip armed before this press replaces the mic for the press. */
        if (!injecting && atomic_exchange_explicit(&s_inject_armed, false, memory_order_acq_rel)) {
            injecting = true;
            ESP_LOGI(TAG, "mic.inject start clip=%d bytes=%u", s_inject_clip,
                     (unsigned)s_inject_left);
        }
        if (injecting) {
            inject_frame();
        } else {
            /* RMNM: 4 x int16 per time instant. We want Mic1 at index 1. */
            const int16_t *in = (const int16_t *)s_raw_buf;
            for (size_t i = 0; i < FRAME_SAMPLES; i++) {
                s_mono_buf[i] = in[MIC_CHANNELS_TDM * i + 1];
            }
        }

        hux_audio_mic_frame_fn sink =
            atomic_load_explicit(&s_mic_sink, memory_order_acquire);
        if (sink != NULL) {
            sink(s_mono_buf, FRAME_SAMPLES);
        }
    }
}

/* Quiet: amp off until an RTC-clock deadline. RTC slow memory and the
 * RTC clock survive software restarts (an update's, a crash's), so a
 * quiet night stays quiet through them; a power cut clears both. */
#define QUIET_MAGIC 0x51554945 /* 'QUIE' */
RTC_NOINIT_ATTR static uint32_t s_quiet_magic;
RTC_NOINIT_ATTR static uint64_t s_quiet_until_us;
static esp_timer_handle_t s_quiet_timer;

static void quiet_over(void *arg) {
    (void)arg;
    s_quiet_magic = 0;
    bool ok = hux_board_tca_write(HUX_TCA_AUDIO_PA_EN, true);
    ESP_LOGW(TAG, "spk.quiet over: pa on ok=%d", ok);
}

static void quiet_arm(uint64_t us) {
    if (s_quiet_timer == NULL) {
        const esp_timer_create_args_t args = {.callback = quiet_over, .name = "spk_quiet"};
        if (esp_timer_create(&args, &s_quiet_timer) != ESP_OK) {
            return;
        }
    }
    esp_timer_stop(s_quiet_timer);
    esp_timer_start_once(s_quiet_timer, us);
}

uint32_t hux_audio_quiet_left_s(void) {
    uint64_t now = esp_rtc_get_time_us();
    if (s_quiet_magic != QUIET_MAGIC || now >= s_quiet_until_us) {
        return 0;
    }
    return (uint32_t)((s_quiet_until_us - now) / 1000000);
}

/* At boot: still inside a quiet period? Then the amp stays off and the
 * timer is armed for what's left. */
static bool quiet_resume(void) {
    uint32_t left = hux_audio_quiet_left_s();
    if (left == 0) {
        s_quiet_magic = 0;
        return false;
    }
    quiet_arm((uint64_t)left * 1000000);
    ESP_LOGW(TAG, "spk.quiet kept through the restart: pa off for %u s more", (unsigned)left);
    return true;
}

void hux_audio_quiet(uint32_t seconds) {
    if (seconds == 0) {
        if (s_quiet_timer != NULL) {
            esp_timer_stop(s_quiet_timer);
        }
        quiet_over(NULL);
        return;
    }
    s_quiet_until_us = esp_rtc_get_time_us() + (uint64_t)seconds * 1000000;
    s_quiet_magic = QUIET_MAGIC;
    bool ok = hux_board_tca_write(HUX_TCA_AUDIO_PA_EN, false);
    quiet_arm((uint64_t)seconds * 1000000);
    ESP_LOGW(TAG, "spk.quiet pa off for %u s (then on by itself) ok=%d", (unsigned)seconds, ok);
}

void hux_audio_init(void) {
    if (s_mic_dev != NULL) {
        return; /* Idempotent. */
    }

    if (i2s_bring_up() != ESP_OK) {
        ESP_LOGE(TAG, "i2s.bring_up_failed");
        return;
    }
    if (es7210_bring_up() != ESP_OK) {
        ESP_LOGE(TAG, "es7210.bring_up_failed");
        return;
    }

    BaseType_t ok = xTaskCreate(mic_task, "hux_mic", MIC_TASK_STACK,
                                NULL, MIC_TASK_PRIO, NULL);
    configASSERT(ok == pdPASS);

    /* Playback path — ES8311 + PSRAM ring + consumer task. */
    if (es8311_bring_up() != ESP_OK) {
        ESP_LOGE(TAG, "es8311.bring_up_failed (playback disabled)");
    } else {
        /* PSRAM-backed stream buffer. `xStreamBufferCreate` would use
         * the default allocator, which doesn't auto-route large
         * blocks to PSRAM and panics out of internal heap at 192 KB.
         * `WithCaps(MALLOC_CAP_SPIRAM)` is the IDF extension that
         * pins both control struct and storage in PSRAM. Trigger
         * level 1 so the consumer wakes on any byte. */
        s_spk_ring = xStreamBufferCreateWithCaps(SPK_RING_BYTES, 1,
                                                 MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        configASSERT(s_spk_ring != NULL);
        s_tone_q = xQueueCreate(TONE_QUEUE_DEPTH, sizeof(tone_seg_t));
        configASSERT(s_tone_q != NULL);

        /* PA on. Per the Waveshare factsheet: assert before the
         * first I2S frame is written, allow 10 ms settle, then the
         * first audio packet arrives without the start-pop.
         * spk_task starts writing silence immediately after, which
         * satisfies the "first I2S frame" part. */
        if (!quiet_resume()) {
            hux_board_tca_write(HUX_TCA_AUDIO_PA_EN, true);
        }
        vTaskDelay(pdMS_TO_TICKS(SPK_PA_SETTLE_MS));

        ok = xTaskCreate(spk_task, "hux_spk", SPK_TASK_STACK,
                         NULL, SPK_TASK_PRIO, NULL);
        configASSERT(ok == pdPASS);
        /* Boot self-test buzz removed in v0.3.2 (F-0011 closed) — the
         * speaker chain is verified by round-trip audio working. The
         * heartbeat in spk_task is the new "is the speaker alive"
         * signal. */
    }

    ESP_LOGI(TAG, "audio.init sr=%d mic=ES7210(RMNM:ch1) spk=ES8311 vol=%d",
             MIC_SAMPLE_RATE_HZ, SPK_VOLUME_DEFAULT);
}

void hux_audio_selftest(int hz) {
    if (hz < 100 || hz > 8000) {
        ESP_LOGW(TAG, "audio.selftest rejected hz=%d", hz);
        return;
    }
    atomic_store_explicit(&s_selftest_hz, (uint32_t)hz, memory_order_release);
}

int hux_audio_inject(int clip) {
    if (s_clips == NULL) {
        s_clips = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                           "storage");
    }
    if (s_clips != NULL && s_clips_map == NULL &&
        esp_partition_mmap(s_clips, 0, s_clips->size, ESP_PARTITION_MMAP_DATA,
                           (const void **)&s_clips_map, &s_clips_map_handle) != ESP_OK) {
        s_clips_map = NULL;
    }
    clip_header_t h;
    clip_entry_t e;
    if (s_clips_map == NULL) {
        ESP_LOGW(TAG, "mic.inject no storage partition");
        return -1;
    }
    memcpy(&h, s_clips_map, sizeof(h));
    if (h.magic != CLIP_MAGIC) {
        ESP_LOGW(TAG, "mic.inject no clips in the storage partition (tools/clips.py)");
        return -1;
    }
    if (clip >= 0 && clip < h.count) {
        memcpy(&e, s_clips_map + sizeof(h) + (size_t)clip * sizeof(e), sizeof(e));
    }
    if (clip < 0 || clip >= h.count || e.offset + e.bytes > s_clips->size) {
        ESP_LOGW(TAG, "mic.inject bad clip=%d count=%u", clip, (unsigned)h.count);
        return -1;
    }
    if (hux_audio_mic_is_running()) {
        ESP_LOGW(TAG, "mic.inject refused: the mic is in use");
        return -1;
    }
    s_inject_clip = clip;
    s_inject_off = e.offset;
    s_inject_left = e.bytes;
    atomic_store_explicit(&s_inject_armed, true, memory_order_release);
    int ms = (int)(e.bytes / (sizeof(int16_t) * MIC_SAMPLE_RATE_HZ / 1000));
    ESP_LOGI(TAG, "mic.inject armed clip=%d ms=%d", clip, ms);
    return ms;
}

void hux_audio_pa(bool on) {
    if (on && s_quiet_magic == QUIET_MAGIC) {
        if (s_quiet_timer != NULL) {
            esp_timer_stop(s_quiet_timer);
        }
        s_quiet_magic = 0; /* an explicit `pa on` ends the quiet */
    }
    bool ok = hux_board_tca_write(HUX_TCA_AUDIO_PA_EN, on);
    ESP_LOGI(TAG, "spk.pa %s ok=%d", on ? "on" : "off", ok);
}

int hux_audio_dac_volume_reg(void) {
    int v = 0;
    if (s_spk_ctrl_if == NULL ||
        s_spk_ctrl_if->read_reg(s_spk_ctrl_if, 0x32 /* ES8311 DAC volume */, 1, &v, 1) != 0) {
        return -1;
    }
    return v & 0xFF;
}

void hux_audio_set_mic_sink(hux_audio_mic_frame_fn sink) {
    atomic_store_explicit(&s_mic_sink, sink, memory_order_release);
}

void hux_audio_mic_start(void) {
    atomic_store_explicit(&s_mic_running, true, memory_order_release);
    ESP_LOGI(TAG, "mic.start");
}

void hux_audio_mic_stop(void) {
    atomic_store_explicit(&s_mic_running, false, memory_order_release);
    ESP_LOGI(TAG, "mic.stop");
}

bool hux_audio_mic_is_running(void) {
    return atomic_load_explicit(&s_mic_running, memory_order_acquire);
}

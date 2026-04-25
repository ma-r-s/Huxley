#include "hux_audio.h"

#include <stdatomic.h>
#include <string.h>

#include "driver/i2s_std.h"
#include "esp_attr.h"
#include "esp_check.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
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
 * Ring size: 32 KB ≈ 660 ms of 24 kHz PCM16 mono. Large enough to
 * ride out a momentary TCP / WS hiccup; small enough that a paused
 * stream doesn't produce audible "stored" audio when resumed. */
#define SPK_SAMPLE_RATE_HZ    24000
#define SPK_CHUNK_MS          20
#define SPK_CHUNK_SAMPLES     (SPK_SAMPLE_RATE_HZ * SPK_CHUNK_MS / 1000) /* 480 */
#define SPK_CHUNK_BYTES       (SPK_CHUNK_SAMPLES * sizeof(int16_t))      /* 960 */
#define SPK_RING_BYTES        (32 * 1024)
#define SPK_TASK_STACK        6144
#define SPK_TASK_PRIO         8
#define SPK_VOLUME_DEFAULT    70  /* 0..100, ES8311 output gain */
#define SPK_PA_SETTLE_MS      10  /* Waveshare factsheet recommended PA-on delay */

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

static _Atomic(hux_audio_mic_frame_fn) s_mic_sink = NULL;
static _Atomic bool s_mic_running = false;

/* Static capture scratch — zero heap on the hot path. BSS-resident,
 * single-writer (mic task). */
static uint8_t s_raw_buf[FRAME_RAW_BYTES];
static int16_t s_mono_buf[FRAME_SAMPLES];

static esp_err_t i2s_bring_up(void) {
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_PORT, I2S_ROLE_MASTER);
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
 * Note this happens AFTER the ring drain, so the ring stays at 32 KB
 * of mono (~660 ms). */
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

    for (;;) {
        size_t got_bytes = xStreamBufferReceive(
            s_spk_ring, mono16, sizeof(mono16), pdMS_TO_TICKS(SPK_CHUNK_MS));
        size_t got_samples = got_bytes / sizeof(int16_t);

        if (got_samples == 0) {
            /* Ring empty — write a full chunk of silence so the DMA
             * stays fed. esp_codec_dev_write blocks on DMA; this
             * paces the loop at ~20 ms. */
            memset(stereo32, 0, sizeof(stereo32));
            got_samples = SPK_CHUNK_SAMPLES;
        } else {
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
        }

        esp_err_t err = esp_codec_dev_write(
            s_spk_dev, stereo32, got_samples * 2 * sizeof(int32_t));
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "spk.write_failed err=%s", esp_err_to_name(err));
            /* Don't spin: throttle so a broken codec doesn't burn CPU. */
            vTaskDelay(pdMS_TO_TICKS(50));
        }
    }
}

void hux_audio_spk_push(const uint8_t *pcm, size_t len) {
    if (s_spk_ring == NULL || pcm == NULL || len == 0) {
        return;
    }
    /* Non-blocking. If the ring is full the consumer is falling
     * behind; we drop the overflow (newest) rather than stall the
     * WS RX task. Logged at WARN so we see it if it becomes a
     * pattern. */
    size_t sent = xStreamBufferSend(s_spk_ring, pcm, len, 0);
    if (sent < len) {
        ESP_LOGW(TAG, "spk.ring.overflow dropped=%u of=%u",
                 (unsigned)(len - sent), (unsigned)len);
    }
}

void hux_audio_spk_clear(void) {
    if (s_spk_ring != NULL) {
        xStreamBufferReset(s_spk_ring);
    }
}

static void mic_task(void *unused) {
    (void)unused;
    ESP_LOGI(TAG, "mic_task started sr=%d frame_ms=%d samples=%d",
             MIC_SAMPLE_RATE_HZ, FRAME_MS, FRAME_SAMPLES);

    for (;;) {
        if (!atomic_load_explicit(&s_mic_running, memory_order_acquire)) {
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

        /* RMNM: 4 × int16 per time instant. We want Mic1 at index 1. */
        const int16_t *in = (const int16_t *)s_raw_buf;
        for (size_t i = 0; i < FRAME_SAMPLES; i++) {
            s_mono_buf[i] = in[MIC_CHANNELS_TDM * i + 1];
        }

        hux_audio_mic_frame_fn sink =
            atomic_load_explicit(&s_mic_sink, memory_order_acquire);
        if (sink != NULL) {
            sink(s_mono_buf, FRAME_SAMPLES);
        }
    }
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
        /* 32 KB PSRAM-backed stream buffer. Trigger level 1 so the
         * consumer wakes as soon as any PCM arrives rather than
         * waiting for a larger batch. */
        s_spk_ring = xStreamBufferCreate(SPK_RING_BYTES, 1);
        configASSERT(s_spk_ring != NULL);

        /* PA on. Per the Waveshare factsheet: assert before the
         * first I2S frame is written, allow 10 ms settle, then the
         * first audio packet arrives without the start-pop.
         * spk_task starts writing silence immediately after, which
         * satisfies the "first I2S frame" part. */
        hux_board_tca_write(HUX_TCA_AUDIO_PA_EN, true);
        vTaskDelay(pdMS_TO_TICKS(SPK_PA_SETTLE_MS));

        ok = xTaskCreate(spk_task, "hux_spk", SPK_TASK_STACK,
                         NULL, SPK_TASK_PRIO, NULL);
        configASSERT(ok == pdPASS);

        /* Speaker self-test: push 250 ms of 1 kHz square wave into
         * the ring at boot. Mario hears a short buzz right after
         * READY → speaker / I2S / PA chain is healthy and any
         * subsequent silence is upstream (WS, server response).
         * Mario hears nothing → debug here.
         *
         * Square wave (not sine) avoids needing libm; also more
         * audible at low volume. ~50% amplitude so it's not
         * piercingly loud. Remove this block once round-trip audio
         * is working in v0.3 — see triage F-0011. */
        static int16_t self_test[6000]; /* 250 ms @ 24 kHz */
        const int period_samples = SPK_SAMPLE_RATE_HZ / 1000; /* 1 kHz */
        const int half = period_samples / 2;
        for (int i = 0; i < (int)(sizeof(self_test) / sizeof(self_test[0])); i++) {
            self_test[i] = ((i / half) & 1) ? +12000 : -12000;
        }
        hux_audio_spk_push((const uint8_t *)self_test, sizeof(self_test));
        ESP_LOGI(TAG, "spk.self_test pushed=%uB tone=1kHz dur=250ms",
                 (unsigned)sizeof(self_test));
    }

    ESP_LOGI(TAG, "audio.init sr=%d mic=ES7210(RMNM:ch1) spk=ES8311 vol=%d",
             MIC_SAMPLE_RATE_HZ, SPK_VOLUME_DEFAULT);
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
